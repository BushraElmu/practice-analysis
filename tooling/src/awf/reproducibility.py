"""Record and independently reconstruct mock, weather-file and SQL practices."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


class ReproductionError(ValueError):
    """Missing or invalid reproduction information."""


FILES = ("prepare_data.py", "dirty_data.py")
MOCK_OUTPUT = Path("data/weather.csv")
METHOD = "mock-csv-two-column-v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def hashes(directory: Path) -> dict[str, str]:
    """Capture the scripts and prepared dataset after successful mock execution."""
    return {
        "prepare_sha256": sha256(directory / FILES[0]),
        "dirty_sha256": sha256(directory / FILES[1]),
        "output_sha256": sha256(directory / MOCK_OUTPUT),
    }


def reproduction_toml(directory: Path) -> str:
    values = hashes(directory)
    return (
        "\n[reproduction]\n"
        f'method = "{METHOD}"\n'
        f'prepare_sha256 = "{values["prepare_sha256"]}"\n'
        f'dirty_sha256 = "{values["dirty_sha256"]}"\n'
        f'output_sha256 = "{values["output_sha256"]}"\n'
    )


def real_reproduction_toml(directory: Path, fmt: str, *, sql_output_sha256: str | None = None) -> str:
    """Freeze source snapshot, output, and generated wrappers for real weather."""
    from . import real
    values = {
        "prepare_sha256": sha256(directory / "prepare_data.py"),
        "dirty_sha256": sha256(directory / "dirty_data.py"),
        "source_sha256": sha256(directory / real.SNAPSHOT),
        "output_sha256": (sql_output_sha256 if fmt == 'sql'
                          else sha256(real.output_path(directory, fmt))),
        "weather_adapter_sha256": sha256(Path(__file__).with_name("weather.py")),
        "condition_adapter_sha256": sha256(Path(__file__).with_name("conditions.py")),
        "sql_adapter_sha256": (sha256(Path(__file__).with_name("sql_adapter.py")) if fmt == 'sql' else None),
    }
    with (directory / "practice.toml").open("rb") as handle:
        manifest = tomllib.load(handle)
    values["configuration_sha256"] = _config_hash(manifest)
    return "\n[reproduction]\nmethod = \"openmeteo-weather-v1\"\n" + "".join(
        f'{key} = "{value}"\n' for key, value in values.items() if value is not None
    )


def _config_hash(config: dict) -> str:
    """Freeze generation inputs; permit editing titles/status without rerolling."""
    # Exclude deliberately editable presentation fields: title, family, status, featured.
    # Freeze identity, source acquisition/provenance, and all generation inputs.
    relevant = {key: config[key] for key in
                ("schema_version", "id", "slug", "date", "resolved", "source",
                 "source_provenance", "condition_settings")}
    document = json.dumps(relevant, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(document.encode('utf-8')).hexdigest()


def verify_real(directory: Path, details: dict) -> dict[str, object]:
    """Rebuild from preserved clean source in isolation, never touching practice."""
    from . import real, weather
    from .conditions import apply
    config = details['reproduction']
    if config.get('configuration_sha256') != _config_hash(details):
        raise ReproductionError('Frozen preparation/corruption configuration changed')
    fmt, kind = details['resolved']['format'], details['resolved']['dirty']
    adapter_hashes = [('weather.py', 'weather_adapter_sha256'),
                      ('conditions.py', 'condition_adapter_sha256')]
    if fmt == 'sql':
        adapter_hashes.append(('sql_adapter.py', 'sql_adapter_sha256'))
    implementation_matches = all(
        sha256(Path(__file__).with_name(name)) == config.get(key)
        for name, key in adapter_hashes
    )
    # Tooling can evolve. A code change is reported, but independently compare
    # reconstructed data to the frozen result instead of refusing that check.
    for filename, key in [('prepare_data.py', 'prepare_sha256'),
                          ('dirty_data.py', 'dirty_sha256')]:
        if sha256(directory / filename) != config.get(key):
            raise ReproductionError(f'Saved script has changed: {filename}')
    snapshot = directory / real.SNAPSHOT
    if not snapshot.is_file():
        raise ReproductionError('Original clean source snapshot is unavailable; cannot verify offline')
    if sha256(snapshot) != config.get('source_sha256'):
        raise ReproductionError('Original clean source snapshot changed')
    if fmt == 'sql':
        from . import sql_adapter
        try:
            reconstructed, actual = sql_adapter.verify(details,snapshot)
        except Exception as exc:
            raise ReproductionError(f'SQL verification failed: {exc}') from exc
        return {'practice_id':details['id'],'recorded_sha256':config['output_sha256'],
                'reconstructed_sha256':reconstructed,
                'reconstructed_matches':reconstructed == config['output_sha256'],
                'current_matches':actual == config['output_sha256'],
                'implementation_matches':implementation_matches}
    current = real.output_path(directory, fmt)
    current_matches = current.is_file() and sha256(current) == config['output_sha256']
    document = weather.validate(json.loads(snapshot.read_text()), details['source'])
    value = document if fmt == 'json' else weather.to_frame(document)
    opts = details['condition_settings']
    changed, _ = apply(value, fmt=fmt, kind=kind, seed=int(details['resolved']['seed']),
                       source=details['source'], target=opts['target'],
                       rate=(opts['min_rate'],opts['max_rate']),
                       placement=opts['placement'], n_groups=opts['groups'],
                       grouped_share=opts['grouped_share'],
                       variant=details['resolved'].get('variant', 'none'), parameters=opts)
    with tempfile.TemporaryDirectory(prefix='awf-real-verify-') as folder:
        candidate = Path(folder) / f'weather.{fmt}'
        weather.write_data(changed,fmt,candidate)
        generated_hash = sha256(candidate)
    return {'practice_id':details['id'], 'recorded_sha256':config['output_sha256'],
            'reconstructed_sha256':generated_hash,
            'reconstructed_matches':generated_hash == config['output_sha256'],
            'current_matches':current_matches,
            'implementation_matches':implementation_matches}


def verify(directory: Path) -> dict[str, object]:
    """Reexecute the scripts in a *temporary* folder; leave original untouched.

    Reports two independent checks: reconstruction of the frozen result, and
    whether the current analyst-facing CSV still matches its original state.
    """
    directory = directory.resolve()
    config = directory / "practice.toml"
    if not config.is_file():
        raise ReproductionError(f"No practice.toml: {config}")
    from .blind import full_record_path, validate_public_identity
    validate_public_identity(directory)
    stored = full_record_path(directory)
    with (stored if stored.is_file() else config).open("rb") as handle:
        details = tomllib.load(handle)
    expected = details.get("reproduction")
    if isinstance(expected, dict) and expected.get("method") == "openmeteo-weather-v1":
        return verify_real(directory, details)
    if not isinstance(expected, dict) or expected.get("method") != METHOD:
        raise ReproductionError("Practice has no supported reproduction record")

    # Refuse to execute scripts that differ from the saved originals.
    for name, key in ((FILES[0], "prepare_sha256"), (FILES[1], "dirty_sha256")):
        path = directory / name
        if not path.is_file() or sha256(path) != expected.get(key):
            raise ReproductionError(f"Saved script has changed: {name}")
    original = directory / MOCK_OUTPUT
    current_matches = original.is_file() and sha256(original) == expected.get("output_sha256")

    with tempfile.TemporaryDirectory(prefix="awf-reproduce-") as temp_name:
        staged = Path(temp_name)
        for filename in FILES:
            (staged / filename).write_bytes((directory / filename).read_bytes())
        for filename in FILES:
            try:
                subprocess.run([sys.executable, filename], cwd=staged,
                               capture_output=True, text=True, check=True)
            except subprocess.CalledProcessError as exc:
                raise ReproductionError(f"Reproduction script failed: {filename}: {exc.stderr}") from exc
        generated_hash = sha256(staged / MOCK_OUTPUT)

    return {
        "practice_id": details["id"],
        "recorded_sha256": expected["output_sha256"],
        "reconstructed_sha256": generated_hash,
        "reconstructed_matches": generated_hash == expected["output_sha256"],
        "current_matches": current_matches,
    }
