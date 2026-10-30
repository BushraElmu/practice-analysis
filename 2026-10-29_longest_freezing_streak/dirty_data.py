"""SQL - Missing Records: portable PostgreSQL corruption.

Copy Missing Records configuration, remove_records_sql(), shared helpers (once), and its main call into your exercise-specific script.
The main script owns the transaction; always restore a clean practice table before rerunning.
"""

from random import Random
import re
import pandas as pd
import psycopg
from psycopg import sql

# Configuration
DATABASE_NAME = "analytical_workflow_fluency"
PRACTICE_SCHEMA = "practice_017"  # Match prepare_data.py
PRACTICE_TABLE = "weather_hourly"
TIME_COLUMN = "time"
KEY_COLUMN = "time"  # Unique identifier; not necessarily the time column in business data.
TIMEZONE = "America/Edmonton"
START_DATE = "2025-01-01"
END_DATE = "2025-04-01"  # Exclusive local calendar date.
SEED = 283417

# Missing Records
RECORD_METHOD = "mixed"  # "scattered", "grouped", "mixed", "random"
RECORD_RATE = (0.04, 0.04)  # Share of current eligible records removed.
RECORD_GROUPS = 2
RECORD_GROUPED_SHARE = 0.90
EXPECTED_FREQUENCY = "h"  # Weather: one hourly record. For irregular orders: None.

def sql_date_bounds(timezone, start_date, end_date):
    start = pd.Timestamp(start_date, tz=timezone).to_pydatetime()
    end = pd.Timestamp(end_date, tz=timezone).to_pydatetime()
    if start >= end:
        raise ValueError("Start date must precede end date.")
    return start, end


def check_practice_table(conn, schema):
    if not re.fullmatch(r"practice_[0-9]+", schema):
        raise ValueError("Only practice_N schemas can be deliberately corrupted.")
    if conn.autocommit:
        raise ValueError("Use a transaction (autocommit must be off).")


def select_positions(n, rng, *, method, rate, n_groups, grouped_share):
    """Select a fraction of the current eligible records in ordered positions.

    Grouped blocks have at least two consecutive eligible records; mixed
    placement reserves at least one independently scattered selection.
    """
    if n < 1:
        raise ValueError("No eligible records.")
    if method not in ("scattered", "grouped", "mixed", "random"):
        raise ValueError("Unknown placement method.")
    if not 0 < rate[0] <= rate[1] < 1:
        raise ValueError("Rate must be between 0 and 1.")
    if n_groups < 1 or not 0 < grouped_share < 1:
        raise ValueError("Invalid group count or grouped share.")

    chosen = rng.choice(("scattered", "grouped", "mixed")) if method == "random" else method
    count = max(1, round(n * rng.uniform(*rate)))
    if chosen == "mixed" and count < 3:
        raise ValueError("Mixed placement needs two grouped and one scattered record.")
    grouped_count = (count if chosen == "grouped" else
                     max(2, min(count - 1, round(count * grouped_share))) if chosen == "mixed" else 0)
    selected = set()
    if grouped_count:
        if grouped_count < 2:
            raise ValueError("Grouped placement requires at least two affected records.")
        groups = min(n_groups, grouped_count // 2)
        if n - grouped_count < groups - 1:
            raise ValueError("Insufficient eligible records for separate groups.")
        length, extra = divmod(grouped_count, groups)
        lengths = [length + (i < extra) for i in range(groups)]
        rng.shuffle(lengths)
        slack = n - grouped_count - (groups - 1)
        dividers = sorted(rng.sample(range(slack + groups), groups))
        gaps = ([dividers[0]] +
                [dividers[i] - dividers[i - 1] - 1 for i in range(1, groups)] +
                [slack + groups - 1 - dividers[-1]])
        cursor = gaps[0]
        for i, block_length in enumerate(lengths):
            selected.update(range(cursor, cursor + block_length))
            if i < groups - 1:
                cursor += block_length + 1 + gaps[i + 1]

    scattered_count = count - grouped_count
    if scattered_count:
        remaining = [i for i in range(n) if i not in selected]
        selected.update(rng.sample(remaining, k=scattered_count))
    if len(selected) != count:
        raise AssertionError("Selected count differs from the intended count.")
    return sorted(selected), chosen


def remove_records_sql(
    conn, rng, *, schema, table, time_column, key_column, timezone,
    start_date, end_date, method, record_rate, n_groups, grouped_share,
    expected_frequency=None,
):
    check_practice_table(conn, schema)
    start, end = sql_date_bounds(timezone, start_date, end_date)
    source = sql.Identifier(schema, table)
    key_id = sql.Identifier(key_column)
    time_id = sql.Identifier(time_column)
    rows = conn.execute(
        sql.SQL("""
            SELECT {key}, {time} FROM {source}
            WHERE {time} >= %s AND {time} < %s
            ORDER BY {time}, {key}
        """).format(key=key_id, time=time_id, source=source),
        (start, end),
    ).fetchall()
    keys = [key for key, _ in rows]
    if not keys or any(key is None for key in keys) or len(set(keys)) != len(keys):
        raise ValueError("Expected non-NULL unique keys before record removal.")

    if expected_frequency is not None:
        # Appropriate to scheduled snapshots/hourly data; NOT arbitrary transaction logs.
        actual = pd.DatetimeIndex([time for _, time in rows]).tz_convert("UTC")
        expected = pd.date_range(start, end, freq=expected_frequency, inclusive="left").tz_convert("UTC")
        if not actual.equals(expected):
            raise ValueError("Expected scheduled records are already absent, repeated, or misaligned.")

    positions, chosen_method = select_positions(
        len(keys), rng, method=method, rate=record_rate,
        n_groups=n_groups, grouped_share=grouped_share,
    )
    selected_keys = [keys[i] for i in positions]
    removed = conn.execute(
        sql.SQL("""
            DELETE FROM {source}
            WHERE {key} = ANY(%s) AND {time} >= %s AND {time} < %s
            RETURNING {key}
        """).format(source=source, key=key_id, time=time_id),
        (selected_keys, start, end),
    ).fetchall()
    if len(removed) != len(selected_keys) or {row[0] for row in removed} != set(selected_keys):
        raise ValueError("Deleted keys do not match selection; roll back transaction.")
    return len(removed), chosen_method


if __name__ == "__main__":
    with psycopg.connect(dbname=DATABASE_NAME) as conn:
        rng = Random(SEED)
        removed, placement = remove_records_sql(
            conn, rng, schema=PRACTICE_SCHEMA, table=PRACTICE_TABLE,
            time_column=TIME_COLUMN, key_column=KEY_COLUMN, timezone=TIMEZONE,
            start_date=START_DATE, end_date=END_DATE,
            method=RECORD_METHOD, record_rate=RECORD_RATE,
            n_groups=RECORD_GROUPS, grouped_share=RECORD_GROUPED_SHARE,
            expected_frequency=EXPECTED_FREQUENCY,
        )
