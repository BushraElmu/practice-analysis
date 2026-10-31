# practice-analysis

Personal analytical workflow practice using reproducible Calgary weather data. The
analytical question changes; the core workflow repeats:

`problem → prepare → load → inspect → establish grain/quality → clean if needed → transform → validate → visualize → reproduce`

The tooling prepares the **starting dataset**. It does not clean the data,
select a solution, or perform the analysis. Completed historical Practices 1–18
remain in their original independent directories.

## Installation and configuration

Requires Python 3.11+, [`uv`](https://docs.astral.sh/uv/), and
[`just`](https://just.systems/). PostgreSQL is optional if SQL practices are not
needed.

```sh
uv sync --no-editable --project tooling
just doctor
just list
```

Each newly generated practice has its own `pyproject.toml`, lockfile and local
virtual environment. The repository root is not a Python package. A local,
Git-ignored `personal.just` may define personal shortcuts, but is not required.
The root `justfile` provides all essential commands.

### SQL on this Mac

The historical source is the existing `analytical_workflow_fluency` database,
`canonical.weather_hourly` table. Use the pre-existing **restricted**
`awf_practice` role; it may read canonical data and create independent practice
schemas, but cannot change the canonical table.

```sh
export AWF_DATABASE_URL='dbname=analytical_workflow_fluency user=awf_practice host=/tmp'
unset AWF_CANONICAL_DATABASE_URL
just doctor
```

Keep the canonical-maintenance connection unset during normal practice. **Do
not run `just sql-sync` against the historical source as part of routine
setup**; the source is already populated. Generated SQL data live in a new
`practice_NNN.weather_hourly` schema. SQL setup refuses to overwrite an
existing practice schema. `just verify` uses isolated reconstruction and does
not reset analyst tables.

## Generate and use a practice

```sh
just setup exercise-slug --blind
just list --linked
just show 19
just verify 19
just reveal 19
```

The assigned number may differ from 19. `--blind` hides the condition,
subtype and seed from normal public metadata; the local `.awf/` record can
still be deliberately inspected. Blindness prevents accidental disclosure,
not intentional access to private files.

By default, **format, corruption family (including clean), applicable subtype,
and seed are randomly selected**. The exercise question is written by the
analyst; it is not generated or randomized. Dates are configured separately.
The current source default is all of calendar **2025**, in
`America/Edmonton` local time. Dates use a start-inclusive, end-exclusive
interval. Override when appropriate:

```sh
just setup another-exercise --blind --start 2024-01-01 --end 2026-01-01
just setup explicit-csv --format csv --dirty missing_values --blind
```

`tooling/selection.toml` records the *training* probabilities, not real-world
prevalence. With all formats eligible: 25% per format, ~31.5% clean overall,
with the rest spread approximately evenly among supported conditions. If SQL
is unavailable, the other formats are renormalized. JSON structural corruption
is not implemented and is not randomly selected.

For file-based exercises, `prepare_data.py` and `dirty_data.py` reconstruct
from the local frozen clean snapshot, subject to no-overwrite checks. In SQL,
`prepare_data.py` refuses to replace an existing practice schema. Use the
notebook's Imports, Load, Inspect, Clean, Transform, and Visualize sections to
perform the analysis yourself. Open Jupyter using your local workflow or from
the practice directory after `uv sync` (e.g., `uv run --with jupyter jupyter lab`).

`just verify N` reconstructs the *original generated starting state* without
overwriting the current data. `just reveal N` discloses the hidden condition;
`just reveal N --publish` also makes it public. `just index` regenerates the
root registry after manual metadata changes. Historical Practices 1–18 are
metadata-only registry entries and cannot use the new frozen verification
contract.

Generated datasets, private `.awf` records and clean-source snapshots are
Git-ignored. **A fresh clone cannot necessarily verify or byte-for-byte
reconstruct an older generated practice** without those preserved local
artifacts. Retain local snapshots if long-term reproduction matters.

The currently supported generated weather fields are `time`,
`wind_speed_10m`, `wind_direction_10m`, `temperature_2m`, `cloud_cover`,
`relative_humidity_2m`, and `shortwave_radiation`. The original historical
canonical database has additional variables; these are not yet exposed by the
new generator.

## Existing practices

| # | Exercise | Format |
|---|---|---|
| 1 | Average June Temperature, 2024–2026 | CSV |
| 2 | Average May Temperature, 2024–2026 | CSV |
| 3 | Maximum April Temperature, 2024–2026 | CSV |
| 4 | 7-Day Rolling Average Temperature, Final 90 Days | CSV |
| 5 | Average Relative Humidity by Hour of Day, August 2025 | CSV |
| 6 | Average Daily Temperature Range by Month, 2025 | CSV |
| 7 | Daytime vs Overnight Temperature by Month, Summer 2025 | CSV |
| 8 | Monthly Precipitation, 2024 vs 2025 | SQL |
| 9 | Precipitation Days by Month, 2025 | SQL |
| 10 | Largest Daily Temperature Swings, 2025 | JSON |
| 11 | January vs July Temperature Distribution, 2025 | Parquet |
| 12 | Trailing 7-Day Precipitation Total, Spring–Summer 2026 | CSV |
| 13 | Cumulative Precipitation, 2024 vs 2025 | SQL |
| 14 | Temperature vs Relative Humidity, August 2026 | CSV |
| 15 | Hot and Dry Daytime Hours, Summer 2026 | Parquet |
| 16 | Historical Temperature Anomalies, 2025 | JSON |
| 17 | Longest Freezing Streak, Winter 2025 | SQL |
| 18 | Solar Radiation by Cloud-Cover Band, June 2025 | CSV |

Deliberate data corruption began with Practice 14. Existing historical
preparation scripts and notebooks are preserved as written.

## Data and license

Weather data originate from the [Open-Meteo](https://open-meteo.com/)
Historical Weather API / ERA5, using Calgary local time
(`America/Edmonton`). Code is licensed under the
[Apache License 2.0](LICENSE); data retain their respective attribution and
license requirements.
