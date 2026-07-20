"""Enablement-manifest seeding (FEAT-260718-210a5f; format per ADR-260707-c9919f).

One household-owned file — ``<data_root>/enablement.yaml`` — maps habitat →
``all`` | named-user list. An absent habitat means ``all`` (today's
behavior), so seeding only writes an entry when the habitat's declared
default narrows visibility. **Seeding only**: nothing in the kernel enforces
this manifest yet — that is FEAT-260707-acb2b6's re-scoped job.

An ``admin`` default is resolved to the *current* admin slugs at install
time, keeping the manifest schema to exactly the two shapes the ADR decided
(``all`` | list). New admins added later do not silently gain the habitat —
an explicit household-policy edit does that, which is the point.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

MANIFEST_FILENAME = 'enablement.yaml'
_KINDS = ('skills', 'connectors')


def manifest_path() -> Path:
    from marcel_core.storage._root import data_root

    return data_root() / MANIFEST_FILENAME


def _load() -> dict:
    path = manifest_path()
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    return raw if isinstance(raw, dict) else {}


def _save(manifest: dict) -> None:
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.yaml.tmp')
    tmp.write_text(yaml.safe_dump(manifest, sort_keys=True), encoding='utf-8')
    tmp.replace(path)


def _admin_slugs() -> list[str]:
    from marcel_core.storage.paths import list_user_slugs
    from marcel_core.storage.users import get_user_role

    return sorted(slug for slug in list_user_slugs() if get_user_role(slug) == 'admin')


def seed(kind: str, name: str, default: str) -> str:
    """Seed the manifest entry for a fresh install; returns what was written.

    ``all`` writes nothing (absent ⇒ all); ``admin`` writes the current admin
    slugs; ``none`` writes an empty list (installed, visible to nobody until
    the admin enables it).
    """
    if kind not in _KINDS:
        raise ValueError(f'unknown habitat kind {kind!r}')
    if default == 'all':
        return 'all (manifest untouched — absent means everyone)'
    value: list[str] = _admin_slugs() if default == 'admin' else []
    manifest = _load()
    manifest.setdefault(kind, {})[name] = value
    _save(manifest)
    rendered = ', '.join(value) or 'nobody (enable explicitly)'
    return f'{default} → {rendered}'


def remove(kind: str, name: str) -> bool:
    """Drop the habitat's entry on removal; True when an entry existed."""
    manifest = _load()
    entries = manifest.get(kind)
    if not isinstance(entries, dict) or name not in entries:
        return False
    del entries[name]
    if not entries:
        del manifest[kind]
    _save(manifest)
    return True
