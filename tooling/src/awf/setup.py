"""Create a dated, independent practice from one resolved selection."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from .selection import Choice, choose, resolve_variant
from .registry import next_number, write_index
from .reproducibility import reproduction_toml, real_reproduction_toml
from .conditions import CAPABILITIES
from .sql_safety import practice_schema
from . import real

SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class SetupError(ValueError):
    """Expected, user-correctable setup failure."""


def _quote(value: str) -> str:
    # Slug and labels have constrained ASCII alphabets; JSON strings are valid TOML here.
    return json.dumps(value)


def _practice_toml(number: int, slug: str, date_str: str, choice: Choice, *,
                   variant: str | None = None, requested_variant: str | None = None,
                   variant_weights: dict[str,int] | None = None) -> str:
    fmt_weights = "\n".join(f"{k} = {v}" for k, v in choice.format_weights.items())
    cond_weights = "\n".join(f"{k} = {v}" for k, v in choice.condition_weights.items())
    return (
        "# Selection details are currently visible; this is not yet a blind exercise.\n"
        "schema_version = 1\n"
        f"title = {_quote(slug.replace('-', ' ').title())}\n"
        "status = \"in-progress\"\n"
        "family = \"unclassified\"\n"
        "featured = false\n"
        f"id = {number}\n"
        f"slug = {_quote(slug)}\n"
        f"date = {_quote(date_str)}\n"
        "\n[requested]\n"
        f"format = {_quote(choice.requested_format)}\n"
        f"dirty = {_quote(choice.requested_dirty)}\n"
        f"seed_source = {_quote(choice.seed_source)}\n"
        + (f'requested_variant = {_quote(requested_variant)}\n' if requested_variant is not None else '')
        + "\n[resolved]\n"
        + f"format = {_quote(choice.format)}\n"
        + f"dirty = {_quote(choice.dirty)}\n"
        + f'seed = "{choice.seed:06d}"\n'
        + (f'variant = {_quote(variant)}\n' if variant is not None else '')
        + "\n[selection_snapshot.formats]\n"
        + f"{fmt_weights}\n"
        + f"\n[selection_snapshot.conditions.{choice.format}]\n"
        + f"{cond_weights}\n"
        + (
            f'\n[selection_snapshot.variants.{choice.dirty}]\n'
            + ''.join(f'{k} = {v}\n' for k,v in variant_weights.items())
            if variant_weights else ''
        )
    )


def _script_prepare(fmt: str) -> str:
    return f'''"""Mock preparation: intentionally writes a one-row CSV for ANY format label."""
import csv
from pathlib import Path

FORMAT = {fmt!r}
ROOT = Path(__file__).resolve().parent


def main():
    destination = ROOT / "data" / "weather.csv"
    destination.parent.mkdir(exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["format"])
        writer.writerow([FORMAT])
    print(f"Prepared {{destination}}")


if __name__ == "__main__":
    main()
'''


def _script_dirty(dirty: str) -> str:
    return f'''"""Mock corruption: adds a condition label, NOT actual data corruption."""
import csv
from pathlib import Path

CONDITION = {dirty!r}
ROOT = Path(__file__).resolve().parent


def main():
    destination = ROOT / "data" / "weather.csv"
    with destination.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames not in (["format"], ["format", "dirty"]):
            raise ValueError("Run prepare_data.py first; unexpected CSV structure")
        rows = list(reader)
    if len(rows) != 1:
        raise ValueError("Expected exactly one mock source row")
    rows[0]["dirty"] = CONDITION
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["format", "dirty"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Labeled {{destination}}")


if __name__ == "__main__":
    main()
'''


def _script_wrapper(operation: str) -> str:
    return f'''"""Thin wrapper: preparation logic lives in the independent tooling project."""
from pathlib import Path
import subprocess
import sys

PRACTICE = Path(__file__).resolve().parent
REPOSITORY = PRACTICE.parent

if __name__ == "__main__":
    subprocess.run(["uv", "run", "--no-editable", "--project", str(REPOSITORY / "tooling"),
                    "awf", "step", str(PRACTICE), "{operation}", *sys.argv[1:]], check=True)
'''


def _real_practice_addendum(start: str, end: str, *, condition: str,
                            defaults_path: Path | None = None) -> str:
    """Render a frozen source specification and the existing weather-specific defaults."""
    from .source import source_spec
    source = source_spec(start, end, config_path=defaults_path)
    options = {
        'target': 'temperature_2m' if condition == 'invalid_values' else 'wind_speed_10m',
        'min_rate': 0.01, 'max_rate': 0.03,
        'placement': 'mixed', 'groups': 3, 'grouped_share': 0.70,
    }
    if condition == 'duplicates':
        options.update(conflict_column='temperature_2m', conflict_offset=1.5, conflict_share=0.50)
    elif condition == 'invalid_values':
        options.update(valid_min=-60.0, valid_max=60.0, outside_margin=10.0,
                       scale_factor=2.0, bounds_share=0.50)
    elif condition == 'representation':
        options.update(numeric_suffix=' km/h', sentinel='not_recorded')

    def literal(value):
        if isinstance(value, str):
            return _quote(value)
        if isinstance(value, list):
            return '[' + ', '.join(literal(v) for v in value) + ']'
        return str(value)

    return ('\n[source]\n' + ''.join(f'{k} = {literal(v)}\n' for k, v in source.items())
            + '\n[condition_settings]\n'
            + ''.join(f'{k} = {literal(v)}\n' for k, v in options.items()))


def _analysis_notebook() -> str:
    cell = lambda heading: {
        "id": heading.lower(),
        "cell_type": "code", "execution_count": None, "metadata": {},
        "outputs": [], "source": [f"# {heading}\n"],
    }
    notebook = {
        "cells": [cell(s) for s in ("Imports", "Load", "Inspect", "Clean", "Transform", "Visualize")],
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                     "language_info": {"name": "python"}},
        "nbformat": 4, "nbformat_minor": 5,
    }
    return json.dumps(notebook, indent=2) + "\n"


def _pyproject(slug: str, fmt: str) -> str:
    deps = ['"pandas>=2.2"', '"requests>=2.31"', '"matplotlib>=3.8"']
    if fmt == "parquet":
        deps.append('"pyarrow>=16"')
    if fmt == 'sql':
        deps.append('"psycopg[binary]>=3.2"')
    return (
        "[project]\n"
        f"name = {_quote(slug)}\n"
        'version = "0.1.0"\n'
        'requires-python = ">=3.11"\n'
        f"dependencies = [{', '.join(deps)}]\n\n"
        '[dependency-groups]\n'
        'dev = ["ipykernel>=6"]\n'
    )


def _run(argv: list[str], *, cwd: Path) -> None:
    try:
        subprocess.run(argv, cwd=cwd, check=True)
    except FileNotFoundError as exc:
        raise SetupError(f"Required tool not installed: {argv[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise SetupError(f"Command failed (exit {exc.returncode}): {' '.join(argv)}") from exc


def setup(
    *, root: Path, slug: str, policy_path: Path, fmt: str = "random",
    dirty: str = "random", seed: int | None = None,
    variant: str = "random",
    number: int | None = None, date_str: str | None = None,
    scaffold_only: bool = False, mode: str = "weather",
    fixture: Path | None = None, blind: bool = False, start_local: str | None = None,
    end_local: str | None = None,
) -> Path:
    root = root.resolve()
    if not SLUG.fullmatch(slug):
        raise SetupError("Practice name must be lowercase letters/numbers separated by hyphens")
    date_str = date_str or datetime.now().astimezone().date().isoformat()
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as exc:
        raise SetupError("Date must be YYYY-MM-DD") from exc
    destination = root / f"{date_str}_{slug}"
    if destination.exists():
        raise SetupError(f"Practice directory already exists: {destination}")
    number = next_number(root, number)
    if mode not in ("mock", "weather"):
        raise SetupError("mode must be 'mock' or 'weather'")
    if mode == "mock" and blind:
        raise SetupError("--blind requires real weather data")
    if mode == "mock" and variant != "random":
        raise SetupError("--variant applies only to real weather preparation")
    if variant != "random" and dirty == "random":
        raise SetupError("Specify --dirty when explicitly selecting --variant")
    capabilities = None
    if mode == "weather":
        from .source import source_spec
        source = source_spec(start_local, end_local,
                             config_path=policy_path.parent / "weather_defaults.toml")
        start_local, end_local = source['start_local'], source['end_local']
        # A format is eligible only if its real adapter can run in this environment.
        import importlib.util
        from . import sql_adapter
        capabilities = {name: set(conditions) for name, conditions in CAPABILITIES.items()
                        if ((name != "parquet" or importlib.util.find_spec("pyarrow") is not None)
                            and (name != "sql" or sql_adapter.available()))}
        if fmt == 'sql' and 'sql' not in capabilities:
            raise SetupError('SQL unavailable: connection missing or unsafe. Check AWF_DATABASE_URL and the restricted practice role (just doctor).')
        if blind and mode != 'weather':
            raise SetupError('--blind is only available for real-weather practices')
    choice = choose(policy_path, requested_format=fmt, requested_dirty=dirty,
                    seed=seed, capabilities=capabilities)
    selected_variant, variant_weights = (
        resolve_variant(policy_path, condition=choice.dirty, fmt=choice.format,
                        seed=choice.seed, requested_variant=variant)
        if mode == "weather" else (None, {})
    )

    # Before publishing a SQL folder, establish the selected interval really
    # exists in canonical. SQL build checks again inside its transaction.
    if mode == 'weather' and choice.format == 'sql':
        from . import sql_adapter
        sql_adapter.require_coverage(source)

    # Stage source files first. No .venv is created until after moving to the
    # final path: venv scripts may contain absolute environment paths.
    with tempfile.TemporaryDirectory(prefix=".awf-staging-", dir=root) as temp:
        staged = Path(temp) / destination.name
        staged.mkdir()
        (staged / "data").mkdir()
        (staged / "outputs").mkdir()
        (staged / "data" / ".gitkeep").touch()
        (staged / "outputs" / ".gitkeep").touch()
        (staged / "prepare_data.py").write_text(
            _script_prepare(choice.format) if mode == "mock" else _script_wrapper("prepare"), encoding="utf-8")
        (staged / "dirty_data.py").write_text(
            _script_dirty(choice.dirty) if mode == "mock" else _script_wrapper("dirty"), encoding="utf-8")
        (staged / "practice.toml").write_text(
            _practice_toml(number, slug, date_str, choice,
                           variant=selected_variant,
                           requested_variant=variant if mode == "weather" else None,
                           variant_weights=variant_weights) +
            (_real_practice_addendum(start_local, end_local, condition=choice.dirty,
                                         defaults_path=policy_path.parent / "weather_defaults.toml")
             if mode == "weather" else ""), encoding="utf-8")
        if mode == "weather":
            with (staged / "practice.toml").open("a", encoding="utf-8") as manifest:
                manifest.write("\n[source_provenance]\n")
                manifest.write('acquisition = "postgresql-canonical"\n' if choice.format == 'sql'
                               else 'acquisition = "synthetic-fixture"\n' if fixture is not None
                               else 'acquisition = "open-meteo-api"\n')
        (staged / "pyproject.toml").write_text(_pyproject(slug, choice.format), encoding="utf-8")
        (staged / "analysis.ipynb").write_text(_analysis_notebook(), encoding="utf-8")
        if mode == "mock":
            (staged / "README.md").write_text(
                f"# Practice {number}: {slug.replace('-', ' ').title()}\n\n"
                "Stage 2 **mock** only. Run prepare_data.py then dirty_data.py.\n",
                encoding="utf-8")
            _run([sys.executable, "prepare_data.py"], cwd=staged)
            _run([sys.executable, "dirty_data.py"], cwd=staged)
            repro = reproduction_toml(staged)
        else:
            (staged / "README.md").write_text(
                f"# Practice {number}: {slug.replace('-', ' ').title()}\n\n"
                + ("SYNTHETIC test fixture (not observed Open-Meteo weather).\n" if fixture is not None
                 else "Open-Meteo ERA5 preparation (source window frozen below).\n")
                + f"Source local window: {start_local} to {end_local} exclusive.\n"
                + (f"Interface: {choice.format}. Condition: undisclosed.\n\n"
                   if blind else f"Interface: {choice.format}. Condition: {choice.dirty}. Variant: {selected_variant}.\n\n")
                + (f"SQL data: {practice_schema(number)}.weather_hourly \n"
                   "SQL prepare_data.py refuses to overwrite existing schema; dirty_data.py refuses edited tables. \n"
                   "Use `just verify N` for non-destructive reproduction.\n"
                   if choice.format == 'sql' else
                   "Run prepare_data.py then dirty_data.py to rebuild from the local "
                   "clean snapshot; `just verify N` checks reproduction without overwriting.\n")
                + "The clean snapshot and generated dataset (if file-backed) are ignored by Git. "
                "Without a preserved snapshot, a later reconstruction may differ.\n",
                encoding="utf-8")
            if choice.format == 'sql':
                if fixture is not None:
                    raise SetupError('--fixture does not seed canonical SQL data; run `just sql-sync --fixture ...` explicitly')
                # Defer persistent SQL creation until after environment setup.
            else:
                real.prepare(staged, fixture=fixture)
                real.dirty(staged)
                repro = real_reproduction_toml(staged, choice.format)
        if mode == 'mock' or (mode == 'weather' and choice.format != 'sql'):
            with (staged / 'practice.toml').open('a', encoding='utf-8') as manifest:
                manifest.write(repro)
            if mode == 'weather' and blind:
                from .blind import conceal
                conceal(staged)
            if mode == 'weather':
                from .reproducibility import verify
                check = verify(staged)
                if not check['reconstructed_matches']:
                    raise SetupError('Initial weather reconstruction did not match')
        staged.rename(destination)

    sql_attempted = False
    try:
        if not scaffold_only:
            # Create the environment in its FINAL location, before SQL writes.
            _run(['uv', 'sync', '--project', str(destination)], cwd=root)
        if mode == 'weather' and choice.format == 'sql':
            from . import sql_adapter
            sql_attempted = True
            sql_result = sql_adapter.build(destination)
            repro = real_reproduction_toml(destination, choice.format,
                                           sql_output_sha256=sql_result['output_sha256'])
            with (destination / 'practice.toml').open('a', encoding='utf-8') as manifest:
                manifest.write(repro)
            if blind:
                from .blind import conceal
                conceal(destination)
            from .reproducibility import verify
            check = verify(destination)
            if not check['reconstructed_matches']:
                raise SetupError('Initial SQL reconstruction did not match')
        write_index(root)
    except Exception as exc:
        if sql_attempted:
            # SQL might have committed: preserve evidence, never drop the schema.
            marker = destination / '.awf' / 'SETUP_INCOMPLETE'
            marker.parent.mkdir(exist_ok=True)
            marker.write_text(
                f'Setup failed after SQL build was attempted: {type(exc).__name__}: {exc}\n'
                f'Inspect {practice_schema(number)} before deciding how to recover.\n',
                encoding='utf-8')
            try:
                write_index(root)
            except Exception:
                pass
            raise SetupError(f'SQL setup incomplete: {exc}. Preserved {destination} and '
                             f'possible PostgreSQL schema {practice_schema(number)}; '
                             'nothing was dropped. Inspect .awf/SETUP_INCOMPLETE.') from exc
        shutil.rmtree(destination)
        raise
    return destination
