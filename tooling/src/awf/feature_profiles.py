"""Feature-aware corruption profiles for the default weather measurements.

These are training-generation parameters, NOT meteorological quality-control
bounds. Older practice manifests preserve the profile parameters used at setup.
"""
from __future__ import annotations

from dataclasses import dataclass
import random

from .weather import COLUMNS

MEASUREMENTS = COLUMNS[1:]


@dataclass(frozen=True)
class NumericProfile:
    kind: str
    unit: str
    valid_min: float
    valid_max: float
    outside_margin: float
    scale_factor: float = 2.0
    conflict_offset: float = 1.5


PROFILES: dict[str, NumericProfile] = {
    'temperature_2m': NumericProfile('signed', ' °C', -60.0, 60.0, 10.0),
    'relative_humidity_2m': NumericProfile('percentage', '%', 0.0, 100.0, 10.0),
    'wind_speed_10m': NumericProfile('nonnegative', ' km/h', 0.0, 250.0, 10.0),
    'wind_direction_10m': NumericProfile('circular', '°', 0.0, 360.0, 10.0),
    'cloud_cover': NumericProfile('percentage', '%', 0.0, 100.0, 10.0),
    'shortwave_radiation': NumericProfile('nonnegative', ' W/m²', 0.0, 1600.0, 50.0),
    # The values below are deliberately broad training-generation thresholds,
    # NOT verified Calgary extreme-value bounds or an observation QC system.
    'precipitation': NumericProfile('nonnegative', ' mm', 0.0, 100.0, 10.0, scale_factor=3.0),
    'snowfall': NumericProfile('nonnegative', ' cm', 0.0, 50.0, 5.0, scale_factor=3.0),
    'dew_point_2m': NumericProfile('signed', ' °C', -70.0, 50.0, 10.0),
    'surface_pressure': NumericProfile('positive', ' hPa', 650.0, 1100.0, 50.0,
                                       conflict_offset=10.0),
}

if set(PROFILES) != set(MEASUREMENTS):
    raise AssertionError('Every currently supplied weather measurement needs a profile')

TARGETED = {'missing_values', 'invalid_values', 'representation'}
ROW_ONLY = {'none', 'missing_records'}


def parse_dirty_vars(argument: str | None, available: list[str]) -> tuple[str, ...] | None:
    """Explicit comma list means an OR pool, not multiple simultaneous defects."""
    if argument is None:
        return None
    names = [name.strip() for name in argument.split(',')]
    if not names or any(not name for name in names):
        raise ValueError('--dirty-vars requires a nonempty comma-separated list of fields')
    if len(set(names)) != len(names):
        raise ValueError('--dirty-vars contains duplicate field names')
    unknown = [name for name in names if name not in PROFILES or name not in available]
    if unknown:
        raise ValueError(f'Unsupported/unavailable --dirty-vars: {unknown}; supported: {sorted(set(PROFILES).intersection(available))}')
    return tuple(names)


def compatible_targets(condition: str, fmt: str, variant: str, pool: tuple[str, ...]) -> tuple[str, ...]:
    """Profiles and current format capabilities restrict column-level operators."""
    if condition in ROW_ONLY or (condition == 'duplicates' and variant == 'exact'):
        return ()
    if condition == 'representation' and fmt != 'csv':
        return ()
    if condition not in TARGETED and condition != 'duplicates':
        return ()
    return tuple(name for name in pool if name in PROFILES)


def select_target(seed: int, fmt: str, condition: str, variant: str,
                  pool: tuple[str, ...]) -> str | None:
    choices = compatible_targets(condition, fmt, variant, pool)
    if not choices:
        if condition in ROW_ONLY or (condition == 'duplicates' and variant == 'exact'):
            return None
        raise ValueError(f'No compatible --dirty-vars target for {fmt}/{condition}/{variant}')
    # Explicitly separate stream from format/condition/subtype/row placement.
    return random.Random(f'awf-target-v1:{seed:06d}:{fmt}:{condition}:{variant}').choice(choices)


def condition_options(condition: str, selected_target: str | None) -> dict:
    """Resolve target-dependent settings while preserving V2.1 extent/placement."""
    target = selected_target if selected_target is not None else 'time'
    options = {
        'target': target, 'min_rate': 0.01, 'max_rate': 0.03,
        'placement': 'mixed', 'groups': 3, 'grouped_share': 0.70,
    }
    if condition == 'duplicates':
        # Unused for exact duplicates; meaningful only for conflicting copies.
        profile = PROFILES[selected_target] if selected_target else None
        options.update(conflict_column=selected_target or 'temperature_2m',
                       conflict_offset=profile.conflict_offset if profile else 1.5,
                       conflict_share=0.50)
    elif condition == 'invalid_values':
        if selected_target is None:
            raise ValueError('Invalid-values corruption requires a measurement target')
        profile = PROFILES[selected_target]
        options.update(valid_min=profile.valid_min, valid_max=profile.valid_max,
                       outside_margin=profile.outside_margin,
                       scale_factor=profile.scale_factor, bounds_share=0.50)
    elif condition == 'representation':
        if selected_target is None:
            raise ValueError('Representation corruption requires a measurement target')
        options.update(numeric_suffix=PROFILES[selected_target].unit,
                       sentinel='not_recorded')
    return options
