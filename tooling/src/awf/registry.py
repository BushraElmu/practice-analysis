"""Ledger-backed identity and derived index for standalone dated practices.

Historical ledger records deliberately do not have guessed filesystem paths.
A real practice.toml with the same ID supersedes the historical record.
"""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from pathlib import Path


class RegistryError(ValueError):
    """Invalid registry data or ambiguous practice identity."""


def _load(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def historical(root: Path) -> list[dict]:
    source = root / "tooling" / "historical_practices.toml"
    if not source.is_file():
        return []
    data = _load(source)
    rows = data.get("practices", [])
    if not isinstance(rows, list):
        raise RegistryError("historical_practices.toml must contain [[practices]] entries")
    for row in rows:
        if type(row.get("id")) is not int or row["id"] < 1:
            raise RegistryError("Historical practice IDs must be positive integers")
        if not isinstance(row.get("title"), str) or not row["title"].strip():
            raise RegistryError(f"Historical practice {row['id']} needs a title")
        if "path" in row:
            raise RegistryError("Historical records must not invent paths: link a real practice.toml instead")
    if len({row["id"] for row in rows}) != len(rows):
        raise RegistryError("Duplicate historical practice ID")
    return rows


def _dated_dirs(root: Path):
    # Only direct children, never scan an entire nested virtual environment.
    for directory in sorted(root.iterdir()):
        if directory.is_dir() and (directory / "practice.toml").is_file():
            yield directory


def catalog(root: Path) -> list[dict]:
    root = root.resolve()
    entries: dict[int, dict] = {}
    for row in historical(root):
        entries[row["id"]] = dict(row, origin="historical-ledger", linked=False)

    seen_paths: set[str] = set()
    seen_live_ids: set[int] = set()
    for directory in _dated_dirs(root):
        from .blind import validate_public_identity
        validate_public_identity(directory)
        meta = _load(directory / "practice.toml")
        number = meta.get("id")
        if type(number) is not int or number < 1:
            raise RegistryError(f"Invalid practice ID in {directory / 'practice.toml'}")
        if number in seen_live_ids:
            raise RegistryError(f"Duplicate linked practice ID {number}")
        seen_live_ids.add(number)
        relative = directory.relative_to(root).as_posix()
        if relative in seen_paths:
            raise RegistryError(f"Duplicate practice path {relative}")
        seen_paths.add(relative)
        base = entries.get(number, {})
        title = meta.get("title", base.get("title", directory.name))
        if not isinstance(title, str) or not title.strip():
            raise RegistryError(f"Invalid title in {directory / 'practice.toml'}")
        resolved = meta.get("resolved", {})
        incomplete = (directory / '.awf' / 'SETUP_INCOMPLETE').exists()
        entry = {
            "id": number, "title": title,
            "status": ('setup-incomplete' if incomplete else
                       meta.get("status", base.get("status", "in-progress"))),
            "family": meta.get("family", base.get("family", "unclassified")),
            "format": resolved.get("format", meta.get("format", base.get("format", "unknown"))),
            "condition": resolved.get("dirty", meta.get("condition", base.get("condition", "unknown"))),
            "featured": meta.get("featured", base.get("featured", False)),
            "origin": "practice.toml", "linked": True, "path": relative,
        }
        if "note" in base:
            entry["note"] = base["note"]
        entries[number] = entry
    return [entries[key] for key in sorted(entries)]


def next_number(root: Path, requested: int | None = None) -> int:
    ids = {row["id"] for row in catalog(root)}
    if requested is not None:
        if type(requested) is not int or requested < 1 or requested in ids:
            raise RegistryError(f"Practice number unavailable: {requested}")
        return requested
    return max(ids, default=0) + 1


def resolve(root: Path, selector: str) -> Path:
    """Locate a *linked* practice by ID or relative directory. No guessed paths."""
    root = root.resolve()
    for row in catalog(root):
        if str(row["id"]) == selector or (row.get("path") == selector):
            if not row["linked"]:
                raise RegistryError(
                    f"Practice {row['id']} exists in the historical ledger, "
                    "but no real folder is linked; this operation is unavailable"
                )
            return root / row["path"]
    raise RegistryError(f"Unknown practice: {selector}")


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_index(rows: list[dict]) -> str:
    result = [
        "# GENERATED by awf index; edit each practice.toml or tooling/historical_practices.toml.",
        "# Historical entries have no invented path; a linked practice.toml supersedes them.",
        "",
    ]
    field_order = ("id", "title", "status", "family", "format", "condition", "featured", "linked", "origin", "path", "note")
    for row in rows:
        result.append("[[practices]]")
        for key in field_order:
            if key not in row:
                continue
            value = row[key]
            if isinstance(value, bool):
                literal = "true" if value else "false"
            elif type(value) is int:
                literal = str(value)
            elif isinstance(value, str):
                literal = _quote(value)
            else:
                raise RegistryError(f"Unsupported registry value for {key}")
            result.append(f"{key} = {literal}")
        result.append("")
    return "\n".join(result)


def write_index(root: Path) -> Path:
    root = root.resolve()
    content = render_index(catalog(root))
    target = root / "practices.toml"
    # Preserve a valid old index if the new index cannot be constructed/written.
    fd, draft_name = tempfile.mkstemp(prefix=".awf-index-", suffix=".toml", dir=root)
    draft = Path(draft_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
            out.write(content)
        os.replace(draft, target)
    finally:
        draft.unlink(missing_ok=True)
    return target
