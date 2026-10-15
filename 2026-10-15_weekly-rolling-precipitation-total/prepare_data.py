"""Prepare CSV weather data for Practice #.

Downloads the required Open-Meteo history, validates exact hourly coverage,
and writes the reproducible analyst-facing dataset to data/weather.csv.
"""

from pathlib import Path
import time

import pandas as pd
import requests


# Configuration
URL = "https://archive-api.open-meteo.com/v1/archive"

LATITUDE = 51.0447
LONGITUDE = -114.0719
MODEL = "era5"
LOCAL_TIMEZONE = "America/Edmonton"

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

START_LOCAL = pd.Timestamp(
    "2000-01-01 00:00",
    tz=LOCAL_TIMEZONE,
)

END_LOCAL = pd.Timestamp(
    "2026-10-01 00:00",
    tz=LOCAL_TIMEZONE,
)

REQUEST_DELAY_SECONDS = 4

DATA_PATH = Path("data") / "weather.csv"


def create_year_batches(start_local, end_local):
    batches = []
    batch_start = start_local

    while batch_start < end_local:
        next_year = pd.Timestamp(
            year=batch_start.year + 1,
            month=1,
            day=1,
            tz=batch_start.tz,
        )

        batch_end = min(next_year, end_local)
        batches.append((batch_start, batch_end))
        batch_start = batch_end

    return batches


def fetch_weather_batch(start_local, end_local):
    start_utc = start_local.tz_convert("UTC")
    end_utc = end_local.tz_convert("UTC")

    # API accepts dates, so request the UTC envelope then trim.
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "start_date": start_utc.date().isoformat(),
        "end_date": (
            end_utc - pd.Timedelta(hours=1)
        ).date().isoformat(),
        "hourly": ",".join(HOURLY_VARIABLES),
        "models": MODEL,
        "timezone": "GMT",
        "timeformat": "unixtime",
    }

    response = requests.get(
        URL,
        params=params,
        timeout=60,
    )
    response.raise_for_status()

    batch_df = pd.DataFrame(
        response.json()["hourly"]
    )

    batch_df["time"] = pd.to_datetime(
        batch_df["time"],
        unit="s",
        utc=True,
    )

    return (
        batch_df.loc[
            batch_df["time"].ge(start_utc)
            & batch_df["time"].lt(end_utc),
            ["time", *HOURLY_VARIABLES],
        ]
        .reset_index(drop=True)
    )


def fetch_weather(start_local, end_local):
    batches = create_year_batches(
        start_local,
        end_local,
    )

    frames = []

    for i, (batch_start, batch_end) in enumerate(
        batches,
        start=1,
    ):
        print(
            f"Request {i}/{len(batches)}: "
            f"{batch_start} -> {batch_end}"
        )

        frames.append(
            fetch_weather_batch(
                batch_start,
                batch_end,
            )
        )

        if i < len(batches):
            time.sleep(REQUEST_DELAY_SECONDS)

    df = pd.concat(
        frames,
        ignore_index=True,
    )

    # Catch gaps, overlaps, boundary errors, and unexpected hours.
    expected_time = pd.date_range(
        start=start_local.tz_convert("UTC"),
        end=end_local.tz_convert("UTC"),
        freq="h",
        inclusive="left",
    )

    if not pd.DatetimeIndex(
        df["time"]
    ).equals(expected_time):
        raise ValueError(
            "Unexpected hourly coverage."
        )

    return df


def main():
    df = fetch_weather(
        START_LOCAL,
        END_LOCAL,
    )

    DATA_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    df.to_csv(
        DATA_PATH,
        index=False,
    )

    print(f"Rows: {len(df):,}")
    print(
        f"Range: {df['time'].min()} "
        f"to {df['time'].max()}"
    )
    print(f"Saved: {DATA_PATH}")


if __name__ == "__main__":
    main()
