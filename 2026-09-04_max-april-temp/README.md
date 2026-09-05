# Practice 3: Max April Temperature, 2024-2026

Calculate and visualize Calgary's max April temperature for 2024-2026 using the ERA5 historical Open-Meteo API Weather Data.

## Problem

Show the max April temperature in Calgary for 2024-2026.

## Parameters

- Data Source: Open-Meteo Historical Weather API (ERA5)
- Data Format: CSV
- Source Grain: Hourly
- Timezone: America/Edmonton
- Output Grain: One maximum hourly April temperature per year
- Tools: Python, pandas, matplotlib
- Output: Vertical bar chart
- Additional Practice:
  - Blind first run, excluding `prepare_data.py`, visualization, and attribution

## Method

1. Load the prepared hourly weather.csv dataset.
2. Inspect its structure and quality.
3. Filter observations to April 2024–2026.
4. Aggregate hourly temperature to one maximum per year.
5. Visualize the resulting yearly averages.

## Outputs

![Max April Temperature](outputs/max_april_temp.png)

## Reproduction

```bash
uv sync
uv run python prepare_data.py
```

Then run analysis.ipynb.

## Data & Attribution

[Open-Meteo](https://open-meteo.com/) ERA5 data, licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Contains modified Copernicus Climate Change Service information (2026).

Neither the European Commission nor ECMWF is responsible for any use that may be made of the Copernicus information or data contained in this project.