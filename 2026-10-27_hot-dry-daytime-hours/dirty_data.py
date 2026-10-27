"""Portable Parquet corruption. Use clean input before each run.

In a combined dirty_data.py include the relevant corruption config/function/call.
Copy the shared selection helpers only once. Load and save Parquet once.
Schema mismatch runs last. Validation against authoritative sources occurs upstream.
"""

from pathlib import Path
from random import Random

import pandas as pd
import pyarrow.parquet as pq

# Configuration
DATA_PATH = Path("data/weather.parquet")
TIME_COLUMN = "time"
TIMEZONE = "America/Edmonton"
START_DATE = "2026-06-01"
END_DATE = "2026-09-01"  # Exclusive; local dates.
SEED = 614007

# Duplicates
KEY_COLUMNS = ["time"]
DUPLICATE_KIND = "mixed"  # exact, conflicting, mixed, random
DUPLICATE_PLACEMENT = "grouped"  # scattered, grouped, mixed, random
DUPLICATE_RATE = (0.02, 0.04)  # Share of eligible original rows copied once.
DUPLICATE_GROUPS = 3
DUPLICATE_GROUPED_SHARE = 0.70
DUPLICATE_CONFLICT_COLUMN = "temperature_2m"
DUPLICATE_CONFLICT_OFFSET = 2.0
DUPLICATE_CONFLICT_SHARE = 0.50

# Shared selection: include once in an assembled dirty_data.py.
def scoped_rows(df, *, time_column, timezone, start_date, end_date):
    if time_column not in df:
        raise ValueError("Time column not found.")
    times = pd.to_datetime(df[time_column], utc=True, errors="raise")
    if times.isna().any():
        raise ValueError("Time column contains missing timestamps.")
    start = pd.Timestamp(start_date, tz=timezone).tz_convert("UTC")
    end = pd.Timestamp(end_date, tz=timezone).tz_convert("UTC")
    if start >= end:
        raise ValueError("Start date must precede end date.")
    scoped = df.loc[times.ge(start) & times.lt(end)]
    if scoped.empty:
        raise ValueError("No records in the selected interval.")
    # Indices refer to the input DataFrame. No complete-hourly assumption.
    return scoped.sort_values(time_column, kind="stable").index.tolist(), start, end

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

def add_duplicates(
    df, rng, *, time_column, timezone, start_date, end_date,
    key_columns, duplicate_kind, placement, duplicate_rate,
    n_groups, grouped_share, conflict_column, conflict_offset, conflict_share,
):
    if not key_columns or any(c not in df for c in key_columns):
        raise ValueError("Expected existing key columns.")
    clean = df.reset_index(drop=True).copy()
    if clean[key_columns].isna().any().any() or clean.duplicated(subset=key_columns).any():
        raise ValueError("Expected unique, nonmissing record keys.")
    eligible, _, _ = scoped_rows(
        clean, time_column=time_column, timezone=timezone,
        start_date=start_date, end_date=end_date,
    )
    if duplicate_kind not in ("exact", "conflicting", "mixed", "random"):
        raise ValueError("Unknown duplicate kind.")
    if not 0 < conflict_share < 1:
        raise ValueError("Invalid conflict share.")
    kind = (rng.choice(("exact", "conflicting", "mixed"))
            if duplicate_kind == "random" else duplicate_kind)
    if kind != "exact":
        if (conflict_column not in clean or conflict_column in key_columns
                or conflict_column == time_column or conflict_offset == 0):
            raise ValueError("Provide a non-key numeric conflict column and nonzero offset.")
        if not pd.api.types.is_numeric_dtype(clean[conflict_column]):
            raise ValueError("Conflicting copies require a numeric column.")
        if pd.api.types.is_integer_dtype(clean[conflict_column]) and not float(conflict_offset).is_integer():
            raise ValueError("Use an integer conflict offset to preserve Parquet integer type.")
        eligible = [i for i in eligible if pd.notna(clean.at[i, conflict_column])]
    selected, _ = select_positions(
        len(eligible), rng, method=placement, rate=duplicate_rate,
        n_groups=n_groups, grouped_share=grouped_share,
    )
    copies = clean.loc[[eligible[i] for i in selected]].copy().reset_index(drop=True)
    if kind == "mixed" and len(copies) < 2:
        raise ValueError("Mixed duplicates need at least two copies.")
    conflicts = (0 if kind == "exact" else len(copies) if kind == "conflicting" else
                 max(1, min(len(copies) - 1, round(len(copies) * conflict_share))))
    if conflicts:
        for i in rng.sample(range(len(copies)), k=conflicts):
            copies.at[i, conflict_column] = copies.at[i, conflict_column] + conflict_offset
    # Sort original records before their copies, preserving timestamp order.
    result = pd.concat([clean, copies], ignore_index=True)
    sort_columns = [time_column, *[c for c in key_columns if c != time_column]]
    result = result.sort_values(sort_columns, kind="stable").reset_index(drop=True)
    if result.duplicated(subset=key_columns).sum() != len(copies):
        raise AssertionError("Unexpected repeated-key count.")
    return result


if __name__ == "__main__":
    df = pd.read_parquet(DATA_PATH)
    rng = Random(SEED)
    df = add_duplicates(
        df, rng, time_column=TIME_COLUMN, timezone=TIMEZONE,
        start_date=START_DATE, end_date=END_DATE,
        key_columns=KEY_COLUMNS, duplicate_kind=DUPLICATE_KIND,
        placement=DUPLICATE_PLACEMENT, duplicate_rate=DUPLICATE_RATE,
        n_groups=DUPLICATE_GROUPS, grouped_share=DUPLICATE_GROUPED_SHARE,
        conflict_column=DUPLICATE_CONFLICT_COLUMN, conflict_offset=DUPLICATE_CONFLICT_OFFSET,
        conflict_share=DUPLICATE_CONFLICT_SHARE,
    )
    df.to_parquet(DATA_PATH, index=False)
