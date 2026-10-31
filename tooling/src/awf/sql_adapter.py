"""PostgreSQL weather adapter: persistent canonical source, isolated practice schemas.

Adapted from the project's SQL prepare/missing-values/missing-records/
duplicates/value-errors notebooks.  Canonical writes are available ONLY through
explicit `sql-sync` using a separate maintenance connection.  Practice mutations
are restricted to the exact schema assigned to their practice number.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import random
from pathlib import Path

import pandas as pd

from .conditions import select_positions
from .sql_safety import practice_schema, require_practice_target
from . import weather

CANONICAL = 'canonical'
TABLE = 'weather_hourly'
COLUMNS = tuple(weather.COLUMNS)
MEASUREMENTS = COLUMNS[1:]


class SQLSetupError(ValueError):
    pass


def _driver():
    try:
        import psycopg
        from psycopg import sql
    except ImportError as exc:
        raise SQLSetupError('SQL requires psycopg: install tooling dependencies') from exc
    return psycopg, sql


def connection(*, maintenance: bool = False):
    variable = 'AWF_CANONICAL_DATABASE_URL' if maintenance else 'AWF_DATABASE_URL'
    dsn = os.environ.get(variable)
    if not dsn:
        raise SQLSetupError(f'Set {variable} to a PostgreSQL DSN (credentials are never written to practices)')
    psycopg, _ = _driver()
    try:
        return psycopg.connect(dsn, connect_timeout=5)
    except psycopg.Error as exc:
        raise SQLSetupError(f'PostgreSQL connection via {variable} failed: {exc.__class__.__name__}') from exc


def available() -> bool:
    """Check connection, canonical existence, and practice permissions (not date coverage).

    The full requested window is validated when the practice is built, not
    during every format-selection draw.
    """
    if not os.environ.get('AWF_DATABASE_URL'):
        return False
    try:
        with connection() as conn:
            ready=bool(conn.execute("SELECT to_regclass('canonical.weather_hourly') IS NOT NULL").fetchone()[0])
            permitted=bool(conn.execute("SELECT has_database_privilege(current_user, current_database(), 'CREATE') AND has_database_privilege(current_user, current_database(), 'TEMP')").fetchone()[0])
            readable=bool(conn.execute("SELECT has_schema_privilege(current_user, 'canonical', 'USAGE') AND has_table_privilege(current_user, 'canonical.weather_hourly', 'SELECT')").fetchone()[0])
            if not (ready and permitted and readable and not _canonical_writable(conn)):
                return False
            return True
    except Exception:
        return False


def _canonical_writable(conn):
    return bool(conn.execute("SELECT has_table_privilege(current_user, 'canonical.weather_hourly', 'INSERT,UPDATE,DELETE,TRUNCATE')").fetchone()[0])


def _require_safe_practice_role(conn):
    if _canonical_writable(conn):
        raise SQLSetupError('Unsafe SQL connection: practice role can modify canonical.weather_hourly. Use a separate restricted practice role.')


def table_identifier(schema: str, *, number: int | None = None):
    if number is None:
        raise SQLSetupError('Assigned practice number is required')
    require_practice_target(number, schema)
    _, sql = _driver()
    return sql.Identifier(schema, TABLE)


def _bounds(source):
    a,b = weather.bounds(source)
    return a.to_pydatetime(), b.to_pydatetime()


def _columns_sql():
    _, sql = _driver()
    return sql.SQL(', ').join(sql.Identifier(c) for c in COLUMNS)


def _canonical_identifier():
    _, sql = _driver()
    return sql.Identifier(CANONICAL, TABLE)


def _check_coverage(rows, source):
    expected = pd.date_range(*weather.bounds(source), freq='h', inclusive='left')
    actual = pd.DatetimeIndex(pd.to_datetime([r[0] for r in rows], utc=True))
    if not actual.equals(expected):
        raise SQLSetupError(f'Canonical weather coverage incomplete: expected {len(expected)}, found {len(actual)}; run `just sql-sync --start ... --end ...` first')


def require_coverage(source) -> None:
    """Read-only exact-window preflight; never changes canonical or practice state.

    The build transaction checks coverage again to protect against a change
    between this preflight and the actual practice construction.
    """
    with connection() as conn:
        _require_safe_practice_role(conn)
        _, sql = _driver()
        start, end = _bounds(source)
        # Transfer only timestamps for this read-only prerequisite check.
        rows = conn.execute(sql.SQL(
            'SELECT time FROM {} WHERE time >= %s AND time < %s ORDER BY time'
        ).format(_canonical_identifier()), (start, end)).fetchall()
        _check_coverage(rows, source)


def canonical_rows(conn, source):
    _, sql = _driver()
    a,b = _bounds(source)
    rows = conn.execute(sql.SQL('SELECT {} FROM {} WHERE time >= %s AND time < %s ORDER BY time').format(
        _columns_sql(), _canonical_identifier()), (a,b)).fetchall()
    _check_coverage(rows, source)
    return rows


def _document(rows, source):
    hourly = {'time': [int(pd.Timestamp(r[0]).timestamp()) for r in rows]}
    for j, name in enumerate(MEASUREMENTS, 1):
        hourly[name] = [None if r[j] is None else float(r[j]) for r in rows]
    units = {'time':'unixtime', 'wind_speed_10m':'km/h', 'wind_direction_10m':'°',
             'temperature_2m':'°C', 'cloud_cover':'%', 'relative_humidity_2m':'%',
             'shortwave_radiation':'W/m²'}
    return weather.validate({'latitude':source['latitude'], 'longitude':source['longitude'],
        'timezone':'GMT', 'utc_offset_seconds':0,
        'hourly_units':{c: units.get(c,'') for c in COLUMNS}, 'hourly':hourly}, source)


def source_snapshot(conn, source):
    return _document(canonical_rows(conn, source), source)


def _list_rows(conn, target):
    _, sql = _driver()
    # Sort by *every* field so duplicate timestamps have stable logical order.
    order = sql.SQL(', ').join(sql.SQL('{} NULLS FIRST').format(sql.Identifier(c)) for c in COLUMNS)
    return conn.execute(sql.SQL('SELECT {} FROM {} ORDER BY {}').format(
        _columns_sql(), target, order)).fetchall()


def _normalize(rows):
    result=[]
    for row in rows:
        result.append([v.isoformat() if isinstance(v, (dt.datetime,dt.date))
                       else v for v in row])
    return result


def logical_hash(rows):
    return hashlib.sha256(json.dumps(_normalize(rows), separators=(',',':'),
                  allow_nan=False, default=str).encode('utf-8')).hexdigest()


def _choose_keys(conn, target, *, source, cfg, seed, kind):
    _, sql = _driver()
    a,b = _bounds(source)
    target_col = cfg['target']
    if target_col not in MEASUREMENTS:
        raise SQLSetupError('Corruption target is not an allowed weather measurement')
    # Restrict to observations usable by that mutation, preserving archive eligibility.
    rows=conn.execute(sql.SQL('SELECT time, {} FROM {} WHERE time >= %s AND time < %s ORDER BY time').format(
        sql.Identifier(target_col),target),(a,b)).fetchall()
    if not rows or len(set(t for t,_ in rows))!=len(rows):
        raise SQLSetupError('Expected unique nonempty practice timestamps before corruption')
    if kind=='missing_values' and any(v is None for _,v in rows):
        raise SQLSetupError('Target already contains missing values')
    eligible=[(t,v) for t,v in rows if v is not None]
    if kind=='duplicates' and cfg.get('conflict_column') in MEASUREMENTS:
        # Conflicting duplicates need a measurement that can actually be changed.
        # Match the source-facing file adapter's eligibility restriction.
        conflict=conn.execute(sql.SQL('SELECT time, {} FROM {} WHERE time >= %s AND time < %s').format(
            sql.Identifier(cfg['conflict_column']),target),(a,b)).fetchall()
        if cfg.get('_variant') != 'exact':
            allowed={t for t,v in conflict if v is not None}
            eligible=[(t,v) for t,v in eligible if t in allowed]
    if kind=='invalid_values' and cfg.get('_variant') in ('scaled','mixed'):
        eligible=[(t,v) for t,v in eligible if v != 0]
    if kind=='invalid_values'  and any(not cfg['valid_min']<=v<=cfg['valid_max'] for _,v in eligible):
        raise SQLSetupError('Original target contains out-of-bounds measurements')
    positions, placement = select_positions(len(eligible), random.Random(seed),
         method=cfg['placement'],rate=(cfg['min_rate'],cfg['max_rate']),
         n_groups=cfg['groups'],grouped_share=cfg['grouped_share'])
    return [eligible[i] for i in positions],placement


def mutate(conn, *, schema, number, config, verify=False):
    """Apply exactly one supported SQL family natively inside caller transaction."""
    if conn.autocommit:
        raise SQLSetupError('SQL mutations require an explicit transaction')
    if verify:
        if schema != 'pg_temp':
            raise SQLSetupError('Only pg_temp may be used in verification')
        _,sql=_driver()
        target=sql.Identifier('pg_temp',f'awf_verify_{number}')
    else:
        target=table_identifier(schema,number=number)
    _,sql = _driver()
    resolved=config['resolved']; kind=resolved['dirty']; variant=resolved.get('variant','none')
    cfg=config['condition_settings']; source=config['source']
    if kind not in ('none','missing_values','missing_records','duplicates','invalid_values'):
        raise SQLSetupError(f'SQL condition {kind} is not supported')
    if kind == 'none':
        return {'kind':'none','variant':'none','affected_rows':0,'selected_positions':[]}
    selected, placement=_choose_keys(conn,target,source=source,cfg={**cfg,'_variant':variant},seed=int(resolved['seed']),kind=kind)
    times=[t for t,_ in selected]
    if kind=='missing_values':
        result=conn.execute(sql.SQL('UPDATE {} SET {} = NULL WHERE time = ANY(%s) AND {} IS NOT NULL RETURNING time').format(
            target,sql.Identifier(cfg['target']),sql.Identifier(cfg['target'])),(times,)).fetchall()
    elif kind=='missing_records':
        result=conn.execute(sql.SQL('DELETE FROM {} WHERE time = ANY(%s) RETURNING time').format(target),(times,)).fetchall()
    elif kind=='duplicates':
        if variant not in ('exact','conflicting','mixed'):
            raise SQLSetupError('Unrecognized duplicate variant')
        if cfg['conflict_column'] not in MEASUREMENTS:
            raise SQLSetupError('Duplicate conflict column invalid')
        if variant!='exact':
            original=conn.execute(sql.SQL('SELECT time, {} FROM {} WHERE time = ANY(%s)').format(
                sql.Identifier(cfg['conflict_column']),target),(times,)).fetchall()
            if any(v is None for _,v in original):
                raise SQLSetupError('Conflicting duplicates require non-null measurements')
        rng=random.Random(f'awf-sql-duplicate:{int(resolved["seed"]):06d}')
        nconf=0 if variant=='exact' else len(times) if variant=='conflicting' else max(1,min(len(times)-1,round(len(times)*cfg['conflict_share'])))
        if variant=='mixed' and len(times)<2:
            raise SQLSetupError('Mixed duplicates require at least two rows')
        conflicting=rng.sample(times,k=nconf)
        copies=[]
        for col in COLUMNS:
            identifier=sql.Identifier(col)
            if col==cfg['conflict_column'] and nconf:
                copies.append(sql.SQL('CASE WHEN time = ANY(%s) THEN {} + %s ELSE {} END').format(identifier,identifier))
            else:
                copies.append(identifier)
        query=sql.SQL('INSERT INTO {} ({}) SELECT {} FROM {} WHERE time = ANY(%s) RETURNING time').format(
            target,_columns_sql(),sql.SQL(', ').join(copies),target)
        params=((conflicting,cfg['conflict_offset']) if nconf else ())+(times,)
        result=conn.execute(query,params).fetchall()
    else:
        if variant not in ('out_of_bounds','scaled','mixed'):
            raise SQLSetupError('Unrecognized value-error variant')
        rng=random.Random(f'awf-sql-values:{int(resolved["seed"]):06d}')
        cnt=len(selected)
        n_bounds=cnt if variant=='out_of_bounds' else 0 if variant=='scaled' else max(1,min(cnt-1,round(cnt*cfg['bounds_share'])))
        if variant=='mixed' and cnt<2:
            raise SQLSetupError('Mixed value errors require two selected rows')
        bound_keys=set(rng.sample(times,k=n_bounds))
        changes=[]
        for t,v in selected:
            if t in bound_keys:
                value=(cfg['valid_max']+cfg['outside_margin'] if rng.choice((True,False))
                       else cfg['valid_min']-cfg['outside_margin'])
            else:
                value=v*cfg['scale_factor']
            changes.append((float(value),t))
        with conn.cursor() as cur:
            cur.executemany(sql.SQL('UPDATE {} SET {} = %s WHERE time = %s').format(
                target,sql.Identifier(cfg['target'])),changes)
        result=[(t,) for t in times]
    if len(result)!=len(times) or {r[0] for r in result}!=set(times):
        raise SQLSetupError('SQL mutation affected unexpected rows; transaction will roll back')
    return {'kind':kind,'variant':variant,'affected_rows':len(times),'placement':placement,
            'affected_timestamps':[t.isoformat() for t in times]}


def build(directory: Path):
    """Create an entirely new practice schema; never drop a pre-existing schema."""
    from .real import spec, SNAPSHOT
    config=spec(directory); number=config['id']; schema=practice_schema(number)
    _,sql=_driver(); source=config['source']
    with connection() as conn:
        _require_safe_practice_role(conn)
        if conn.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(schema,)).fetchone():
            raise SQLSetupError(f'{schema} already exists; refusing to overwrite any tables')
        doc=source_snapshot(conn,source)
        # Practice DDL + mutation in one transaction. Failures roll back both.
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        a,b=_bounds(source)
        conn.execute(sql.SQL('CREATE TABLE {} AS SELECT {} FROM {} WHERE time >= %s AND time < %s').format(
            sql.Identifier(schema,TABLE),_columns_sql(),_canonical_identifier()),(a,b))
        details=mutate(conn,schema=schema,number=number,config=config)
        rows=_list_rows(conn,sql.Identifier(schema,TABLE))
        out_hash=logical_hash(rows)
    snapshot=directory / SNAPSHOT
    weather.write_snapshot(doc, snapshot)
    return {'schema':schema,'rows':len(rows),'output_sha256':out_hash,'details':details}


def verify(config, snapshot: Path):
    """Rebuild in PostgreSQL TEMP table; always roll back, never mutate practice/canonical."""
    _,sql=_driver(); number=config['id']; source=config['source']; schema=practice_schema(number)
    if not snapshot.is_file():
        raise SQLSetupError('SQL verification requires the frozen clean snapshot')
    doc=weather.validate(json.loads(snapshot.read_text()),source)
    frame=weather.to_frame(doc)
    with connection() as conn:
        _require_safe_practice_role(conn)
        # The practice role needs TEMP privilege, but not canonical write privilege.
        temp=f'awf_verify_{number}'
        conn.execute(sql.SQL('CREATE TEMP TABLE {} (LIKE {} ) ON COMMIT DROP').format(
            sql.Identifier(temp),_canonical_identifier()))
        query=sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
            sql.Identifier('pg_temp',temp),_columns_sql(),sql.SQL(', ').join(sql.Placeholder() for _ in COLUMNS))
        data=[]
        for values in frame[list(COLUMNS)].itertuples(index=False,name=None):
            data.append(tuple(v.to_pydatetime() if isinstance(v,pd.Timestamp) else
                              None if pd.isna(v) else float(v) for v in values))
        with conn.cursor() as cur:
            cur.executemany(query,data)
        mutate(conn,schema='pg_temp',number=number,config=config,verify=True)
        candidate=logical_hash(_list_rows(conn,sql.Identifier('pg_temp',temp)))
        exists=conn.execute('SELECT to_regclass(%s)',(f'{schema}.{TABLE}',)).fetchone()[0]
        current=logical_hash(_list_rows(conn,sql.Identifier(schema,TABLE))) if exists else None
        conn.rollback()  # No persistent DDL or data changes; explicit, even on success.
    return candidate,current


def sync_canonical(source, *, fixture: Path | None = None):
    """Explicit maintenance operation; never called automatically by practice setup."""
    _,sql=_driver()
    with connection(maintenance=True) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(CANONICAL)))
        column_types=sql.SQL(', ').join([sql.SQL('time TIMESTAMPTZ PRIMARY KEY')] + [
            sql.SQL('{} DOUBLE PRECISION').format(sql.Identifier(c)) for c in MEASUREMENTS])
        conn.execute(sql.SQL('CREATE TABLE IF NOT EXISTS {} ({})').format(_canonical_identifier(),column_types))
        a,b=_bounds(source)
        existing=conn.execute(sql.SQL('SELECT time FROM {} WHERE time >= %s AND time < %s ORDER BY time').format(
            _canonical_identifier()),(a,b)).fetchall()
        desired=pd.date_range(*weather.bounds(source),freq='h',inclusive='left')
        observed=set(pd.to_datetime([r[0] for r in existing],utc=True))
        if len(existing)==len(desired) and all(t in observed for t in desired):
            return {'added':0,'expected':len(desired)}
    # No unbounded repeat fetch when source window already fully cached.
    document=weather.acquire(source,fixture=fixture)
    frame=weather.to_frame(document)
    with connection(maintenance=True) as conn:
        insert=sql.SQL('INSERT INTO {} ({}) VALUES ({}) ON CONFLICT (time) DO NOTHING').format(
            _canonical_identifier(),_columns_sql(),sql.SQL(', ').join(sql.Placeholder() for _ in COLUMNS))
        data=[tuple(v.to_pydatetime() if isinstance(v,pd.Timestamp) else None if pd.isna(v) else float(v)
                    for v in row) for row in frame[list(COLUMNS)].itertuples(index=False,name=None)]
        with conn.cursor() as cur:
            cur.executemany(insert,data)
            added=cur.rowcount
        _check_coverage(canonical_rows(conn,source),source)
    return {'added':added,'expected':len(frame)}


def prepare_script(directory: Path):
    """Explicit SQL prepare wrapper: create ONLY if its practice schema is absent.

    Never drop/reset a practice that already contains analyst work. The initial
    `setup` flow creates and corrupts in one transaction via build().
    """
    from .real import spec, SNAPSHOT
    from .reproducibility import sha256
    conf=spec(directory); number=conf['id']; schema=practice_schema(number)
    snap=directory/SNAPSHOT
    _,sql=_driver()
    with connection() as conn:
        _require_safe_practice_role(conn)
        if conn.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(schema,)).fetchone():
            raise SQLSetupError(f'{schema} already exists; refusing to reset existing analyst data. Use `just verify {number}` instead.')
        doc=source_snapshot(conn,conf['source'])
        expected=conf.get('reproduction',{}).get('source_sha256')
        encoded=json.dumps(doc,allow_nan=False,separators=(',',':')).encode('utf-8')
        if expected and hashlib.sha256(encoded).hexdigest()!=expected:
            raise SQLSetupError('Canonical source no longer matches recorded clean source; refusing rebuild')
        a,b=_bounds(conf['source'])
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        conn.execute(sql.SQL('CREATE TABLE {} AS SELECT {} FROM {} WHERE time >= %s AND time < %s').format(
            sql.Identifier(schema,TABLE),_columns_sql(),_canonical_identifier()),(a,b))
    snap.parent.mkdir(exist_ok=True)
    if not snap.exists(): weather.write_snapshot(doc, snap)
    return schema


def dirty_script(directory: Path):
    """Apply the saved defect ONLY to a pristine practice table; otherwise refuse.

    If the table already matches its recorded dirty fingerprint, this is a safe
    no-op. This prevents accidental reapplication or overwriting analyst work.
    """
    from .real import spec, SNAPSHOT
    conf=spec(directory); number=conf['id']; schema=practice_schema(number)
    expected=conf.get('reproduction',{}).get('output_sha256')
    _,sql=_driver()
    with connection() as conn:
        _require_safe_practice_role(conn)
        target=sql.Identifier(schema,TABLE)
        current=_list_rows(conn,target)
        if expected and logical_hash(current)==expected:
            return {'status':'already-reproduced','schema':schema}
        document=json.loads((directory/SNAPSHOT).read_text())
        frame=weather.to_frame(weather.validate(document,conf['source']))
        clean_rows=[]
        for values in frame[list(COLUMNS)].itertuples(index=False,name=None):
            clean_rows.append(tuple(v.to_pydatetime() if isinstance(v,pd.Timestamp) else
                       None if pd.isna(v) else float(v) for v in values))
        if logical_hash(current)!=logical_hash(sorted(clean_rows, key=lambda r:r[0])):
            raise SQLSetupError(f'{schema}.{TABLE} was modified; refusing to overwrite analyst data')
        details=mutate(conn,schema=schema,number=number,config=conf)
        new_hash=logical_hash(_list_rows(conn,target))
        if expected and new_hash!=expected:
            raise SQLSetupError('SQL reconstruction disagrees with original fingerprint; transaction rolled back')
    return details
