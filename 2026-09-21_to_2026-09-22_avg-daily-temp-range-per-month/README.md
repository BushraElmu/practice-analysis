# Practice 6: Average Daily Temperature Range by Month, 2025

Calculate and visualize Calgary's average daily temperature range for each month of 2025.

## Problem

Show Calgary's average daily temperature range for each month of 2025, where a day's temperature range is the difference between its maximum and minimum hourly temperature.

## Parameters

- Data Source: Open-Meteo Historical Weather API (ERA5)
- Data Format: CSV
- Source Grain: Hourly
- Timezone: America/Edmonton
- Intermediate Grain: One row per day with daily minimum, maximum, and temperature range
- Output Grain: One average daily temperature range per month
- Tools: Python, pandas, matplotlib
- Output: Floating vertical bar chart
- Additional Practice:
  - Multi-stage aggregation from hourly → daily → monthly
  - Derived metrics from daily minimum and maximum temperatures
  - Retaining contextual endpoints alongside a derived range
  - Comparing multiple visual representations of the same analytical result
  - Matplotlib floating bars, labels, ticks, and coordinate systems

## Method

1. Load the prepared hourly `weather.csv` dataset.
2. Inspect its structure and quality.
3. Filter observations to 2025.
4. Aggregate hourly temperatures to daily minimum and maximum temperatures.
5. Calculate each day's temperature range as maximum temperature minus minimum temperature.
6. Aggregate the daily results to monthly averages.
7. Visualize the monthly temperature ranges.

## Output

![Average Daily Temperature Range by Month](outputs/v3-avg_monthly-daily_temp_range-2025.svg)

The final visualization uses floating bars spanning each month's average daily minimum to average daily maximum temperature.

This preserves both the magnitude of the average daily temperature range and its position on the temperature scale.

Two earlier visualization iterations were retained during development. See [notes.md](notes.md) for the comparison and failed stacked-bar prototype.

## Reproduction

```bash
uv sync
uv run python prepare_data.py
```

Then run analysis.ipynb.

##Data & Attribution

Open-Meteo ERA5 data, licensed under CC BY 4.0. Contains modified Copernicus Climate Change Service information (2026).

Neither the European Commission nor ECMWF is responsible for any use that may be made of the Copernicus information or data contained in this project.
