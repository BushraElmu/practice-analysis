# Imports
from datetime import date
from pathlib import Path
import time

import pandas as pd
import requests

# Source configuration
URL = "https://archive-api.open-meteo.com/v1/archive"

HOURLY_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "precipitation",
    "rain",
    "snowfall",
    "weather_code",
    "cloud_cover",
    "pressure_msl",
    "surface_pressure",
    "wind_speed_10m",
    "wind_direction_10m",
    "shortwave_radiation",
    "vapour_pressure_deficit",
    "soil_temperature_0_to_7cm",
]

BASE_PARAMS = {
    "latitude": 51.0447,
    "longitude": -114.0719,
    "hourly": ",".join(HOURLY_VARIABLES),
    "models": "era5",
    "timezone": "America/Edmonton",
}

START_DATE = "2000-01-01"
END_DATE = "2026-07-31"

BATCH_SLEEP_SECONDS = 4

# Batching
def create_year_batches(start_date, end_date):
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)

    batches = []

    for year in range(start.year, end.year + 1):
        batch_start = max(start, date(year, 1, 1))
        batch_end = min(end, date(year, 12, 31))

        batches.append(
            (batch_start.isoformat(), batch_end.isoformat())
        )

    return batches


batches = create_year_batches(START_DATE, END_DATE)

# Acquisition
frames = []
api_requests = 0

for i, (start_date, end_date) in enumerate(batches, start=1):
    batch_params = BASE_PARAMS.copy()

    batch_params["start_date"] = start_date
    batch_params["end_date"] = end_date

    print(
        f"Request {i}/{len(batches)}: "
        f"{start_date} to {end_date}"
    )

    response = requests.get(
        URL,
        params=batch_params,
        timeout=60,
    )

    api_requests += 1

    if response.status_code == 429:
        raise RuntimeError(
            f"Rate limit reached on request {i}/{len(batches)}."
        )

    response.raise_for_status()

    batch_data = response.json()

    batch_df = pd.DataFrame(batch_data["hourly"])
    batch_df["time"] = pd.to_datetime(batch_df["time"])

    frames.append(batch_df)

    if i < len(batches):
        time.sleep(BATCH_SLEEP_SECONDS)

# Canonical DataFrame
df = pd.concat(frames, ignore_index=True)

print(f"HTTP requests made: {api_requests}")
print(f"Rows acquired: {len(df):,}")
print(f"Range: {df['time'].min()} to {df['time'].max()}")

# CSV output
DATA_PATH = Path("data") / "weather.csv"

df.to_csv(DATA_PATH, index=False)