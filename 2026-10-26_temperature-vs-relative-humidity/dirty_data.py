"""Introduce seeded missing values.

Copy only this corruption section, its function, and its main call into a combined dirty_data.py.
Start with a clean data/weather.csv; restore it before another run.
"""

from pathlib import Path
from random import Random
import pandas as pd

# Configuration
DATA_PATH = Path("data/weather.csv")
TIME_COLUMN = "time"
TIMEZONE = "America/Edmonton"
START_DATE = "2026-08-01"
END_DATE = "2026-09-01"  # Exclusive; local calendar dates.
SEED = 326723

# Missing Values
MISSING_TARGET_COLUMN = "relative_humidity_2m"
MISSING_METHOD = "mixed"  # "scattered", "grouped", "mixed", "random".
MISSING_RATE = (0.03, 0.08)  # Share of current eligible nonmissing values changed.
MISSING_GROUPS = 3
MISSING_GROUPED_SHARE = 0.70

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

def add_missing_values(
    df, rng, *, time_column, timezone, start_date, end_date,
    target_column, method, missing_rate, n_groups, grouped_share,
):
    # Select eligible records in chronological order (not necessarily hourly).
    if time_column not in df or target_column not in df:
        raise ValueError("Time or target column not found.")

    dirty = df.reset_index(drop=True).copy()
    time = pd.to_datetime(dirty[time_column], utc=True)
    if time.isna().any():
        raise ValueError("Timestamps cannot be missing.")
    start = pd.Timestamp(start_date, tz=timezone).tz_convert("UTC")
    end = pd.Timestamp(end_date, tz=timezone).tz_convert("UTC")
    if start >= end:
        raise ValueError("Start date must precede end date.")

    scope = time.ge(start) & time.lt(end)
    eligible = time.loc[scope].sort_values(kind="stable")
    if eligible.empty:
        raise ValueError("No eligible observations.")
    if dirty.loc[scope, target_column].isna().any():
        raise ValueError("Target already contains missing values in this interval. Restore clean data first.")

    # Rate refers to currently eligible records in this configured interval.
    selected, _ = select_positions(
        len(eligible), rng, method=method, rate=missing_rate,
        n_groups=n_groups, grouped_share=grouped_share,
    )
    dirty.loc[eligible.index[selected], target_column] = pd.NA
    return dirty

if __name__ == "__main__":
    df = pd.read_csv(DATA_PATH)
    rng = Random(SEED)
    df = add_missing_values(
        df, rng,
        time_column=TIME_COLUMN, timezone=TIMEZONE,
        start_date=START_DATE, end_date=END_DATE,
        target_column=MISSING_TARGET_COLUMN, method=MISSING_METHOD,
        missing_rate=MISSING_RATE, n_groups=MISSING_GROUPS,
        grouped_share=MISSING_GROUPED_SHARE,
    )
    df.to_csv(DATA_PATH, index=False)
