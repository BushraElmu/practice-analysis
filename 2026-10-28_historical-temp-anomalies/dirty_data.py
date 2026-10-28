"""JSON - Value Errors: portable corruption.

Copy this corruption config/function/call into one exercise-specific dirty_data.py.
Include shared helpers only once and load/save once.
"""

from pathlib import Path
from random import Random
import copy
import json

import pandas as pd

# Configuration
DATA_PATH = Path("data/weather.json")
TIME_COLUMN = "time"
TIMEZONE = "America/Edmonton"
START_DATE = "2025-01-01"
END_DATE = "2026-01-01"  # Exclusive, local calendar dates.
SEED = 651999

# Value Errors
ERROR_TARGET_COLUMN = "temperature_2m"
ERROR_KIND = "out_of_bounds"  # "out_of_bounds", "scaled", "mixed", "random".
ERROR_PLACEMENT = "scattered"
ERROR_RATE = (0.005, 0.01)  # Share of current eligible nonmissing/valid values changed.
ERROR_GROUPS = 3
ERROR_GROUPED_SHARE = 0.70
ERROR_VALID_MIN = -50
ERROR_VALID_MAX = 45
ERROR_SCALE_FACTOR = 1.5
ERROR_SCALE_OFFSET = 5

# Shared JSON helpers: include once in a combined dirty_data.py.
def eligible_positions(document, *, time_column, timezone, start_date, end_date):
    hourly = document.get("hourly")
    if not isinstance(hourly, dict) or time_column not in hourly:
        raise ValueError("Expected a source-shaped document with hourly arrays.")
    if not all(isinstance(v, list) for v in hourly.values()):
        raise ValueError("All hourly fields must contain arrays.")
    if len({len(v) for v in hourly.values()}) != 1:
        raise ValueError("Hourly arrays must be aligned before ordinary mutations.")
    times = hourly[time_column]
    if not times or any(isinstance(t, bool) or not isinstance(t, (int, float)) for t in times):
        raise ValueError("Expected nonempty numeric Unix-second timestamps.")
    if any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError("Expected unique increasing timestamps; apply duplicates last.")
    start = pd.Timestamp(start_date, tz=timezone).tz_convert("UTC").timestamp()
    end = pd.Timestamp(end_date, tz=timezone).tz_convert("UTC").timestamp()
    if start >= end:
        raise ValueError("Start date must precede end date.")
    positions = [i for i, t in enumerate(times) if start <= t < end]
    if not positions:
        raise ValueError("No observations in the selected interval.")
    return positions, start, end


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

def add_value_errors_json(
    document, rng, *, time_column, timezone, start_date, end_date,
    target_column, error_kind, placement, error_rate, n_groups,
    grouped_share, valid_min, valid_max, scale_factor, scale_offset,
):
    positions, _, _ = eligible_positions(
        document, time_column=time_column, timezone=timezone,
        start_date=start_date, end_date=end_date,
    )
    hourly = document["hourly"]
    if target_column not in hourly or target_column == time_column:
        raise ValueError("Target must be an existing measurement array.")
    values = hourly[target_column]
    # Other corruption functions may already have introduced nulls: leave them untouched.
    positions = [i for i in positions if values[i] is not None]
    if any(isinstance(values[i], bool) or not isinstance(values[i], (int, float)) for i in positions):
        raise ValueError("This numeric mutation requires numerical measurements.")
    if valid_min >= valid_max or scale_factor == 1 and scale_offset == 0:
        raise ValueError("Invalid bounds or identity scaling.")
    if error_kind not in ("out_of_bounds", "scaled", "mixed", "random"):
        raise ValueError("Unknown error kind.")
    kind = rng.choice(("out_of_bounds", "scaled", "mixed")) if error_kind == "random" else error_kind
    selected, _ = select_positions(
        len(positions), rng, method=placement, rate=error_rate,
        n_groups=n_groups, grouped_share=grouped_share,
    )
    if kind == "mixed" and len(selected) < 2:
        raise ValueError("Mixed errors need at least two affected values.")
    n_bounds = (len(selected) if kind == "out_of_bounds" else
                0 if kind == "scaled" else max(1, len(selected) // 2))
    bounds_positions = set(rng.sample(selected, k=n_bounds))
    dirty = copy.deepcopy(document)
    for i in selected:
        pos = positions[i]
        old = values[pos]
        if i in bounds_positions:
            new = valid_min - 50 if rng.random() < 0.5 else valid_max + 50
        else:
            new = old * scale_factor + scale_offset
        if new == old or not isinstance(new, (int, float)):
            raise ValueError("Mutation did not create a different numeric value.")
        dirty["hourly"][target_column][pos] = new
    return dirty

if __name__ == "__main__":
    with DATA_PATH.open("r", encoding="utf-8") as f:
        document = json.load(f)
    rng = Random(SEED)
    document = add_value_errors_json(
        document, rng, time_column=TIME_COLUMN, timezone=TIMEZONE,
        start_date=START_DATE, end_date=END_DATE,
        target_column=ERROR_TARGET_COLUMN, error_kind=ERROR_KIND, placement=ERROR_PLACEMENT,
        error_rate=ERROR_RATE, n_groups=ERROR_GROUPS, grouped_share=ERROR_GROUPED_SHARE,
        valid_min=ERROR_VALID_MIN, valid_max=ERROR_VALID_MAX,
        scale_factor=ERROR_SCALE_FACTOR, scale_offset=ERROR_SCALE_OFFSET,
    )
    # Serialize before overwriting, so invalid JSON numbers cannot be written.
    serialized = json.dumps(document, allow_nan=False)
    DATA_PATH.write_text(serialized, encoding="utf-8")
