"""Enablement-manifest seeding (FEAT-260718-210a5f; format per ADR-260707-c9919f).

One household-owned file — ``<data_root>/enablement.yaml`` — maps habitat →
``all`` | named-user list. An absent habitat means ``all`` (today's
behavior), so seeding only writes an entry when the habitat's declared
default narrows visibility.

**Enforcement** (FEAT-260707-acb2b6) lives at the two loader choke points —
``load_skills`` and ``load_connectors`` call :func:`enabled_for` per habitat
— so a scoped-out user's build simply never contains the habitat: catalog,
toolset attachment, skill→connector bundling and job scoping all flow
through those loaders. Availability = role ∧ enablement ∧ configuration
(ADR-260707-c9919f). The anonymous/global view (``user_slug=None``) is not
filtered — enablement is per-user policy, not a system gate. A malformed
entry fails **closed as admin-only** with a loud log (NFR1): the household
keeps working for the zoo keeper, who is also the person who can fix it.

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


def enabled_for(kind: str, name: str, user_slug: str | None, role: str = 'user') -> bool:
    """Whether *user_slug* may see habitat *name* under household policy.

    Absent file/entry or a literal ``all`` ⇒ enabled for everyone. A named
    list ⇒ membership. Anything else is malformed: fail closed as admin-only
    with a loud log. ``user_slug=None`` (the global view) is never filtered.
    """
    if user_slug is None:
        return True
    entries = _load().get(kind)
    if not isinstance(entries, dict) or name not in entries:
        return True
    value = entries[name]
    if value == 'all':
        return True
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return user_slug in value
    log.warning(
        'enablement: malformed entry %s/%s (%r) — failing closed as admin-only until fixed',
        kind,
        name,
        value,
    )
    return role == 'admin'


def enable(kind: str, name: str, slug: str) -> str:
    """Admin surface: add *slug* to the habitat's named list."""
    if kind not in _KINDS:
        raise ValueError(f'unknown habitat kind {kind!r}')
    manifest = _load()
    raw_entries = manifest.get(kind)
    entries: dict = raw_entries if isinstance(raw_entries, dict) else {}
    value = entries.get(name)
    if value is None or value == 'all':
        return f'{name} is already enabled for everyone (no entry — absent means all).'
    if not isinstance(value, list):
        raise ValueError(f'entry {kind}/{name} is malformed ({value!r}) — fix the manifest by hand first')
    if slug in value:
        return f'{name} is already enabled for {slug}.'
    manifest.setdefault(kind, {})[name] = sorted([*value, slug])
    _save(manifest)
    return f'{name} enabled for {slug} (now: {", ".join(manifest[kind][name])}).'


def disable(kind: str, name: str, slug: str) -> str:
    """Admin surface: remove *slug*; disabling from ``all`` writes the
    explicit everyone-but-*slug* list (the manifest's two legal shapes)."""
    if kind not in _KINDS:
        raise ValueError(f'unknown habitat kind {kind!r}')
    from marcel_core.storage.paths import list_user_slugs

    manifest = _load()
    raw_entries = manifest.get(kind)
    entries: dict = raw_entries if isinstance(raw_entries, dict) else {}
    value = entries.get(name)
    if value is None or value == 'all':
        remaining = sorted(s for s in list_user_slugs() if s != slug)
        manifest[kind] = {**entries, name: remaining}  # normalized — never .setdefault onto garbage
        _save(manifest)
        return f'{name} disabled for {slug} (was all; now: {", ".join(remaining) or "nobody"}).'
    if not isinstance(value, list):
        raise ValueError(f'entry {kind}/{name} is malformed ({value!r}) — fix the manifest by hand first')
    if slug not in value:
        return f'{name} is already disabled for {slug}.'
    manifest[kind][name] = [v for v in value if v != slug]
    _save(manifest)
    return f'{name} disabled for {slug} (now: {", ".join(manifest[kind][name]) or "nobody"}).'
