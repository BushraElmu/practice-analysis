"""CLI for independent weather practice generation, discovery and reproduction."""

from __future__ import annotations

import argparse
import sys
import json
import os
import shutil
from pathlib import Path

from .registry import RegistryError, resolve, write_index, catalog
from .reproducibility import ReproductionError, verify
from .setup import SetupError, setup


def repository_root(start: Path | None = None) -> Path:
    # For a practice wrapper, anchor lookup to its explicit target, not cwd.
    # Other commands intentionally operate on the caller's current checkout.
    origin = (start if start is not None else Path.cwd()).resolve()
    for candidate in (origin, *origin.parents):
        if (candidate / "tooling" / "selection.toml").is_file():
            return candidate
    raise SetupError("Cannot find repository containing tooling/selection.toml")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="awf", description="AWF weather setup, variant selection and isolated reproduction")
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("setup", help="Create a dated real-weather practice (small window by default)")
    prep.add_argument("name", help="lowercase hyphenated practice name")
    prep.add_argument("--format", default="random", dest="fmt")
    prep.add_argument("--dirty", default="random")
    prep.add_argument("--variant", default="random", help="weighted subtype of selected dirtiness family; or request one explicitly")
    prep.add_argument("--seed", type=int, help="integer 000000 through 999999; automatically generated if omitted")
    prep.add_argument("--number", type=int, help="optional explicit practice number")
    prep.add_argument("--blind", action="store_true", help="conceal the resolved condition in a gitignored private record (not adversarial secrecy)")
    prep.add_argument("--scaffold-only", action="store_true", help="skip uv sync (no lockfile/.venv)")
    prep.add_argument("--date", dest="date_str", help=argparse.SUPPRESS)
    prep.add_argument("--mock", action="store_true", help="retain the Stage 2 two-column demo")
    prep.add_argument("--fixture", type=Path, help="offline Open-Meteo-shaped JSON fixture (test/demo only)")
    prep.add_argument("--start", help="local source start date, inclusive; defaults from tooling/weather_defaults.toml")
    prep.add_argument("--end", help="local source end date, exclusive; defaults from tooling/weather_defaults.toml")

    step = sub.add_parser("step", help="Run the prepare/dirty adapter from a generated practice wrapper")
    step.add_argument("practice_dir", type=Path)
    step.add_argument("operation", choices=("prepare", "dirty"))
    step.add_argument("--force", action="store_true", help="explicitly replace current file data during regeneration")

    browse = sub.add_parser("list", help="Browse the consolidated practice registry")
    browse.add_argument("--format", choices=('csv','json','parquet','sql'))
    browse.add_argument("--status")
    browse.add_argument("--featured", action="store_true")
    browse.add_argument("--linked", action="store_true")
    show = sub.add_parser("show", help="Show public metadata for a practice")
    show.add_argument("practice")
    show.add_argument("--json", action="store_true")
    reveal = sub.add_parser("reveal", help="Reveal a locally concealed condition after analysis")
    reveal.add_argument("practice")
    reveal.add_argument("--publish", action="store_true", help="publish resolved metadata to practice.toml and regenerate the registry")
    sub.add_parser("doctor", help="Check dependencies and SQL opt-in availability")
    sync = sub.add_parser("sql-sync", help="Explicitly populate canonical weather source; requires separate maintenance DSN")
    sync.add_argument("--start", help="source start, inclusive; defaults from weather_defaults.toml")
    sync.add_argument("--end", help="source end, exclusive; defaults from weather_defaults.toml")
    sync.add_argument("--fixture", type=Path, help="local synthetic fixture for integration tests only")

    sub.add_parser("index", help="Rebuild practices.toml from historical ledger and practice.toml files")
    check = sub.add_parser("verify", help="Reconstruct the frozen starting dataset in isolation")
    check.add_argument("practice", help="linked practice ID or dated folder name")

    args = parser.parse_args(argv)
    try:
        if args.command == "step":
            root = repository_root(args.practice_dir.resolve().parent)
        else:
            root = repository_root()
        if args.command == "step":
            from . import real
            practice = args.practice_dir.resolve()
            if root not in practice.parents or not (practice / "practice.toml").is_file():
                raise SetupError("step must target a registered practice inside the repository")
            if args.operation == "prepare":
                real.prepare(practice, force=args.force)
            else:
                real.dirty(practice, force=args.force)
            print(f"{args.operation} complete: {practice}")
            return 0
        if args.command == "setup":
            path = setup(
                root=root, slug=args.name, policy_path=root / "tooling" / "selection.toml",
                fmt=args.fmt, dirty=args.dirty, variant=args.variant, seed=args.seed,
                number=args.number, scaffold_only=args.scaffold_only, blind=args.blind,
                date_str=args.date_str, mode="mock" if args.mock else "weather",
                fixture=args.fixture.resolve() if args.fixture else None,
                start_local=args.start, end_local=args.end,
            )
            print(f"Practice created: {path}")
            print("Registry updated: practices.toml")
            print("Condition concealed in .awf/record.toml; run `just reveal N` when ready." if args.blind else "Selection recorded in practice.toml; condition is visible.")
            if args.scaffold_only:
                print("Scaffold-only: run `uv sync` in the practice folder to create uv.lock and .venv.")
            return 0
        if args.command == 'doctor':
            from . import sql_adapter
            print('uv:', shutil.which('uv') or 'NOT FOUND')
            print('just:', shutil.which('just') or 'NOT FOUND')
            import importlib.util
            print('pyarrow:', 'installed' if importlib.util.find_spec('pyarrow') else 'unavailable')
            print('psycopg:', 'installed' if importlib.util.find_spec('psycopg') else 'unavailable')
            print('AWF_DATABASE_URL:', 'configured' if os.environ.get('AWF_DATABASE_URL') else 'not set')
            print('AWF_CANONICAL_DATABASE_URL:', 'configured' if os.environ.get('AWF_CANONICAL_DATABASE_URL') else 'not set')
            print('SQL random setup:', 'eligible (canonical coverage validated at setup)' if sql_adapter.available() else 'excluded (no safe PostgreSQL connection)')
            return 0
        if args.command == 'list':
            rows=catalog(root)
            for r in rows:
                if args.format and r['format']!=args.format: continue
                if args.status and r['status']!=args.status: continue
                if args.featured and not r['featured']: continue
                if args.linked and not r['linked']: continue
                print(f"{r['id']:>3} | {r['format']:<8} | {r['condition']:<17} | {r['status']:<12} | {r['title']}")
            return 0
        if args.command == 'show':
            rows=catalog(root)
            matches=[r for r in rows if str(r['id'])==args.practice or r.get('path')==args.practice]
            if not matches: raise RegistryError(f'Unknown practice: {args.practice}')
            row=matches[0]
            if args.json: print(json.dumps(row,indent=2))
            else:
                for k,v in row.items(): print(f'{k}: {v}')
            return 0
        if args.command == 'reveal':
            from .blind import reveal
            record = reveal(resolve(root,args.practice),publish=args.publish)
            print(json.dumps(record, indent=2))
            if args.publish: write_index(root)
            return 0
        if args.command == 'sql-sync':
            from . import sql_adapter
            from .source import source_spec
            result=sql_adapter.sync_canonical(source_spec(args.start, args.end, root=root),
                    fixture=args.fixture.resolve() if args.fixture else None)
            print(f"Canonical weather ready: {result['expected']} hours; {result['added']} inserted")
            return 0
        if args.command == "index":
            path = write_index(root)
            print(f"Registry generated: {path}")
            return 0
        if args.command == "verify":
            result = verify(resolve(root, args.practice))
            print(f"Practice {result['practice_id']}: reconstructed starting dataset "
                  f"{'MATCHES' if result['reconstructed_matches'] else 'DIFFERS'} from frozen record")
            print(f"Current dataset: {'unchanged' if result['current_matches'] else 'changed or missing'}")
            if not result["current_matches"]:
                print("Current data was not overwritten; verify reconstruction independently.")
            if result.get('implementation_matches') is False:
                print('Tooling implementation changed since setup; output reconstruction was compared independently.')
            return 0 if result["reconstructed_matches"] else 1
    except (SetupError, RegistryError, ReproductionError, ValueError, OSError) as exc:
        print(f"awf: {exc}", file=sys.stderr)
        return 2
    return 2
