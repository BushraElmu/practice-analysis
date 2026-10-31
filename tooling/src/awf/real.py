"""Prepare and rebuild file-backed weather practices; dispatch SQL operations."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

from . import weather
from .conditions import apply
from .reproducibility import sha256

SNAPSHOT = Path('data/.awf-source.json')


def spec(directory: Path) -> dict:
    from .blind import full_record_path
    hidden = full_record_path(directory)
    with (hidden if hidden.is_file() else directory / 'practice.toml').open('rb') as handle:
        return tomllib.load(handle)


def output_path(directory: Path, fmt: str) -> Path:
    if fmt not in ('csv','json','parquet'):
        raise ValueError(f'Unsupported physical format {fmt}')
    return directory / 'data' / f'weather.{fmt}'


def _require_original_snapshot(directory: Path, manifest: dict) -> None:
    """Never regenerate from a changed/missing frozen source, even with --force.

    --force permits replacing analyst-facing output; it does not authorize
    silently changing what was originally generated as the source.
    """
    expected = manifest.get('reproduction', {}).get('source_sha256')
    if expected:
        snapshot = directory / SNAPSHOT
        if not snapshot.is_file() or sha256(snapshot) != expected:
            raise ValueError('Frozen clean source snapshot is missing or changed; '
                             'refusing regeneration even with --force')


def prepare(directory: Path, fixture: Path | None = None, *, force: bool = False) -> Path:
    """Build clean output from saved source snapshot, fetching only if absent."""
    config = spec(directory)
    source, fmt = config['source'], config['resolved']['format']
    if fmt == 'sql':
        from . import sql_adapter
        return sql_adapter.prepare_script(directory)
    snapshot = directory / SNAPSHOT
    _require_original_snapshot(directory, config)
    if snapshot.exists():
        document = weather.validate(json.loads(snapshot.read_text()), source)
    else:
        document = weather.acquire(source, fixture=fixture)
        weather.write_snapshot(document, snapshot)
    dest = output_path(directory, fmt)
    _check_overwrite(directory, dest, force=force, operation="prepare")
    weather.write(document, fmt, dest)
    return dest


def dirty(directory: Path, *, force: bool = False) -> dict:
    """Idempotently build the selected dirty output from the clean snapshot."""
    config = spec(directory)
    source, resolved = config['source'], config['resolved']
    fmt, kind = resolved['format'], resolved['dirty']
    if fmt == 'sql':
        from . import sql_adapter
        return sql_adapter.dirty_script(directory)
    snapshot = directory / SNAPSHOT
    if not snapshot.is_file():
        raise ValueError('Missing clean source snapshot; run prepare_data.py first')
    _require_original_snapshot(directory, config)
    dest = output_path(directory, fmt)
    _check_overwrite(directory, dest, force=force, operation="dirty")
    document = weather.validate(json.loads(snapshot.read_text()), source)
    value = document if fmt == 'json' else weather.to_frame(document)
    cfg = config['condition_settings']
    changed, details = apply(value, fmt=fmt, kind=kind,
        seed=int(resolved['seed']), source=source, target=cfg['target'],
        rate=(cfg['min_rate'], cfg['max_rate']), placement=cfg['placement'],
        n_groups=cfg['groups'], grouped_share=cfg['grouped_share'],
        variant=resolved.get('variant', 'none'), parameters=cfg)
    weather.write_data(changed, fmt, dest)
    return details



def _check_overwrite(directory: Path, path: Path, *, force: bool, operation: str) -> None:
    """Respect analyst edits; explicit --force is required to reset a changed file."""
    if not path.exists() or force:
        return
    manifest = spec(directory)
    expected = manifest.get('reproduction', {}).get('output_sha256')
    if operation == 'dirty' and expected and sha256(path) == expected:
        return  # An unchanged originally-generated dataset may be rebuilt idempotently.
    if operation == 'dirty' and not expected:
        return  # Initial setup: a clean file exists, no frozen dirty output yet.
    raise ValueError(f'{path.name} already exists and may contain analyst edits; '
                     f'use {operation}_data.py --force to replace it deliberately')
