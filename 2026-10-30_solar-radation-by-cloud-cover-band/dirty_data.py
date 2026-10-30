"""Introduce seeded inconsistent value representations in CSV.

Copy the representation settings, add_representation_errors(), and its main call
into a combined dirty_data.py. Run representation last if numeric transforms precede it.
Start from a clean CSV; reset before rerunning.
"""

from pathlib import Path
from random import Random
import pandas as pd

# Configuration
DATA_PATH = Path("data/weather.csv")
TIME_COLUMN = "time"
TIMEZONE = "America/Edmonton"
START_DATE = "2025-06-01"
END_DATE = "2025-07-01"  # Exclusive; local calendar dates.
SEED = 814205

# Representation
REPRESENTATION_TARGET_COLUMN = "cloud_cover"
REPRESENTATION_KIND = "numeric_suffix"  # numeric: "numeric_suffix", "sentinel"; text: "whitespace", "case", "sentinel"; both: "mixed", "random".
REPRESENTATION_PLACEMENT = "mixed"  # "scattered", "grouped", "mixed", "random".
REPRESENTATION_RATE = (0.01, 0.03)  # Share of current eligible nonmissing values changed.
REPRESENTATION_GROUPS = 3
REPRESENTATION_GROUPED_SHARE = 0.70
NUMERIC_SUFFIX = "%"  # A plausible unit marker for the configured humidity column.
SENTINEL = "not_recorded"  # An ordinary string that read_csv does not treat as NA.

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

def add_representation_errors(
    df, rng, *, time_column, timezone, start_date, end_date,
    target_column, kind, placement, representation_rate, n_groups,
    grouped_share, numeric_suffix, sentinel,
):
    if time_column not in df or target_column not in df:
        raise ValueError("Time or target column not found.")
    if target_column == time_column:
        raise ValueError("Do not alter the time column with this value-representation function.")
    if not 0 < representation_rate[0] <= representation_rate[1] < 1:
        raise ValueError("Representation rate must lie between 0 and 1.")
    if placement not in ("scattered", "grouped", "mixed", "random"):
        raise ValueError("Unrecognized representation placement.")
    if not 0 < grouped_share < 1 or not numeric_suffix or not sentinel:
        raise ValueError("Provide a positive grouped share and nonempty representation markers.")
    if sentinel.lower() in ("", "na", "n/a", "null", "none", "nan"):
        raise ValueError("Use a sentinel that read_csv won't silently interpret as a null.")

    dirty = df.reset_index(drop=True).copy()
    numeric = pd.api.types.is_numeric_dtype(dirty[target_column])
    allowed = ("numeric_suffix", "sentinel") if numeric else ("whitespace", "case", "sentinel")
    if kind not in (*allowed, "mixed", "random"):
        raise ValueError(f"Kind {kind!r} is not supported for this column type.")
    selected_kind = rng.choice((*allowed, "mixed")) if kind == "random" else kind
    selected_placement = rng.choice(("scattered", "grouped", "mixed")) if placement == "random" else placement

    time = pd.to_datetime(dirty[time_column], utc=True, errors="raise")
    if time.isna().any():
        raise ValueError("Timestamps cannot be missing.")
    start = pd.Timestamp(start_date, tz=timezone).tz_convert("UTC")
    end = pd.Timestamp(end_date, tz=timezone).tz_convert("UTC")
    if start >= end:
        raise ValueError("Start date must precede end date.")

    scope = time.ge(start) & time.lt(end)
    if selected_kind in ("sentinel", "mixed") and dirty[target_column].dropna().astype(str).eq(sentinel).any():
        raise ValueError("Sentinel already exists; use a fresh source or choose a different marker.")
    eligible_mask = scope & dirty[target_column].notna()
    if not numeric and selected_kind in ("case", "mixed"):
        # Case changes require letters; numeric-looking IDs should instead use whitespace.
        eligible_mask &= dirty[target_column].astype(str).map(lambda x: any(c.isalpha() for c in x))
    eligible = dirty.loc[eligible_mask].sort_values(time_column, kind="stable")
    if eligible.empty:
        raise ValueError("No eligible values; check the column, dates, and representation kind.")

    selected, _ = select_positions(
        len(eligible), rng, method=placement, rate=representation_rate,
        n_groups=n_groups, grouped_share=grouped_share,
    )
    count = len(selected)
    if selected_kind == "mixed" and count < len(allowed):
        raise ValueError("Mixed representation needs at least one observation per style.")

    rows = eligible.index[selected]
    styles = [allowed[i % len(allowed)] for i in range(count)] if selected_kind == "mixed" else [selected_kind] * count
    rng.shuffle(styles)
    original = dirty.loc[rows, target_column].astype(str).copy()
    dirty[target_column] = dirty[target_column].astype(object)
    for row, style in zip(rows, styles):
        value = str(dirty.at[row, target_column])
        if style == "numeric_suffix":
            changed = value + numeric_suffix
        elif style == "whitespace":
            changed = value + " "
        elif style == "case":
            changed = value.upper() if value != value.upper() else value.lower()
        else:
            changed = sentinel
        dirty.at[row, target_column] = changed

    if not dirty.loc[rows, target_column].astype(str).ne(original).all():
        raise AssertionError("Some selected values did not change representation.")
    return dirty

if __name__ == "__main__":
    df = pd.read_csv(DATA_PATH)
    rng = Random(SEED)
    df = add_representation_errors(
        df, rng, time_column=TIME_COLUMN, timezone=TIMEZONE,
        start_date=START_DATE, end_date=END_DATE,
        target_column=REPRESENTATION_TARGET_COLUMN, kind=REPRESENTATION_KIND,
        placement=REPRESENTATION_PLACEMENT, representation_rate=REPRESENTATION_RATE,
        n_groups=REPRESENTATION_GROUPS, grouped_share=REPRESENTATION_GROUPED_SHARE,
        numeric_suffix=NUMERIC_SUFFIX, sentinel=SENTINEL,
    )
    df.to_csv(DATA_PATH, index=False)
