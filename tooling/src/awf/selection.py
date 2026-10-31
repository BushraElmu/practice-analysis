"""Validate and resolve weighted format -> conditional-dirtiness choices."""

from __future__ import annotations

import random
import secrets
import tomllib
from dataclasses import dataclass
from pathlib import Path

KNOWN_FORMATS = {"csv", "parquet", "json", "sql"}
KNOWN_CONDITIONS = {
    "none", "missing_values", "missing_records", "duplicates",
    "invalid_values", "representation", "structure",
}
MAX_SEED = 999_999  # display and store as six digits (000000..999999)


@dataclass(frozen=True)
class Choice:
    seed: int
    seed_source: str
    requested_format: str
    requested_dirty: str
    format: str
    dirty: str
    format_weights: dict[str, int]
    condition_weights: dict[str, int]


def _weights(name: str, raw: object, *, valid: set[str]) -> dict[str, int]:
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{name} must be a nonempty TOML table")
    if unknown := set(raw).difference(valid):
        raise ValueError(f"Unknown options in {name}: {sorted(unknown)}")
    if any(type(v) is not int or v < 0 for v in raw.values()):
        raise ValueError(f"{name} weights must be nonnegative integers")
    if sum(raw.values()) != 100:
        raise ValueError(f"{name} weights must sum to 100, got {sum(raw.values())}")
    return dict(raw)


def load_policy(path: Path) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    with path.open("rb") as handle:
        content = tomllib.load(handle)
    formats = _weights("formats", content.get("formats"), valid=KNOWN_FORMATS)
    raw_conditions = content.get("conditions")
    if not isinstance(raw_conditions, dict):
        raise ValueError("Missing [conditions.<format>] tables")
    if unknown := set(raw_conditions).difference(formats):
        raise ValueError(f"Condition tables for unlisted formats: {sorted(unknown)}")
    if missing := set(formats).difference(raw_conditions):
        raise ValueError(f"Missing condition tables for: {sorted(missing)}")
    conditions = {
        fmt: _weights(f"conditions.{fmt}", raw, valid=KNOWN_CONDITIONS)
        for fmt, raw in raw_conditions.items()
    }
    return formats, conditions


def _weighted(rng: random.Random, weights: dict[str, int]) -> str:
    positive = [(name, weight) for name, weight in weights.items() if weight > 0]
    if not positive:
        raise ValueError("No positive-weight eligible option")
    names, values = zip(*positive)
    return rng.choices(names, weights=values, k=1)[0]


# Only variants with a meaningfully different diagnostic/resolution decision.
# The standard missing-value/record routines retain their existing behavior.
VARIANTS = {
    "duplicates": {"exact", "conflicting", "mixed"},
    "invalid_values": {"out_of_bounds", "scaled", "mixed"},
    "representation": {"numeric_suffix", "sentinel", "mixed"},
}


def resolve_variant(
    policy_path: Path, *, condition: str, fmt: str, seed: int,
    requested_variant: str = "random",
) -> tuple[str, dict[str, int]]:
    """Resolve a condition subtype with an independent deterministic RNG.

    A selection-policy edit cannot silently reroll an existing practice: its
    resolved subtype and selected weights are written to practice.toml.
    """
    if condition not in VARIANTS:
        if requested_variant not in ("random", "none"):
            raise ValueError(f"{condition} has no variant; got {requested_variant}")
        return "none", {}
    if fmt != "csv" and condition == "representation":
        raise ValueError("Representation variants currently require CSV")
    if requested_variant != "random" and requested_variant not in VARIANTS[condition]:
        raise ValueError(f"Unsupported {condition} variant: {requested_variant}")
    with policy_path.open("rb") as handle:
        raw = tomllib.load(handle).get("variants", {})
    if not isinstance(raw, dict) or condition not in raw:
        raise ValueError(f"Missing [variants.{condition}] weights")
    weights = _weights(f"variants.{condition}", raw[condition], valid=VARIANTS[condition])
    # Stable stream, independent of the format/family selection's RNG calls.
    rng = random.Random(f"awf-variant-v1:{seed:06d}:{fmt}:{condition}")
    variant = _weighted(rng, weights) if requested_variant == "random" else requested_variant
    return variant, weights


def choose(
    policy_path: Path, *, requested_format: str = "random",
    requested_dirty: str = "random", seed: int | None = None,
    capabilities: dict[str, set[str]] | None = None,
) -> Choice:
    format_weights, conditions = load_policy(policy_path)
    if capabilities is not None:
        # Limit to tested real adapters. Zero-weight options remain explicitly
        # selectable, but unsupported conditions are rejected even explicitly.
        formats = {name: weight for name,weight in format_weights.items()
                   if name in capabilities and capabilities[name]}
        if not formats:
            raise ValueError("No real preparation formats are available")
        conditions = {fmt: {name:weight for name,weight in conditions[fmt].items()
                            if name in capabilities[fmt]} for fmt in formats}
        format_weights = formats
    if seed is not None and (type(seed) is not int or not 0 <= seed <= MAX_SEED):
        raise ValueError(f"seed must be an integer between 0 and {MAX_SEED}")
    real_seed = secrets.randbelow(1_000_000) if seed is None else seed
    rng = random.Random(real_seed)

    if requested_format != "random" and requested_format not in format_weights:
        raise ValueError(f"Unknown/unsupported format: {requested_format}")
    if requested_dirty != "random" and requested_dirty not in KNOWN_CONDITIONS:
        raise ValueError(f"Unknown dirtiness condition: {requested_dirty}")

    if requested_format == "random":
        eligible = {
            fmt: weight for fmt, weight in format_weights.items()
            if (requested_dirty == "random" and any(w > 0 for w in conditions[fmt].values()))
            or (requested_dirty != "random" and requested_dirty in conditions[fmt])
        }
        if not eligible:
            raise ValueError(f"No format supports the requested condition: {requested_dirty}")
        fmt = _weighted(rng, eligible)
    else:
        fmt = requested_format
        if requested_dirty != "random" and requested_dirty not in conditions[fmt]:
            raise ValueError(f"{requested_dirty} is not supported by format {fmt}")

    dirty = (
        _weighted(rng, conditions[fmt])
        if requested_dirty == "random" else requested_dirty
    )
    return Choice(
        seed=real_seed, seed_source="generated" if seed is None else "provided",
        requested_format=requested_format, requested_dirty=requested_dirty,
        format=fmt, dirty=dirty,
        format_weights=format_weights,
        condition_weights=conditions[fmt],
    )
