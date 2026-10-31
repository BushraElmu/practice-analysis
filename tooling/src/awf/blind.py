"""Voluntary concealment of generation answers, not a security boundary.

The private TOML is gitignored and stored in the user's own checkout.  It is
readable by anyone with access to that checkout.  Do not present this as an
adversarial secret or as fresh-clone reproduction support.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path


def full_record_path(directory: Path) -> Path:
    return directory / '.awf' / 'record.toml'


def conceal(directory: Path):
    source = directory / 'practice.toml'
    original = source.read_text(encoding='utf-8')
    config = tomllib.loads(original)
    if 'resolved' not in config or 'reproduction' not in config:
        raise ValueError('Cannot conceal practice without resolved reproduction metadata')
    private = full_record_path(directory)
    private.parent.mkdir(exist_ok=True)
    with private.open('x', encoding='utf-8') as handle:
        handle.write(original)
    os.chmod(private, 0o600)
    # We intentionally do not expose [requested] or [resolved].dirty/variant,
    # the selection weights, condition settings, or fingerprints.
    keep = [
        f'schema_version = {config["schema_version"]}',
        f'id = {config["id"]}',
        f'title = {config["title"]!r}',
        f'slug = {config["slug"]!r}',
        f'date = {config["date"]!r}',
        f'status = {config["status"]!r}',
        f'family = {config["family"]!r}',
        'featured = false',
        'condition = "undisclosed"',
        '[resolved]',
        f'format = {config["resolved"]["format"]!r}',
        '[source]',
    ]
    for key, value in config['source'].items():
        if isinstance(value, str):
            keep.append(f'{key} = {value!r}')
        elif isinstance(value, list):
            keep.append(f'{key} = ['+', '.join(repr(x) for x in value)+']')
        else:
            keep.append(f'{key} = {value!r}')
    source.write_text('\n'.join(keep)+'\n',encoding='utf-8')


def validate_public_identity(directory: Path, private: dict | None = None) -> None:
    """Reject diverging public/private identities, without exposing the condition."""
    record = full_record_path(directory)
    if not record.is_file():
        return
    if private is None:
        with record.open('rb') as f:
            private = tomllib.load(f)
    with (directory / 'practice.toml').open('rb') as f:
        public = tomllib.load(f)
    for key in ('schema_version', 'id', 'slug', 'date'):
        if public.get(key) != private.get(key):
            raise ValueError(f'Public and private practice identity disagree: {key}')
    if public.get('condition') != 'undisclosed':
        raise ValueError('Blind public record must keep the condition undisclosed')
    if public.get('resolved', {}).get('format') != private.get('resolved', {}).get('format'):
        raise ValueError('Public and private format disagree')
    if public.get('source') != private.get('source'):
        raise ValueError('Public and private source metadata disagree')
    if 'dirty' in public.get('resolved', {}) or 'reproduction' in public:
        raise ValueError('Blind public metadata unexpectedly discloses generation details')


def reveal(directory: Path, *, publish: bool = False) -> dict:
    path = full_record_path(directory)
    if not path.is_file():
        raise ValueError('No concealed generation record for this practice')
    config = tomllib.loads(path.read_text(encoding='utf-8'))
    validate_public_identity(directory, config)
    if publish:
        (directory / 'practice.toml').write_bytes(path.read_bytes())
        path.unlink()
    return {'id':config['id'], 'format':config['resolved']['format'],
            'condition':config['resolved']['dirty'],
            'variant':config['resolved'].get('variant', 'none'),
            'seed': config['resolved']['seed']}
