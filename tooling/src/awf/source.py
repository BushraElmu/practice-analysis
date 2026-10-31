"""Load source settings from the checkout, independent of Python installation location.

The `awf` package is installed into site-packages in ordinary (non-editable)
installs. The configuration belongs to the repository, not to the wheel.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from . import weather


def defaults_path(*, root: Path | None = None, config_path: Path | None = None) -> Path:
    """Find the checked-out source configuration; never assume `__file__` is in `tooling/src`."""
    if config_path is not None:
        candidate = Path(config_path).resolve()
    elif root is not None:
        candidate = Path(root).resolve() / "tooling" / "weather_defaults.toml"
    else:
        candidate = None
        for directory in (Path.cwd().resolve(), *Path.cwd().resolve().parents):
            path = directory / "tooling" / "weather_defaults.toml"
            if path.is_file():
                candidate = path
                break
        if candidate is None:
            raise FileNotFoundError(
                "Cannot locate tooling/weather_defaults.toml. "
                "Run AWF from inside the repository or supply an explicit configuration path."
            )
    if not candidate.is_file():
        raise FileNotFoundError(f"Weather configuration not found: {candidate}")
    return candidate


def source_spec(start: str | None = None, end: str | None = None, *,
                root: Path | None = None, config_path: Path | None = None) -> dict:
    with defaults_path(root=root, config_path=config_path).open("rb") as handle:
        source = tomllib.load(handle)["source"]
    if start is not None:
        source["start_local"] = start
    if end is not None:
        source["end_local"] = end
    if tuple(source["columns"]) != weather.COLUMNS:
        raise ValueError("Current weather and SQL adapters require the configured standard weather columns")
    weather.bounds(source)
    return source
