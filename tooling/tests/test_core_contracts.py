"""Small offline regression baseline for committed AWF tooling.

These tests do not contact Open-Meteo or PostgreSQL and do not need private
practice records. They are deliberately not a substitute for full acceptance.
"""

import tomllib
from pathlib import Path

import pytest

import awf

from awf.feature_profiles import parse_dirty_vars, select_target
from awf.registry import catalog, render_index
from awf.reproducibility import verify
from awf.selection import choose
from awf.setup import SetupError, setup
from awf.weather import COLUMNS

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "tooling" / "selection.toml"


def test_committed_registry_matches_source_manifests():
    expected = render_index(catalog(ROOT))
    assert (ROOT / "practices.toml").read_text(encoding="utf-8") == expected, (
        "practices.toml is stale; regenerate it with 'just index'"
    )


def test_seeded_selection_is_repeatable():
    first = choose(POLICY, requested_format="json", seed=597400)
    second = choose(POLICY, requested_format="json", seed=597400)
    assert first == second
    assert first.format == "json"


def test_dirty_vars_are_an_or_pool():
    pool = parse_dirty_vars("wind_speed_10m, wind_direction_10m", list(COLUMNS))
    assert pool == ("wind_speed_10m", "wind_direction_10m")

    chosen = select_target(597400, "json", "duplicates", "conflicting", pool)
    assert chosen in pool
    assert chosen == select_target(597400, "json", "duplicates", "conflicting", pool)


def test_dirty_vars_reject_unknown_and_repeated_fields():
    with pytest.raises(ValueError, match="Unsupported/unavailable"):
        parse_dirty_vars("not_a_weather_field", list(COLUMNS))
    with pytest.raises(ValueError, match="duplicate field"):
        parse_dirty_vars("wind_speed_10m,wind_speed_10m", list(COLUMNS))


def test_exact_duplicates_do_not_select_field_target():
    pool = ("wind_speed_10m", "wind_direction_10m")
    assert select_target(42, "json", "duplicates", "exact", pool) is None


def test_mock_setup_verifies_without_overwriting_analyst_edits(tmp_path):
    (tmp_path / "tooling").mkdir()
    (tmp_path / "tooling" / "selection.toml").write_bytes(POLICY.read_bytes())

    practice = setup(
        root=tmp_path,
        slug="contract-smoke",
        policy_path=tmp_path / "tooling" / "selection.toml",
        fmt="csv", dirty="duplicates", seed=42,
        mode="mock", scaffold_only=True,
        date_str="2026-11-04",
    )

    original = verify(practice)
    assert original["reconstructed_matches"] is True
    assert original["current_matches"] is True

    analyst_file = practice / "data" / "weather.csv"
    analyst_file.write_text("analyst changed this file\n", encoding="utf-8")
    result = verify(practice)
    assert result["reconstructed_matches"] is True
    assert result["current_matches"] is False
    assert analyst_file.read_text(encoding="utf-8") == "analyst changed this file\n"


def test_mock_scaffold_keeps_required_directories_without_placeholders(tmp_path):
    (tmp_path / "tooling").mkdir()
    (tmp_path / "tooling" / "selection.toml").write_bytes(POLICY.read_bytes())

    practice = setup(
        root=tmp_path,
        slug="directory-contract",
        policy_path=tmp_path / "tooling" / "selection.toml",
        fmt="json", dirty="none", seed=22,
        mode="mock", scaffold_only=True,
        date_str="2026-11-04",
    )

    for folder in ("data", "outputs"):
        assert (practice / folder).is_dir()
        assert not (practice / folder / ".gitkeep").exists()

    assert (practice / "data" / "weather.csv").is_file()
    assert verify(practice)["reconstructed_matches"] is True


def test_package_version_matches_project_metadata():
    project = tomllib.loads((ROOT / "tooling" / "pyproject.toml").read_text(encoding="utf-8"))
    assert awf.__version__ == project["project"]["version"]


def test_setup_rejects_unsafe_slug_without_writing_files(tmp_path):
    (tmp_path / "tooling").mkdir()
    (tmp_path / "tooling" / "selection.toml").write_bytes(POLICY.read_bytes())

    with pytest.raises(SetupError, match="lowercase"):
        setup(
            root=tmp_path,
            slug="../escape",
            policy_path=tmp_path / "tooling" / "selection.toml",
            mode="mock", scaffold_only=True,
        )
    assert list(tmp_path.iterdir()) == [tmp_path / "tooling"]
