"""Prepare JSON weather data for Practice #.

Downloads the required Open-Meteo history, validates exact hourly coverage,
and writes a source-shaped analyst-facing document to data/weather.json.
"""

from pathlib import Path
import json
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

COLUMNS = ["time", *HOURLY_VARIABLES]

START_LOCAL = pd.Timestamp(
    "2000-01-01 00:00",
    tz=LOCAL_TIMEZONE,
)

END_LOCAL = pd.Timestamp(
    "2026-10-01 00:00",
    tz=LOCAL_TIMEZONE,
)

REQUEST_DELAY_SECONDS = 4

DATA_PATH = Path("data") / "weather.json"


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

    payload = response.json()

    metadata = {
        "latitude": payload["latitude"],
        "longitude": payload["longitude"],
        "elevation": payload["elevation"],
        "timezone": payload["timezone"],
        "utc_offset_seconds": (
            payload["utc_offset_seconds"]
        ),
        "hourly_units": payload["hourly_units"],
    }

    hourly = payload["hourly"]

    time_index = pd.to_datetime(
        hourly["time"],
        unit="s",
        utc=True,
    )

    keep = (
        (time_index >= start_utc)
        & (time_index < end_utc)
    )

    hourly = {
        key: [
            value
            for value, include
            in zip(values, keep)
            if include
        ]
        for key, values in hourly.items()
    }

    return metadata, hourly


def fetch_weather(start_local, end_local):
    batches = create_year_batches(
        start_local,
        end_local,
    )

    metadata = None

    hourly = {
        column: []
        for column in COLUMNS
    }

    for i, (batch_start, batch_end) in enumerate(
        batches,
        start=1,
    ):
        print(
            f"Request {i}/{len(batches)}: "
            f"{batch_start} -> {batch_end}"
        )

        batch_metadata, batch_hourly = (
            fetch_weather_batch(
                batch_start,
                batch_end,
            )
        )

        if metadata is None:
            metadata = batch_metadata
        elif batch_metadata != metadata:
            raise ValueError(
                "Source metadata changed across batches."
            )

        for column in COLUMNS:
            hourly[column].extend(
                batch_hourly[column]
            )

        if i < len(batches):
            time.sleep(REQUEST_DELAY_SECONDS)

    if metadata is None:
        raise ValueError(
            "No weather batches were fetched."
        )

    expected_time = pd.date_range(
        start=start_local.tz_convert("UTC"),
        end=end_local.tz_convert("UTC"),
        freq="h",
        inclusive="left",
    )

    actual_time = pd.DatetimeIndex(
        pd.to_datetime(
            hourly["time"],
            unit="s",
            utc=True,
        )
    )

    if not actual_time.equals(expected_time):
        raise ValueError(
            "Unexpected hourly coverage."
        )

    if {
        len(values)
        for values in hourly.values()
    } != {len(expected_time)}:
        raise ValueError(
            "Unexpected hourly array lengths."
        )

    return {
        **metadata,
        "hourly": hourly,
    }


def main():
    weather = fetch_weather(
        START_LOCAL,
        END_LOCAL,
    )

    DATA_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with DATA_PATH.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            weather,
            file,
            allow_nan=False,
        )

    print(
        f"Rows: "
        f"{len(weather['hourly']['time']):,}"
    )
    print(f"Saved: {DATA_PATH}")


if __name__ == "__main__":
    main()
