# Root is a collection of independent uv practice projects; tooling is nested.
import? 'personal.just'
set positional-arguments

# Create a dated practice. Further flags are forwarded to awf's Python CLI.
setup name *options:
    name="$1"; shift; uv run --no-editable --project tooling awf setup "$name" "$@"

# Refresh the derived root registry (historical ledger + linked practice.toml).
index:
    uv run --no-editable --project tooling awf index

# Reconstruct a frozen starting dataset in isolation; never overwrite the current one.
verify practice:
    uv run --no-editable --project tooling awf verify "$1"

help:
    uv run --no-editable --project tooling awf --help

# See configured formats, historical practices, and optional SQL prerequisites.
doctor:
    uv run --no-editable --project tooling awf doctor

list *options:
    uv run --no-editable --project tooling awf list "$@"

show practice *options:
    practice="$1"; shift; uv run --no-editable --project tooling awf show "$practice" "$@"

reveal practice *options:
    practice="$1"; shift; uv run --no-editable --project tooling awf reveal "$practice" "$@"

# Explicit maintenance task. Never run automatically during practice setup.
sql-sync *options:
    uv run --no-editable --project tooling awf sql-sync "$@"
