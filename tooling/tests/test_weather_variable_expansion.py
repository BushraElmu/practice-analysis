"""Offline contracts for expanded weather columns and per-field corruption."""

import json
import tomllib
from pathlib import Path

import pandas as pd
import pytest

from awf import weather, sql_adapter
from awf.conditions import apply
from awf.feature_profiles import PROFILES, condition_options, parse_dirty_vars, select_target
from awf.reproducibility import verify
from awf.setup import setup
from awf.source import source_spec

ROOT = Path(__file__).resolve().parents[2]
ADDED = ("precipitation", "snowfall", "dew_point_2m", "surface_pressure")
LEGACY = ("time", "wind_speed_10m", "wind_direction_10m", "temperature_2m",
          "cloud_cover", "relative_humidity_2m", "shortwave_radiation")


def fixture_document(source):
    start, end = weather.bounds(source)
    index = pd.date_range(start, end, freq="h", inclusive="left")
    observations = {
        "wind_speed_10m": [12.0] * len(index),
        "wind_direction_10m": [180.0] * len(index),
        "temperature_2m": [-8.0] * len(index),
        "cloud_cover": [40.0] * len(index),
        "relative_humidity_2m": [60.0] * len(index),
        "shortwave_radiation": [15.0] * len(index),
        "precipitation": [0.0 if i % 4 else 1.2 for i in range(len(index))],
        "snowfall": [0.0 if i % 5 else 0.7 for i in range(len(index))],
        "dew_point_2m": [-12.0] * len(index),
        "surface_pressure": [885.0] * len(index),
    }
    return {
        "latitude": 51.0447, "longitude": -114.0719,
        "timezone": "GMT", "utc_offset_seconds": 0,
        "hourly_units": {key: PROFILES[key].unit.strip() for key in source["columns"][1:]},
        "hourly": {"time": [int(t.timestamp()) for t in index],
                   **{key: observations[key] for key in source["columns"][1:]}},
    }


def small_source():
    return {**source_spec(root=ROOT), "start_local": "2025-01-01", "end_local": "2025-01-15"}


def test_source_config_profiles_and_new_variables_agree():
    configured = source_spec(root=ROOT)
    assert tuple(configured["columns"]) == weather.COLUMNS
    assert set(PROFILES) == set(weather.COLUMNS[1:])
    assert set(ADDED).issubset(PROFILES)
    assert PROFILES["snowfall"].unit.strip() == "cm"
    assert PROFILES["precipitation"].unit.strip() == "mm"


def test_validate_and_frame_support_expanded_and_old_snapshots():
    source = small_source()
    full = weather.validate(fixture_document(source), source)
    assert tuple(full["hourly"]) == tuple(source["columns"])
    assert len(weather.to_frame(full).columns) == len(weather.COLUMNS)
    assert len(full["hourly"]["time"]) == 14 * 24

    old_source = {**source, "columns": list(LEGACY)}
    old = weather.validate(fixture_document(old_source), old_source)
    assert tuple(old["hourly"]) == LEGACY
    assert list(weather.to_frame(old)) == list(LEGACY)


def test_target_pool_selects_new_fields_and_profile_options_are_specific():
    source = small_source()
    pool = parse_dirty_vars(",".join(ADDED), source["columns"])
    assert set(pool) == set(ADDED)
    assert select_target(100_120, "json", "invalid_values", "out_of_bounds", pool) in ADDED
    for field in ADDED:
        profile = PROFILES[field]
        cfg = condition_options("invalid_values", field)
        assert (cfg["valid_min"], cfg["valid_max"]) == (profile.valid_min, profile.valid_max)
        assert cfg["outside_margin"] == profile.outside_margin
        assert condition_options("representation", field)["numeric_suffix"] == profile.unit
        assert condition_options("duplicates", field)["conflict_column"] == field


@pytest.mark.parametrize("field", ADDED)
@pytest.mark.parametrize("fmt", ("csv", "json"))
def test_new_fields_are_mutable_with_invalid_values_without_changing_other_fields(field, fmt):
    source = small_source()
    document = weather.validate(fixture_document(source), source)
    original = weather.to_frame(document) if fmt == "csv" else document
    cfg = condition_options("invalid_values", field)
    dirty, audit = apply(original, fmt=fmt, kind="invalid_values", variant="out_of_bounds",
                         seed=220, source=source, target=field, parameters=cfg)
    assert 0 < audit["affected_rows"] < len(document["hourly"]["time"])
    values = dirty[field].to_list() if fmt == "csv" else dirty["hourly"][field]
    assert any(x < cfg["valid_min"] or x > cfg["valid_max"] for x in values)
    untouched = "temperature_2m" if field != "temperature_2m" else "cloud_cover"
    other = dirty[untouched].to_list() if fmt == "csv" else dirty["hourly"][untouched]
    assert other == document["hourly"][untouched]


@pytest.mark.parametrize("field", ADDED)
def test_new_fields_support_missing_and_csv_representation(field):
    source = small_source()
    document = weather.validate(fixture_document(source), source)
    frame = weather.to_frame(document)
    missing, audit = apply(frame, fmt="csv", kind="missing_values", variant="none",
                           seed=250, source=source, target=field)
    assert missing[field].isna().sum() == audit["affected_rows"] > 0
    represented, audit = apply(frame, fmt="csv", kind="representation", variant="numeric_suffix",
                               seed=250, source=source, target=field,
                               parameters=condition_options("representation", field))
    assert represented[field].map(lambda value: isinstance(value, str)).sum() == audit["affected_rows"]
    assert any(str(x).endswith(PROFILES[field].unit) for x in represented[field])


@pytest.mark.parametrize("fmt,target", (("csv", "precipitation"), ("json", "snowfall")))
def test_setup_and_reproduce_expanded_fixture(tmp_path, fmt, target):
    tooling = tmp_path / "tooling"
    tooling.mkdir()
    for name in ("selection.toml", "weather_defaults.toml"):
        (tooling / name).write_bytes((ROOT / "tooling" / name).read_bytes())
    source = small_source()
    # Override only this fixture test's local source window, not the global defaults.
    config = (tooling / "weather_defaults.toml").read_text()
    config = config.replace('end_local = "2026-01-01"', 'end_local = "2025-01-15"')
    (tooling / "weather_defaults.toml").write_text(config)
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(fixture_document(source)))
    practice = setup(root=tmp_path, slug=f"expanded-{fmt}",
                     policy_path=tooling / "selection.toml", fmt=fmt,
                     dirty="invalid_values", variant="out_of_bounds", seed=34511,
                     dirty_vars=target, fixture=fixture, mode="weather",
                     scaffold_only=True, date_str="2026-11-05")
    manifest = tomllib.loads((practice / "practice.toml").read_text())
    assert tuple(manifest["source"]["columns"]) == weather.COLUMNS
    assert manifest["resolved"]["target"] == target
    check = verify(practice)
    assert check["reconstructed_matches"] is True
    assert check["current_matches"] is True


class _FakeColumnConnection:
    def __init__(self, columns):
        self.columns = columns

    def execute(self, query, params):
        assert "information_schema.columns" in query
        assert params == ("canonical", "weather_hourly")
        return self

    def fetchall(self):
        return [(name,) for name in self.columns]


def test_sql_schema_preflight_is_read_only_and_explicit():
    source = small_source()
    with pytest.raises(sql_adapter.SQLSetupError, match="precipitation"):
        sql_adapter._require_canonical_columns(_FakeColumnConnection(LEGACY), source)
    sql_adapter._require_canonical_columns(_FakeColumnConnection(weather.COLUMNS), source)
    old = {**source, "columns": list(LEGACY)}
    sql_adapter._require_canonical_columns(_FakeColumnConnection(LEGACY), old)


def test_expanded_sql_snapshot_uses_frozen_projection():
    source = small_source()
    source["start_local"] = "2025-01-01 00:00"
    source["end_local"] = "2025-01-01 01:00"
    row = (pd.Timestamp("2025-01-01T07:00:00Z"), 12., 180., -8., 40., 60., 15.,
           1.2, 0.7, -12., 885.)
    result = sql_adapter._document([row], source)
    assert list(result["hourly"]) == list(weather.COLUMNS)
    assert result["hourly"]["snowfall"] == [0.7]
    assert result["hourly_units"]["surface_pressure"] == "hPa"


def test_legacy_sql_snapshot_uses_frozen_projection():
    source = {**small_source(), "columns": list(LEGACY)}
    # No live PostgreSQL needed to validate the SQL rows-to-snapshot projection.
    rows = [(pd.Timestamp("2025-01-01T07:00:00Z"), 12., 180., -8., 40., 60., 15.)]
    # _document validates a full source window, so use a one-hour local interval.
    source["start_local"] = "2025-01-01 00:00"
    source["end_local"] = "2025-01-01 01:00"
    result = sql_adapter._document(rows, source)
    assert list(result["hourly"]) == list(LEGACY)
