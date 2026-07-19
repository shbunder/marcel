"""Skill document loader — agentskills.io-conformant SKILL.md discovery.

A skill is a folder with a ``SKILL.md`` per the Agent Skills open standard
(agentskills.io): frontmatter with required ``name`` (1–64 chars,
``[a-z0-9]`` + single hyphens, equal to the directory name) and
``description`` (1–1024 chars); any other frontmatter key is tolerated.
Marcel's own extensions travel only in the spec-legal ``metadata`` map
(string→string) under ``marcel-*`` keys — never top-level (ADR-260718-7c68f4).

Skills resolve through a three-root scoping chain, most-specific wins:
``~/.marcel/users/<slug>/skills/`` → ``<zoo>/users/<slug>/skills/`` →
``<zoo>/skills/``. Each visible skill becomes a deferred capability
(FEAT-260718-85b545); this module owns discovery/validation/scoping, the
capability factory lives in :mod:`marcel_core.skills.capability`.

A skill can still fall back to serving ``SETUP.md`` when its declared
requirements are unmet, conversationally onboarding a family member.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from marcel_core.skills.components import ComponentSchema, parse_components_yaml

log = logging.getLogger(__name__)

# agentskills.io name rule: 1–64 chars, lowercase alphanumerics + single
# hyphens, no leading/trailing/double hyphen.
_NAME_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
_MAX_NAME_LEN = 64
_MAX_DESCRIPTION_LEN = 1024
# Soft budget: the spec recommends ~100 tokens (~400 chars) per catalog
# entry; the validator warns above this but does not reject.
_CATALOG_WARN_CHARS = 500

_VALID_TIERS = frozenset({'local', 'fast', 'standard', 'power'})
_VALID_ROLES = frozenset({'admin'})
# marcel-default-enabled: which users a freshly-installed skill is enabled for
# by default. Reserved + validated here (FEAT-260718-85b545); the three-state
# lifecycle (ADR-260718-7addc8 / FEAT-260718-210a5f seeding, FEAT-260707-acb2b6
# enforcement) consumes it. Absent ⇒ 'all'.
_VALID_DEFAULT_ENABLED = frozenset({'all', 'admin', 'none'})
_DEFAULT_ENABLED_FALLBACK = 'all'

_SKILL_MD_NAME = 'SKILL.md'
_SETUP_MD_NAME = 'SETUP.md'
# File extensions exposed as named skill resources.
_RESOURCE_EXTENSIONS = frozenset({'.md', '.yaml', '.yml', '.json', '.txt', '.csv'})


def _skills_dir() -> Path:
    """The data-root skills directory (single-path back-compat accessor)."""
    from marcel_core.config import settings

    return settings.data_dir / 'skills'


def _skill_dirs(user_slug: str | None = None) -> list[tuple[Path, str]]:
    """Return ``(path, source)`` roots in least→most-specific order.

    Sources, least specific first so a later entry overrides an earlier one
    when :func:`load_skills` dedups by name:

    1. ``<zoo>/skills/`` — global habitats (``source='zoo-global'``)
    2. ``<zoo>/users/<slug>/skills/`` — git-managed per-user (``'zoo-user'``)
    3. ``<data>/users/<slug>/skills/`` — runtime-installed per-user
       (``'data-user'``)

    A missing root is simply absent from the list (never an error). When
    ``user_slug`` is ``None`` only the global root is returned.
    """
    from marcel_core.config import settings

    dirs: list[tuple[Path, str]] = []
    zoo = settings.zoo_dir
    if zoo is not None and (zoo / 'skills').is_dir():
        dirs.append((zoo / 'skills', 'zoo-global'))
    if user_slug:
        if zoo is not None and (zoo / 'users' / user_slug / 'skills').is_dir():
            dirs.append((zoo / 'users' / user_slug / 'skills', 'zoo-user'))
        data_user = _skills_dir().parent / 'users' / user_slug / 'skills'
        if data_user.is_dir():
            dirs.append((data_user, 'data-user'))
    return dirs


@dataclass
class SkillDoc:
    """A discovered, validated skill.

    ``content`` is the SKILL.md body (frontmatter stripped) — the
    instructions a deferred capability serves on ``load_capability``.
    """

    name: str
    description: str
    content: str
    source: str  # 'zoo-global' | 'zoo-user' | 'data-user'
    skill_dir: Path
    is_setup: bool = False  # serving SETUP.md because a requirement is unmet
    metadata: dict[str, str] = field(default_factory=dict)
    # Marcel extensions, parsed from metadata marcel-* keys:
    preferred_tier: str | None = None  # marcel-tier ('local'|'fast'|'standard'|'power')
    role: str | None = None  # marcel-role ('admin' or None)
    default_enabled: str = _DEFAULT_ENABLED_FALLBACK  # marcel-default-enabled ('all'|'admin'|'none')
    connectors: list[str] = field(default_factory=list)  # marcel-connectors
    credential_keys: list[str] = field(default_factory=list)
    components: list[ComponentSchema] = field(default_factory=list)


# ---------------------------------------------------------------------------
# frontmatter parsing + validation
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a ``---`` YAML frontmatter block from the markdown body."""
    if not text.startswith('---'):
        return {}, text
    parts = text.split('---', 2)
    if len(parts) < 3:
        return {}, text
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(fm, dict):
        return {}, text
    return fm, parts[2].lstrip('\n')


def validate_skill_frontmatter(fm: dict, dir_name: str) -> str | None:
    """Return an actionable error string if *fm* is nonconformant, else None.

    Mirrors the agentskills.io ``skills-ref`` rules Marcel enforces: a valid
    ``name`` (regex + length) that equals the directory name, and a
    ``description`` within bounds. Unknown frontmatter keys are tolerated.
    """
    name = fm.get('name')
    if not isinstance(name, str) or not name:
        return f'{dir_name!r}: missing required frontmatter `name`'
    if len(name) > _MAX_NAME_LEN or not _NAME_RE.fullmatch(name):
        return (
            f'{dir_name!r}: invalid `name` {name!r} — must be 1–{_MAX_NAME_LEN} chars, '
            'lowercase alphanumerics and single hyphens (no leading/trailing/double hyphen)'
        )
    if name != dir_name:
        return f'{dir_name!r}: frontmatter `name` {name!r} must equal the directory name {dir_name!r}'
    description = fm.get('description')
    if not isinstance(description, str) or not description:
        return f'{dir_name!r}: missing required frontmatter `description`'
    if len(description) > _MAX_DESCRIPTION_LEN:
        return f'{dir_name!r}: `description` exceeds {_MAX_DESCRIPTION_LEN} chars'
    return None


def _coerce_metadata(fm: dict, dir_name: str) -> dict[str, str]:
    """Extract the spec-legal ``metadata`` map (string→string), tolerant."""
    raw = fm.get('metadata')
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        log.warning('skills: %s metadata must be a map; got %r — ignoring', dir_name, type(raw).__name__)
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _migrate_legacy_frontmatter(fm: dict, dir_name: str) -> dict[str, str]:
    """Map retired top-level keys into ``metadata`` marcel-* keys.

    Reads ``depends_on`` / ``preferred_tier`` / ``requires`` (the pre-v2
    Marcel frontmatter) with a deprecation log and returns the metadata they
    map to. The zoo skills are migrated to native ``metadata`` in the same
    feature; this keeps third-party or not-yet-migrated skills working.
    """
    extra: dict[str, str] = {}
    depends_on = fm.get('depends_on')
    if depends_on:
        names = [depends_on] if isinstance(depends_on, str) else [str(n) for n in depends_on]
        extra['marcel-connectors'] = ','.join(names)
        log.warning('skills: %s uses legacy `depends_on` — migrate to metadata.marcel-connectors', dir_name)
    tier = fm.get('preferred_tier')
    if tier:
        extra['marcel-tier'] = str(tier)
        log.warning('skills: %s uses legacy `preferred_tier` — migrate to metadata.marcel-tier', dir_name)
    requires = fm.get('requires')
    if isinstance(requires, dict) and requires:
        if requires.get('role') == 'admin':
            extra['marcel-role'] = 'admin'
        if requires.get('credentials'):
            extra['marcel-requires-credentials'] = ','.join(str(c) for c in requires['credentials'])
        if requires.get('env'):
            extra['marcel-requires-env'] = ','.join(str(e) for e in requires['env'])
        log.warning('skills: %s uses legacy `requires` — migrate to metadata.marcel-* keys', dir_name)
    return extra


def _csv(value: str | None) -> list[str]:
    return [p.strip() for p in value.split(',') if p.strip()] if value else []


# ---------------------------------------------------------------------------
# requirement checks (SETUP.md fallback)
# ---------------------------------------------------------------------------


def _credentials_present(cred_keys: list[str], user_slug: str | None) -> bool:
    if not cred_keys:
        return True
    if user_slug is None:
        # The anonymous (global-catalog) view has no vault to check against.
        return False
    try:
        from marcel_core.storage.credentials import load_credentials

        creds = load_credentials(user_slug)
    except Exception:
        log.debug('Could not load credentials for user %s', user_slug, exc_info=True)
        return False
    return all(creds.get(key) for key in cred_keys)


def _env_present(env_keys: list[str]) -> bool:
    return all(os.environ.get(key) for key in env_keys)


def _connector_requirements_met(connectors: list[str], user_slug: str | None, role: str = 'user') -> bool:
    """Whether a skill's ``marcel-connectors`` are satisfied for this user.

    A discoverable connector counts as met: an unlinked one already degrades on
    its own — the connector capability becomes a readable "needs setup"
    stand-in — and gating the *skill* on it too would hide the skill body
    behind SETUP.md even for a fully-linked user. A name that resolves to no
    connector is unmet, so the user sees SETUP.md rather than a skill that
    cannot work. (The pre-connector toolkit fallback retired with the toolkit
    habitat, FEAT-260718-c232d9.)
    """
    if not connectors:
        return True
    from marcel_core.connectors.loader import get_connector

    return all(get_connector(name, user_slug, role) is not None for name in connectors)


def _requirements_met(
    doc_meta: dict[str, str], connectors: list[str], user_slug: str | None, role: str = 'user'
) -> bool:
    """Whether a skill's own requirements (from metadata) are satisfied."""
    if not _credentials_present(_csv(doc_meta.get('marcel-requires-credentials')), user_slug):
        return False
    if not _env_present(_csv(doc_meta.get('marcel-requires-env'))):
        return False
    return _connector_requirements_met(connectors, user_slug, role)


# ---------------------------------------------------------------------------
# loading + scoping
# ---------------------------------------------------------------------------


def _load_skill_dir(skill_dir: Path, source: str, user_slug: str | None, role: str = 'user') -> SkillDoc | None:
    """Load and validate one skill directory, or None if nonconformant.

    Serves SETUP.md instead of SKILL.md when the skill's requirements are
    unmet; the returned ``SkillDoc.is_setup`` marks that.
    """
    skill_md = skill_dir / _SKILL_MD_NAME
    setup_md = skill_dir / _SETUP_MD_NAME

    if not skill_md.exists():
        # A skill folder must have a SKILL.md; a lone SETUP.md is malformed.
        log.warning('skills: %s has no SKILL.md — skipping', skill_dir.name)
        return None

    fm, body = _parse_frontmatter(skill_md.read_text(encoding='utf-8'))
    error = validate_skill_frontmatter(fm, skill_dir.name)
    if error is not None:
        log.warning('skills: skipping nonconformant skill — %s', error)
        return None

    metadata = _coerce_metadata(fm, skill_dir.name)
    # Legacy keys fill in only where native metadata did not.
    for key, value in _migrate_legacy_frontmatter(fm, skill_dir.name).items():
        metadata.setdefault(key, value)

    if len(fm['description']) > _CATALOG_WARN_CHARS:
        log.warning(
            'skills: %s description is %d chars (>~%d) — catalog entries should stay compact',
            skill_dir.name,
            len(fm['description']),
            _CATALOG_WARN_CHARS,
        )

    connectors = _csv(metadata.get('marcel-connectors'))
    tier = metadata.get('marcel-tier')
    if tier is not None and tier not in _VALID_TIERS:
        log.warning('skills: %s marcel-tier %r invalid — ignoring', skill_dir.name, tier)
        tier = None
    # NB: named skill_role, not role — `role` is the *user's* role parameter,
    # and shadowing it here would feed the skill's own gate into the
    # requirement checks below instead of the caller's role.
    skill_role = metadata.get('marcel-role')
    if skill_role is not None and skill_role not in _VALID_ROLES:
        log.warning('skills: %s marcel-role %r invalid — ignoring', skill_dir.name, skill_role)
        skill_role = None
    default_enabled = metadata.get('marcel-default-enabled')
    if default_enabled is not None and default_enabled not in _VALID_DEFAULT_ENABLED:
        log.warning(
            'skills: %s marcel-default-enabled %r invalid — defaulting to %r',
            skill_dir.name,
            default_enabled,
            _DEFAULT_ENABLED_FALLBACK,
        )
        default_enabled = None
    default_enabled = default_enabled or _DEFAULT_ENABLED_FALLBACK

    cred_keys = list(_csv(metadata.get('marcel-requires-credentials')))
    from marcel_core.connectors.loader import get_connector

    for cname in connectors:
        cdoc = get_connector(cname, user_slug, role)
        if cdoc is not None:
            cred_keys.extend(cdoc.config.auth.credential_keys)

    components = (
        parse_components_yaml(skill_dir / 'components.yaml', fm['name'])
        if (skill_dir / 'components.yaml').exists()
        else []
    )

    met = _requirements_met(metadata, connectors, user_slug, role)
    if met or not setup_md.exists():
        return SkillDoc(
            name=fm['name'],
            description=fm['description'],
            content=body,
            source=source,
            skill_dir=skill_dir,
            is_setup=False,
            metadata=metadata,
            preferred_tier=tier,
            role=skill_role,
            default_enabled=default_enabled,
            connectors=connectors,
            credential_keys=cred_keys,
            components=components,
        )

    # Requirements unmet and a SETUP.md exists → serve it (no preferred_tier:
    # a setup flow must not burn a costlier tier).
    setup_fm, setup_body = _parse_frontmatter(setup_md.read_text(encoding='utf-8'))
    return SkillDoc(
        name=fm['name'],
        description=setup_fm.get('description', fm['description']),
        content=setup_body,
        source=source,
        skill_dir=skill_dir,
        is_setup=True,
        metadata=metadata,
        preferred_tier=None,
        role=skill_role,
        default_enabled=default_enabled,
        connectors=connectors,
        credential_keys=cred_keys,
        components=components,
    )


def load_skills(user_slug: str | None, role: str = 'user') -> list[SkillDoc]:
    """Discover the skills visible to *user_slug* at *role*, sorted by name.

    Resolves the scoping chain (global → zoo-user → data-user; most-specific
    wins on name collision, with a logged shadow warning) and drops skills
    gated to a role the user does not have (``metadata.marcel-role: admin``).
    """
    by_name: dict[str, SkillDoc] = {}
    for skills_path, source in _skill_dirs(user_slug):
        for entry in sorted(skills_path.iterdir()):
            if not entry.is_dir() or entry.name.startswith(('_', '.')):
                continue
            doc = _load_skill_dir(entry, source, user_slug, role)
            if doc is None:
                continue
            if doc.name in by_name and by_name[doc.name].source != source:
                log.info(
                    'skills: %s from %s shadows the %s variant for user %s',
                    doc.name,
                    source,
                    by_name[doc.name].source,
                    user_slug,
                )
            by_name[doc.name] = doc

    visible = [d for d in by_name.values() if d.role is None or d.role == role]
    return sorted(visible, key=lambda d: d.name)


def get_skill_content(skill_name: str, user_slug: str, role: str = 'user') -> str | None:
    """Return a visible skill's body (the ``load_capability`` payload)."""
    for doc in load_skills(user_slug, role):
        if doc.name == skill_name:
            return doc.content
    return None


# ---------------------------------------------------------------------------
# resources (skill-root-scoped, traversal-safe)
# ---------------------------------------------------------------------------


def _find_skill_dir(skill_name: str, user_slug: str) -> Path | None:
    """Locate *skill_name*'s directory across the user's scoping chain.

    Most-specific root wins (matches :func:`load_skills`). Matches on the
    validated frontmatter ``name`` (== dir name for conformant skills).
    """
    for skills_path, _source in reversed(_skill_dirs(user_slug)):
        candidate = skills_path / skill_name
        if (candidate / _SKILL_MD_NAME).is_file():
            return candidate
    return None


def list_skill_resources(skill_name: str, user_slug: str) -> list[str]:
    """Resource filenames in *skill_name*'s dir (everything but SKILL.md)."""
    skill_dir = _find_skill_dir(skill_name, user_slug)
    if skill_dir is None:
        return []
    return [
        f.name
        for f in sorted(skill_dir.iterdir())
        if f.is_file() and f.name != _SKILL_MD_NAME and f.suffix.lower() in _RESOURCE_EXTENSIONS
    ]


def get_skill_resource(skill_name: str, resource_name: str, user_slug: str) -> str | None:
    """Return a named resource's content, scoped to the skill root.

    Matches case-insensitively on exact filename then stem. The candidate is
    always a direct child of the skill dir, so ``../`` / absolute names never
    resolve outside it.
    """
    skill_dir = _find_skill_dir(skill_name, user_slug)
    if skill_dir is None:
        return None
    needle = resource_name.lower()
    candidates = [
        f
        for f in skill_dir.iterdir()
        if f.is_file() and f.name != _SKILL_MD_NAME and f.suffix.lower() in _RESOURCE_EXTENSIONS
    ]
    for candidate in candidates:
        if candidate.name.lower() == needle:
            return candidate.read_text(encoding='utf-8')
    for candidate in candidates:
        if candidate.stem.lower() == needle:
            return candidate.read_text(encoding='utf-8')
    return None


# ---------------------------------------------------------------------------
# A2UI components (loaded eagerly at discovery — they feed the channel catalog)
# ---------------------------------------------------------------------------


def format_components_catalog(skills: list[SkillDoc]) -> str:
    """Compact bullet catalog of every component the loaded skills declare."""
    sections: list[str] = []
    for skill in sorted(skills, key=lambda s: s.name):
        for component in skill.components:
            top_keys = _top_level_prop_keys(component.props)
            keys_str = ', '.join(top_keys) if top_keys else '(no props)'
            desc = component.description or '(no description)'
            sections.append(f'- **{component.name}** ({skill.name}) — {desc} · props: {keys_str}')
    return '\n'.join(sections)


def _top_level_prop_keys(props_schema: dict) -> list[str]:
    if not isinstance(props_schema, dict):
        return []
    properties = props_schema.get('properties')
    if not isinstance(properties, dict):
        return []
    return list(properties.keys())
