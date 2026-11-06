# Weather fields and data-condition contracts

This document describes the **current generator's synthetic defects**, not a meteorological quality-control standard. The numeric boundaries are broad training thresholds selected to create conspicuous anomalies. A value inside a range is not automatically correct, and a genuine observation outside a range requires domain/source review rather than automatic deletion. All hourly observations are Open-Meteo ERA5 reanalysis for a configured point; they are not direct station readings.

## New default fields

The default source for newly generated practices is `time` plus ten measurements. The latest additions are:

| Field | Unit | Meaning / analytical use | Training range (inclusive) | Out-of-bounds margin | Scaled-value factor | Conflicting-duplicate offset |
|---|---|---|---:|---:|---:|---:|
| `precipitation` | mm | Preceding-hour total precipitation, including liquid and snow water equivalent; wet-hour frequency and accumulated totals | 0–100 | 10 | ×3 | +1.5 |
| `snowfall` | cm | Preceding-hour snowfall amount; snow-event and accumulation patterns | 0–50 | 5 | ×3 | +1.5 |
| `dew_point_2m` | °C | Dew-point temperature; moisture, humidity, temperature–dew-point gap | −70–50 | 10 | ×2 | +1.5 |
| `surface_pressure` | hPa | Atmospheric pressure at surface elevation, **not** pressure reduced to sea level; pressure variability and event context | 650–1100 | 50 | ×2 | +10 |

The precipitation and snowfall thresholds are deliberately generous for normal hourly values and are **not** published physical extremes. `precipitation` and `snowfall` can legitimately be zero for most observations. In particular, `snowfall` is measured in centimetres and **must not be added directly to precipitation in millimetres**. The pressure range accommodates altitude-dependent pressure; an analyst should not equate surface pressure with mean-sea-level pressure.

For context, the previously supported measurement profiles remain unchanged:

| Field | Unit | Training range |
|---|---|---:|
| `temperature_2m` | °C | −60–60 |
| `relative_humidity_2m` | % | 0–100 |
| `wind_speed_10m` | km/h | 0–250 |
| `wind_direction_10m` | degrees | 0–360 (circular: 0° and 360° are equivalent directions) |
| `cloud_cover` | % | 0–100 |
| `shortwave_radiation` | W/m² | 0–1600 |

## Which defects are implemented?

New measurements use the same existing corruption operators as the six prior measurements. This is an **eligibility and parameter** expansion, not a new family of defects.

| Condition | Behavior | Scope / restrictions |
|---|---|---|
| `none` | Preserve prepared values | All formats |
| `missing_values` | Replace selected observations in one target measurement with nulls | CSV, JSON, Parquet, SQL |
| `missing_records` | Remove complete hourly records, creating temporal coverage gaps | All formats; not field-targeted |
| `duplicates` — `exact` | Duplicate whole records, preserving fields | All formats; not field-targeted |
| `duplicates` — `conflicting` or `mixed` | Copy records and change one measurement on some copies by its profile's offset | All formats |
| `invalid_values` — `out_of_bounds` | Generate selected values below `valid_min − margin` or above `valid_max + margin`; SQL uses the respective boundary exactly | All formats; synthetic training thresholds |
| `invalid_values` — `scaled` | Multiply nonzero existing observations by the field's factor | All formats; changed values may **still** fall inside the training range |
| `invalid_values` — `mixed` | Combine both value-error mechanisms | All formats; requires enough eligible rows |
| `representation` — `numeric_suffix` | Replace numeric cells with strings containing the measurement and its unit suffix | CSV only |
| `representation` — `sentinel` | Insert `not_recorded` as a nonstandard missing-value marker | CSV only |

The default target/record selection affects approximately **1–3% of eligible observations** (with mixed grouped and scattered placement); it does not guarantee that 1–3% of *all* rows will be affected, particularly for zero-heavy variables and scaled errors. A fixed seed and recorded profile parameters make each exercise reproducible.

To target one of the new measurements intentionally:

```sh
just setup snow-study --format csv --dirty invalid_values --variant out_of_bounds \
  --dirty-vars snowfall
```

To choose one from a pool:

```sh
just setup moisture-study --blind \
  --dirty-vars precipitation,snowfall,dew_point_2m,surface_pressure
```

The comma-separated list is an **OR pool**, never four simultaneous corruptions. Omitting `--dirty-vars` deliberately retains the earlier default targeting (`temperature_2m` for value errors; `wind_speed_10m` for missing values and representation, etc.), so new fields are eligible **when selected explicitly**, not silently added to the default targeting distribution. A clean or whole-row condition may be chosen regardless of the pool.

## Not implemented as corruption conditions

- Cross-field consistency violations (e.g., dew point materially above air temperature, pressure inconsistencies, precipitation and snowfall water-equivalent relationships).
- Unit *conversions* or mixing numerical units without clear suffixes (e.g., hPa versus Pa).
- Threshold-based event impossibilities inferred from combinations of measurements.
- Inherited or pre-existing source quality issues; the generator checks time coverage and schema, not all meteorological relationships.

These may be worthwhile future dirty-data exercises, but are **not** currently selectable conditions. The source model and weather API do not make every apparent meteorological anomaly conclusive evidence of corruption.

## Compatibility and source limitations

New practices freeze eleven columns in `[source].columns` in their `practice.toml`; earlier practice manifests still record seven, and reconstruction must honor that frozen layout. CSV, JSON, and Parquet obtain expanded fields from Open-Meteo during preparation. SQL reads the requested columns from the locally available `canonical.weather_hourly` table and refuses if they are absent; **the ordinary generator never upgrades or rewrites canonical**. The preexisting historical SQL source schema includes the four newly selected fields, but this repository cannot guarantee that every external PostgreSQL installation has them or that all requested dates are populated. `just doctor` checks the configured columns when deciding whether SQL is eligible.

Documentation: [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api).
