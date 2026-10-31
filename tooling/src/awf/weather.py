"""Open-Meteo ERA5 source adapter; based on the archive's prepare_data notebooks.

A narrow development implementation: source-shaped hourly JSON is the immutable
input; CSV, JSON and Parquet are analyst-facing storage representations.
"""
from __future__ import annotations

import json
import os
import tempfile
import time as clock
from pathlib import Path

import pandas as pd
import requests

API = "https://archive-api.open-meteo.com/v1/archive"
TIMEZONE = "America/Edmonton"
COLUMNS = ("time", "wind_speed_10m", "wind_direction_10m", "temperature_2m",
           "cloud_cover", "relative_humidity_2m", "shortwave_radiation")


def bounds(source: dict) -> tuple[pd.Timestamp, pd.Timestamp]:
    zone = source.get("timezone", TIMEZONE)
    start = pd.Timestamp(source["start_local"])
    end = pd.Timestamp(source["end_local"])
    start = start.tz_localize(zone) if start.tzinfo is None else start
    end = end.tz_localize(zone) if end.tzinfo is None else end
    start, end = start.tz_convert("UTC"), end.tz_convert("UTC")
    if start >= end:
        raise ValueError("Source start must precede end")
    return start, end


def validate(payload: dict, source: dict) -> dict:
    """Trim a source-shaped document to exact UTC hourly coverage."""
    times = payload.get("hourly", {}).get("time")
    if not isinstance(times, list):
        raise ValueError("Expected Open-Meteo hourly.time array")
    hourly = payload["hourly"]
    requested = source["columns"]
    if "time" not in requested or len(set(requested)) != len(requested):
        raise ValueError("Source columns need unique time column")
    if not set(requested).issubset(hourly):
        raise ValueError(f"Missing requested hourly fields: {set(requested)-set(hourly)}")
    if any(len(hourly[column]) != len(times) for column in requested):
        raise ValueError("Hourly arrays do not align")
    index = pd.to_datetime(times, unit="s", utc=True)
    start, end = bounds(source)
    mask = (index >= start) & (index < end)
    retained = {col: [v for v, include in zip(hourly[col], mask) if include]
                for col in requested}
    expected = pd.date_range(start, end, freq="h", inclusive="left")
    actual = pd.to_datetime(retained["time"], unit="s", utc=True)
    if not pd.DatetimeIndex(actual).equals(expected):
        raise ValueError(f"Incomplete or duplicated hourly coverage; expected {len(expected)} hours, got {len(actual)}")
    return {"latitude": payload.get("latitude"), "longitude": payload.get("longitude"),
            "elevation": payload.get("elevation"), "timezone": payload.get("timezone", "GMT"),
            "utc_offset_seconds": payload.get("utc_offset_seconds", 0),
            "hourly_units": {k: payload.get("hourly_units", {}).get(k, "") for k in requested},
            "hourly": retained}


def _batches(source: dict):
    """Use bounded calendar-year batches, matching the archive's acquisition strategy."""
    start, end = bounds(source)
    cursor = start
    while cursor < end:
        year_end = pd.Timestamp(year=cursor.year+1, month=1, day=1, tz="UTC")
        next_cursor = min(year_end, end)
        yield cursor, next_cursor
        cursor = next_cursor


def acquire(source: dict, *, fixture: Path | None = None) -> dict:
    """Acquire source-shaped API payload. Fixture is for offline integration tests."""
    if fixture is not None:
        return validate(json.loads(Path(fixture).read_text(encoding="utf-8")), source)
    batches = list(_batches(source))
    merged: dict | None = None
    for n, (start, end) in enumerate(batches):
        params = {
            "latitude": source["latitude"], "longitude": source["longitude"],
            "start_date": start.date().isoformat(),
            "end_date": (end-pd.Timedelta(hours=1)).date().isoformat(),
            "hourly": ",".join(col for col in source["columns"] if col != "time"),
            "models": "era5", "timezone": "GMT", "timeformat": "unixtime",
        }
        response = requests.get(API, params=params, timeout=60)
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            raise ValueError(f"Open-Meteo rejected request: {payload.get('reason', payload['error'])}")
        batch_source = {**source, "timezone": "UTC", "start_local": str(start), "end_local": str(end)}
        # Validate each batch separately, including expected exact timestamp coverage.
        cleaned = validate(payload, batch_source)
        if merged is None:
            merged = cleaned
        else:
            if {k:v for k,v in merged.items() if k!="hourly"} != {k:v for k,v in cleaned.items() if k!="hourly"}:
                raise ValueError("Source metadata changed between API batches")
            for column in source["columns"]:
                merged["hourly"][column].extend(cleaned["hourly"][column])
        if n < len(batches)-1:
            clock.sleep(4)
    if merged is None:
        raise ValueError("No weather data acquired")
    return validate(merged, source)


def to_frame(document: dict) -> pd.DataFrame:
    df = pd.DataFrame(document["hourly"])
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df


def write_snapshot(document: dict, path: Path) -> None:
    """Atomically publish a clean source snapshot with canonical JSON bytes."""
    write_data(document, 'json', path)


def write(document: dict, fmt: str, path: Path) -> None:
    write_data(document if fmt == 'json' else to_frame(document), fmt, path)


def read(fmt: str, path: Path):
    if fmt == "json":
        return json.loads(path.read_text(encoding="utf-8"))
    if fmt == "csv":
        return pd.read_csv(path)
    if fmt == "parquet":
        return pd.read_parquet(path)
    raise ValueError(f"No file adapter for {fmt}")


def write_data(value, fmt: str, path: Path) -> None:
    """Serialize into a sibling temporary file; atomically publish on success."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.awf-write-', suffix=path.suffix, dir=path.parent)
    os.close(descriptor)
    draft = Path(temporary)
    try:
        if fmt == 'json':
            draft.write_text(json.dumps(value, allow_nan=False, separators=(',', ':')), encoding='utf-8')
        elif fmt == 'csv':
            value.to_csv(draft, index=False)
        elif fmt == 'parquet':
            value.to_parquet(draft, index=False, engine='pyarrow')
        else:
            raise ValueError(f'No file adapter for {fmt}')
        os.replace(draft, path)
    finally:
        draft.unlink(missing_ok=True)
