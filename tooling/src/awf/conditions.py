"""Real weather conditions, adapted from user's format-specific notebooks.

All changes are performed on disposable analyst-facing data, never on canonical
source; one condition per practice. Meaningful subtypes are supported for
duplicates, value errors and numeric CSV representation. SQL mutations live in sql_adapter.py; schema and JSON structural corruption
remain outside the accepted selection pool pending integration tests.
"""
from __future__ import annotations

import copy
import random

import pandas as pd

# Supported *real* adapter combinations, distinct from Stage 2 mock labels.
CAPABILITIES = {
    "csv": {"none", "missing_values", "missing_records", "duplicates", "invalid_values", "representation"},
    "json": {"none", "missing_values", "missing_records", "duplicates", "invalid_values"},
    "parquet": {"none", "missing_values", "missing_records", "duplicates", "invalid_values"},
    "sql": {"none", "missing_values", "missing_records", "duplicates", "invalid_values"},
    # SQL eligibility is gated on a configured, accessible PostgreSQL canonical table.
}


def select_positions(n: int, rng: random.Random, *, method: str, rate: tuple[float, float],
                     n_groups: int, grouped_share: float) -> tuple[list[int], str]:
    """Preserve grouped/scattered/mixed selection algorithm from user's archive."""
    if n < 1:
        raise ValueError("No eligible observations")
    if method not in ("scattered", "grouped", "mixed", "random"):
        raise ValueError("Unknown placement method")
    if not 0 < rate[0] <= rate[1] < 1:
        raise ValueError("Rate must lie between 0 and 1")
    if n_groups < 1 or not 0 < grouped_share < 1:
        raise ValueError("Invalid group count or grouped share")
    chosen = rng.choice(("scattered", "grouped", "mixed")) if method == "random" else method
    count = max(1, round(n * rng.uniform(*rate)))
    if chosen == "mixed" and count < 3:
        raise ValueError("Mixed placement needs at least 3 affected records")
    grouped = (count if chosen == "grouped" else
               max(2, min(count - 1, round(count * grouped_share))) if chosen == "mixed" else 0)
    selected = set()
    if grouped:
        if grouped < 2:
            raise ValueError("Grouped placement requires at least two records")
        groups = min(n_groups, grouped // 2)
        if n - grouped < groups - 1:
            raise ValueError("Insufficient eligible records for separate groups")
        length, extra = divmod(grouped, groups)
        lengths = [length+(i < extra) for i in range(groups)]
        rng.shuffle(lengths)
        slack = n-grouped-(groups-1)
        dividers = sorted(rng.sample(range(slack+groups), groups))
        gaps = ([dividers[0]] +
                [dividers[i]-dividers[i-1]-1 for i in range(1,groups)] +
                [slack+groups-1-dividers[-1]])
        cursor = gaps[0]
        for i, block in enumerate(lengths):
            selected.update(range(cursor,cursor+block))
            if i < groups-1:
                cursor += block + 1 + gaps[i+1]
    if count-grouped:
        remaining = [i for i in range(n) if i not in selected]
        selected.update(rng.sample(remaining, k=count-grouped))
    if len(selected) != count:
        raise AssertionError("Selection count mismatch")
    return sorted(selected), chosen


def apply(value, *, fmt: str, kind: str, seed: int, source: dict,
          target: str, rate: tuple[float, float] = (0.01, 0.03),
          placement: str = "mixed", n_groups: int = 3,
          grouped_share: float = 0.70, variant: str = "none",
          parameters: dict | None = None):
    """Modify a disposable copy; return (modified value, realized audit).

    Subtypes adapt the tested weather-relevant mechanisms in the user's CSV,
    JSON, and Parquet development notebooks. Selection happens upstream; this
    function never silently substitutes one selected variant for another.
    """
    if kind not in CAPABILITIES.get(fmt, set()):
        raise ValueError(f"{kind} not implemented for real {fmt} preparation")
    if kind == "none":
        if variant != "none":
            raise ValueError("Clean data cannot have a corruption variant")
        return copy.deepcopy(value), {"kind": "none", "variant": "none",
                                      "affected_rows": 0, "selected_positions": []}
    from .selection import VARIANTS
    allowed = VARIANTS.get(kind, {"none"})
    if variant not in allowed:
        raise ValueError(f"Unsupported variant {variant} for {kind}")
    cfg = parameters or {}
    rng = random.Random(seed)
    if fmt == "json":
        dirty = copy.deepcopy(value)
        hourly = dirty["hourly"]
        if "time" not in hourly or target not in hourly:
            raise ValueError("Unknown JSON time/target field")
        times = pd.to_datetime(hourly["time"], unit="s", utc=True)
        if any(len(seq) != len(times) for seq in hourly.values()):
            raise ValueError("Misaligned JSON hourly arrays")
        target_values = hourly[target]
    else:
        dirty = value.reset_index(drop=True).copy(deep=True)
        if "time" not in dirty or target not in dirty:
            raise ValueError("Unknown tabular time/target field")
        times = pd.to_datetime(dirty["time"], utc=True, errors="raise")
        target_values = dirty[target].tolist()

    start = pd.Timestamp(source["start_local"], tz=source["timezone"]).tz_convert("UTC")
    end = pd.Timestamp(source["end_local"], tz=source["timezone"]).tz_convert("UTC")
    eligible = [i for i, (t, val) in enumerate(zip(times, target_values))
                if start <= t < end and pd.notna(val)]
    if kind == "missing_values" and any(
        pd.isna(val) for t, val in zip(times, target_values) if start <= t < end
    ):
        raise ValueError("Target already contains missing values; restore source")
    if kind == "duplicates":
        if pd.Index(times).has_duplicates:
            raise ValueError("Original source contains duplicate timestamps")
        conflict_column = cfg.get("conflict_column", "temperature_2m")
        if conflict_column == "time":
            raise ValueError("Cannot modify duplicate keys")
        if conflict_column not in (hourly if fmt == "json" else dirty.columns):
            raise ValueError("Duplicate conflict column missing")
        if variant != "exact":
            values = hourly[conflict_column] if fmt == "json" else dirty[conflict_column].tolist()
            eligible = [i for i in eligible if pd.notna(values[i]) and
                        isinstance(values[i], (int, float)) and not isinstance(values[i], bool)]
    if kind == "invalid_values":
        lower, upper = cfg.get("valid_min", -60.0), cfg.get("valid_max", 60.0)
        if lower >= upper:
            raise ValueError("Invalid numeric bounds")
        if any((float(target_values[i]) < lower or float(target_values[i]) > upper)
               for i in eligible):
            raise ValueError("Existing out-of-range values; restore source")
        if variant in ("scaled", "mixed"):
            eligible = [i for i in eligible if float(target_values[i]) != 0]
    if kind == "representation":
        if fmt != "csv" or not pd.api.types.is_numeric_dtype(dirty[target]):
            raise ValueError("Only numeric CSV representation is integrated")
        sentinel = cfg.get("sentinel", "not_recorded")
        if sentinel.lower() in ("", "na", "n/a", "null", "none", "nan"):
            raise ValueError("Use an explicit nonstandard sentinel")
        if dirty[target].astype(str).eq(sentinel).any():
            raise ValueError("Sentinel already present in clean source")
    if not eligible:
        raise ValueError("No eligible target observations")

    positions, realized_placement = select_positions(
        len(eligible), rng, method=placement, rate=rate, n_groups=n_groups,
        grouped_share=grouped_share)
    indices = [eligible[j] for j in positions]
    count = len(indices)
    if variant == "mixed" and kind in ("duplicates", "invalid_values", "representation") and count < 2:
        raise ValueError("Mixed variant requires at least two affected observations")
    extra = {}

    if kind == "missing_values":
        if fmt == "json":
            for idx in indices:
                hourly[target][idx] = None
        else:
            if pd.api.types.is_integer_dtype(dirty[target]):
                dirty[target] = dirty[target].astype("Int64")
            dirty.loc[indices, target] = pd.NA
    elif kind == "missing_records":
        if fmt == "json":
            dropping = set(indices)
            for key, sequence in hourly.items():
                hourly[key] = [v for i, v in enumerate(sequence) if i not in dropping]
        else:
            dirty = dirty.drop(index=indices).reset_index(drop=True)
    elif kind == "duplicates":
        conflict_column = cfg.get("conflict_column", "temperature_2m")
        offset, share = cfg.get("conflict_offset", 1.5), cfg.get("conflict_share", 0.50)
        if not isinstance(offset, (float, int)) or offset == 0 or not 0 < share < 1:
            raise ValueError("Invalid duplicate conflict settings")
        n_conflict = (0 if variant == "exact" else count if variant == "conflicting"
                      else max(1, min(count - 1, round(count * share))))
        if fmt == "json":
            conflicting = set(rng.sample(indices, k=n_conflict))
            for idx in sorted(indices, reverse=True):
                for sequence in hourly.values():
                    sequence.insert(idx, sequence[idx])
                if idx in conflicting:
                    hourly[conflict_column][idx] += offset
        else:
            copies = dirty.iloc[indices].copy().reset_index(drop=True)
            if n_conflict:
                selected = rng.sample(range(count), k=n_conflict)
                if not pd.api.types.is_numeric_dtype(copies[conflict_column]):
                    raise ValueError("Duplicate conflict field must be numeric")
                copies.loc[selected, conflict_column] += offset
            dirty = (pd.concat([dirty, copies], ignore_index=True)
                     .sort_values("time", kind="stable").reset_index(drop=True))
        extra["conflicting_rows"] = n_conflict
    elif kind == "invalid_values":
        lower, upper = cfg.get("valid_min", -60.0), cfg.get("valid_max", 60.0)
        margin = cfg.get("outside_margin", 10.0)
        scale = cfg.get("scale_factor", 2.0)
        share = cfg.get("bounds_share", 0.50)
        if margin <= 0 or scale in (0, 1) or not 0 < share < 1:
            raise ValueError("Invalid numeric error settings")
        n_bounds = (count if variant == "out_of_bounds" else 0 if variant == "scaled"
                    else max(1, min(count - 1, round(count * share))))
        bound_indices = set(rng.sample(indices, k=n_bounds))
        for idx in indices:
            old = target_values[idx]
            if idx in bound_indices:
                changed = (upper + margin + rng.random() * margin
                           if rng.random() < 0.5 else lower - margin - rng.random() * margin)
            else:
                changed = float(old) * scale
            if changed == old:
                raise AssertionError("Chosen error failed to modify measurement")
            if fmt == "json":
                hourly[target][idx] = changed
            else:
                dirty.at[idx, target] = changed
        extra["out_of_bounds_rows"] = n_bounds
        extra["scaled_rows"] = count - n_bounds
    elif kind == "representation":
        suffix = cfg.get("numeric_suffix", " km/h")
        sentinel = cfg.get("sentinel", "not_recorded")
        if not suffix or not sentinel:
            raise ValueError("Representation markers cannot be empty")
        styles = ([variant] * count if variant != "mixed" else
                  ["numeric_suffix", "sentinel"] * (count // 2) +
                  (["numeric_suffix"] if count % 2 else []))
        rng.shuffle(styles)
        dirty[target] = dirty[target].astype(object)
        for idx, style in zip(indices, styles):
            value = str(dirty.at[idx, target])
            dirty.at[idx, target] = value + suffix if style == "numeric_suffix" else sentinel
        extra["style_counts"] = {style: styles.count(style) for style in set(styles)}
    else:
        raise ValueError(kind)
    return dirty, {"kind": kind, "variant": variant, "affected_rows": count,
                   "selected_positions": indices, "placement": realized_placement,
                   "eligible_population": len(eligible), "target": target, **extra}
