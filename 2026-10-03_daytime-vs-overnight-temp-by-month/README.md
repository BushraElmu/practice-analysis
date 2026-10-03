# Blind Practice 7: Daytime vs Overnight Temperature by Month, Summer 2025

Calculate and visualize Calgary's average monthly daytime and overnight temperature for June, July, and August 2025, using explicit hour definitions.

## Problem

Show Calgary's average monthly daytime and overnight temperature for the Summer months of 2025, where its daytime temperature is its average temperature approximately when the sun is up and its overnight temperature is its average temperature approximately when the sun is down.

## Parameters

- Data Source: Open-Meteo Historical Weather API (ERA5)
- Data Format: CSV
- Source Grain: Hourly
- Timezone: America/Edmonton
- Output Grain: One average daytime and average overnight temperature row per month
- Tools: Python, pandas, matplotlib
- Output: 
- Additional Practice:
  - Blind First Run (Allowing pasted README template, pasted prepare_data.py, and access to pandas and matplotlib docs)
  - Adding Label to Top of First Bar Containers
  - Different Hour Ranges over time for Daytime and Overnight

## Method

1. Load the prepared hourly `weather.csv` dataset.
2. Inspect its structure and quality.
3. Filter observations to Summer 2025.
4. Seperate Daytime and Overnight hour ranges apart
5. Aggregate hourly temperatures to daytime average and overnight average temperatures.
6. Visualize the monthly daytime and overnight temperature averages.

## Output

![blind-daytime-vs-overnight-temperature-summer-2025](outputs/blind-daytime-vs-overnight-temperature-summer-2025.svg)

The final visualization uses two bars representing each month's Daytime and Overnight average temperature.

## Reproduction

```bash
uv sync
uv run python prepare_data.py
```

Then run analysis.ipynb.

##Data & Attribution

Open-Meteo ERA5 data, licensed under CC BY 4.0. Contains modified Copernicus Climate Change Service information (2026).

Neither the European Commission nor ECMWF is responsible for any use that may be made of the Copernicus information or data contained in this project.
