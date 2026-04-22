"""Skill document loader — discovers SKILL.md files from the data root.

Skills live at ``<data_root>/skills/`` (``~/.marcel/skills/`` or
``$MARCEL_DATA_DIR/skills/`` in Docker).

Each integration skill can have a ``SETUP.md`` fallback that activates when
the integration's requirements are not met (missing credentials, env vars,
or files).  This guides new users through first-time setup.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from marcel_core.skills.components import ComponentSchema, parse_components_yaml

log = logging.getLogger(__name__)


def _skills_dir() -> Path:
    """Return the skills directory under the data root.

    Backwards-compatible single-path accessor — prefer :func:`_skill_dirs`
    for new callsites that need the full search order (zoo + data root).
    """
    from marcel_core.config import settings

    return settings.data_dir / 'skills'


def _skill_dirs() -> list[Path]:
    """Return all skill directories in load order.

    Skills are discovered from two sources:

    1. ``<MARCEL_ZOO_DIR>/skills/`` — habitats from the marcel-zoo checkout
       (skipped when ``MARCEL_ZOO_DIR`` is unset).
    2. ``<MARCEL_DATA_DIR>/skills/`` — user-installed/customized skills.

    The data-root entry comes last so a user customization with the same
    skill name overrides the zoo habitat.

    Note: the data-root entry is sourced from :func:`_skills_dir` so test
    monkeypatches against that accessor continue to work.
    """
    from marcel_core.config import settings

    dirs: list[Path] = []
    zoo = settings.zoo_dir
    if zoo is not None:
        zoo_skills = zoo / 'skills'
        if zoo_skills.is_dir():
            dirs.append(zoo_skills)
    data_skills = _skills_dir()
    if data_skills.is_dir():
        dirs.append(data_skills)
    return dirs


_VALID_PREFERRED_TIERS = {'local', 'fast', 'standard', 'power'}


@dataclass
class SkillDoc:
    """A loaded skill document ready for injection into the system prompt."""

    name: str
    description: str
    content: str
    is_setup: bool  # True if this is the SETUP.md fallback
    source: str  # 'project' or 'home'
    credential_keys: list[str] = dataclasses.field(default_factory=list)
    """Credential keys declared in requires.credentials (for auto-injection)."""
    components: list[ComponentSchema] = dataclasses.field(default_factory=list)
    """A2UI component schemas declared in this skill's components.yaml."""
    preferred_tier: str | None = None
    """Optional ``fast`` / ``standard`` / ``power`` — per-turn tier override while this skill is active.

    Does NOT mutate the session tier; the override applies only to turns
    where this skill is the active one. See ISSUE-e0db47.
    """


def _parse_preferred_tier(value: object, skill_name: str) -> str | None:
    """Validate and return the ``preferred_tier`` frontmatter value.

    Unknown values are dropped with a warning rather than raising — a broken
    frontmatter edit must never hide the skill from the agent.
    """
    if value is None:
        return None
    if not isinstance(value, str) or value not in _VALID_PREFERRED_TIERS:
        log.warning(
            'skills: %s declares invalid preferred_tier=%r — must be one of %s; ignoring',
            skill_name,
            value,
            sorted(_VALID_PREFERRED_TIERS),
        )
        return None
    return value


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown file.

    Returns (frontmatter_dict, body_text).  If no frontmatter is found,
    returns an empty dict and the full text.
    """
    if not text.startswith('---'):
        return {}, _strip_argument_template(text)
    end = text.find('---', 3)
    if end == -1:
        return {}, _strip_argument_template(text)
    fm_text = text[3:end].strip()
    body = text[end + 3 :].strip()
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, _strip_argument_template(body)


def _strip_argument_template(body: str) -> str:
    """Drop the Claude Code ``Help the user with: $ARGUMENTS`` boilerplate.

    Older SKILL.md files (copied from the Claude Code skill format) lead
    with ``Help the user with: $ARGUMENTS``. The ``$ARGUMENTS`` placeholder
    is never substituted in Marcel, so the line is pure noise that tends
    to confuse the model into ending a turn after a bare acknowledgment.
    Removed defensively at load time so stale data-root copies stay clean.
    """
    lines = body.split('\n')
    cleaned: list[str] = []
    for line in lines:
        if line.strip() == 'Help the user with: $ARGUMENTS':
            continue
        cleaned.append(line)
    return '\n'.join(cleaned).lstrip('\n')


def _check_requirements(requires: dict, user_slug: str) -> bool:
    """Check whether a skill's requirements are met for the given user.

    Requirement types (all fields in the ``requires`` dict are optional):
    - ``credentials``: list of key names that must exist in the user's
      credential store.
    - ``env``: list of environment variable names that must be set.
    - ``files``: list of filenames that must exist in the user's data
      directory.
    - ``packages``: list of importable Python module names.

    Returns True if all requirements are satisfied (or if ``requires`` is
    empty).
    """
    if not requires:
        return True

    # Check credentials
    cred_keys = requires.get('credentials', [])
    if cred_keys:
        try:
            from marcel_core.storage.credentials import load_credentials

            creds = load_credentials(user_slug)
            for key in cred_keys:
                if not creds.get(key):
                    log.debug('Skill requirement not met: credential %s missing for user %s', key, user_slug)
                    return False
        except Exception:
            log.debug('Could not load credentials for user %s', user_slug, exc_info=True)
            return False

    # Check environment variables
    env_keys = requires.get('env', [])
    for key in env_keys:
        if not os.environ.get(key):
            log.debug('Skill requirement not met: env var %s not set', key)
            return False

    # Check Python packages
    packages = requires.get('packages', [])
    for pkg in packages:
        if importlib.util.find_spec(pkg) is None:
            log.debug('Skill requirement not met: package %s not installed', pkg)
            return False

    # Check files in user data directory
    file_names = requires.get('files', [])
    if file_names:
        try:
            from marcel_core.storage._root import data_root

            user_dir = data_root() / 'users' / user_slug
            for fname in file_names:
                if not (user_dir / fname).exists():
                    log.debug('Skill requirement not met: file %s missing for user %s', fname, user_slug)
                    return False
        except Exception:
            log.debug('Could not check files for user %s', user_slug, exc_info=True)
            return False

    return True


def _check_depends_on(depends_on: list[str], user_slug: str) -> bool:
    """Check that every listed integration's requires are met.

    Each entry in ``depends_on`` names an integration (e.g. ``"docker"``).
    The integration's ``integration.yaml`` is consulted via
    :func:`get_integration_metadata`; its ``requires:`` block is then
    checked using :func:`_check_requirements`.

    Returns False (not met) when:
    - an integration name is not registered in the metadata registry
      (zoo not loaded, integration.yaml missing, or load failure), OR
    - any integration's own requires are not satisfied for *user_slug*.

    A missing integration is treated as unmet so the user is shown the
    skill's SETUP.md fallback rather than a SKILL.md they cannot exercise.
    """
    if not depends_on:
        return True

    from marcel_core.toolkit import get_integration_metadata

    for name in depends_on:
        meta = get_integration_metadata(name)
        if meta is None:
            log.debug(
                'Skill depends_on %r but integration metadata is not registered (zoo not '
                'loaded or integration.yaml missing); treating as unmet',
                name,
            )
            return False
        if not _check_requirements(meta.requires, user_slug):
            return False
    return True


def _normalize_depends_on(value: object) -> list[str]:
    """Normalize the ``depends_on`` frontmatter field to a list of names.

    Accepts a list, a single string, or absent/empty. Anything else is
    treated as absent with a warning.
    """
    if value is None or value == '':
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    log.warning('skills: depends_on must be a list of strings or a single string; got %r — ignoring', value)
    return []


def _load_skill_dir(skill_path: Path, user_slug: str, source: str) -> SkillDoc | None:
    """Load a single skill from its directory, applying fallback logic.

    If SKILL.md exists and its ``requires`` are met, return the skill doc.
    If requirements are NOT met and SETUP.md exists, return the setup doc.
    If only SKILL.md exists without ``requires``, always return it.
    """
    skill_md = skill_path / 'SKILL.md'
    setup_md = skill_path / 'SETUP.md'
    components_yaml = skill_path / 'components.yaml'

    if not skill_md.exists() and not setup_md.exists():
        return None

    # Parse A2UI component schemas if present (attached to whichever doc is returned)
    components: list[ComponentSchema] = []
    if components_yaml.exists():
        skill_name = skill_path.name  # tentative — overridden below if frontmatter has name
        components = parse_components_yaml(components_yaml, skill_name)

    # Try SKILL.md first
    if skill_md.exists():
        text = skill_md.read_text(encoding='utf-8')
        fm, body = _parse_frontmatter(text)
        name = fm.get('name', skill_path.name)
        description = fm.get('description', '')
        requires = fm.get('requires', {})
        depends_on = _normalize_depends_on(fm.get('depends_on'))
        # Credentials may be declared inline (legacy frontmatter) OR via
        # depends_on (the integration's integration.yaml). Aggregate both
        # for the system-prompt auto-injection list — the agent benefits
        # from seeing every credential the skill might touch.
        cred_keys = list(requires.get('credentials', []) if requires else [])
        if depends_on:
            from marcel_core.toolkit import get_integration_metadata

            for dep_name in depends_on:
                dep_meta = get_integration_metadata(dep_name)
                if dep_meta is not None:
                    cred_keys.extend(dep_meta.requires.get('credentials', []) or [])
        preferred_tier = _parse_preferred_tier(fm.get('preferred_tier'), name)

        # Update component skill names to match the resolved skill name
        for c in components:
            c.skill = name

        if _check_requirements(requires, user_slug) and _check_depends_on(depends_on, user_slug):
            return SkillDoc(
                name=name,
                description=description,
                content=body,
                is_setup=False,
                source=source,
                credential_keys=cred_keys,
                components=components,
                preferred_tier=preferred_tier,
            )

        # Requirements not met — fall back to SETUP.md. SETUP flows must not
        # burn a more-expensive tier, so the preferred_tier is deliberately
        # dropped for the setup variant.
        if setup_md.exists():
            setup_text = setup_md.read_text(encoding='utf-8')
            setup_fm, setup_body = _parse_frontmatter(setup_text)
            return SkillDoc(
                name=setup_fm.get('name', name),
                description=setup_fm.get('description', f'Setup guide for {name}'),
                content=setup_body,
                is_setup=True,
                source=source,
                credential_keys=cred_keys,
                components=components,
            )

        # No SETUP.md — still return SKILL.md (agent can handle the error at runtime)
        return SkillDoc(
            name=name,
            description=description,
            content=body,
            is_setup=False,
            source=source,
            credential_keys=cred_keys,
            components=components,
            preferred_tier=preferred_tier,
        )

    # Only SETUP.md exists (no SKILL.md) — unusual but supported
    if setup_md.exists():
        text = setup_md.read_text(encoding='utf-8')
        fm, body = _parse_frontmatter(text)
        return SkillDoc(
            name=fm.get('name', skill_path.name),
            description=fm.get('description', ''),
            content=body,
            is_setup=True,
            source=source,
            components=components,
        )

    return None


def load_skills(user_slug: str) -> list[SkillDoc]:
    """Discover and load all skills from every configured skills directory.

    Walks the directories returned by :func:`_skill_dirs` in load order:

    1. ``<MARCEL_ZOO_DIR>/skills/`` (when set) — habitats from marcel-zoo.
    2. ``<MARCEL_DATA_DIR>/skills/`` — user-installed/customized skills.

    When the same skill name is found in both, the later entry wins, so a
    user customization in the data root overrides the zoo habitat. The
    ``source`` field on the returned doc reflects where it came from.

    Args:
        user_slug: The user slug, used for per-user requirement checks.

    Returns:
        List of SkillDoc instances sorted by name.
    """
    from marcel_core.config import settings

    zoo = settings.zoo_dir
    zoo_skills = (zoo / 'skills').resolve() if zoo is not None else None

    skills: dict[str, SkillDoc] = {}

    for skills_path in _skill_dirs():
        source = 'zoo' if zoo_skills is not None and skills_path.resolve() == zoo_skills else 'data'
        for entry in sorted(skills_path.iterdir()):
            if entry.is_dir() and not entry.name.startswith(('_', '.')):
                doc = _load_skill_dir(entry, user_slug, source=source)
                if doc:
                    skills[doc.name] = doc

    return sorted(skills.values(), key=lambda s: s.name)


def format_skills_for_prompt(skills: list[SkillDoc]) -> str:
    """Format loaded skills into a string suitable for the system prompt.

    Each skill becomes a section with its name and content.  Setup docs
    are clearly marked so the agent knows to guide setup rather than
    attempt to use the integration.

    .. deprecated::
        Use :func:`format_skill_index` for the system prompt and
        :func:`get_skill_content` for on-demand loading.
    """
    if not skills:
        return ''

    sections: list[str] = []
    for skill in skills:
        if skill.is_setup:
            sections.append(f'### {skill.name} (not configured)\n\n{skill.content}')
        else:
            sections.append(f'### {skill.name}\n\n{skill.content}')

    return '\n\n---\n\n'.join(sections)


def format_skill_index(skills: list[SkillDoc]) -> str:
    """Format a compact one-line-per-skill index for the system prompt.

    Only names and descriptions are included — full docs are loaded
    on-demand via ``marcel(action="read_skill", name="...")``.
    """
    if not skills:
        return ''

    lines: list[str] = []
    for skill in skills:
        status = ' (not configured)' if skill.is_setup else ''
        lines.append(f'- **{skill.name}**{status} — {skill.description}')
    return '\n'.join(lines)


def get_skill_content(skill_name: str, user_slug: str) -> str | None:
    """Load the full content of a single skill by name.

    Used by the ``marcel`` tool's ``read_skill`` action to serve skill
    docs on demand.

    Returns:
        The skill's full markdown body, or None if not found.
    """
    skills = load_skills(user_slug)
    for s in skills:
        if s.name == skill_name:
            return s.content
    return None


# File extensions that are exposed as named skill resources.
_RESOURCE_EXTENSIONS = frozenset({'.md', '.yaml', '.yml', '.json', '.txt', '.csv'})
# SKILL.md is the primary doc — not surfaced as a resource (use read_skill instead).
_SKILL_MD_NAME = 'SKILL.md'


def _find_skill_dir(skill_name: str) -> Path | None:
    """Locate the directory for *skill_name* across every skills source.

    Walks the directories returned by :func:`_skill_dirs` in reverse order
    so the data root wins over the zoo habitat — same precedence as
    :func:`load_skills`. Checks each skill dir's ``SKILL.md`` frontmatter
    ``name`` field, falling back to the directory name. Returns ``None``
    if not found.
    """
    for skills_path in reversed(_skill_dirs()):
        for entry in skills_path.iterdir():
            if not entry.is_dir() or entry.name.startswith(('_', '.')):
                continue
            skill_md = entry / _SKILL_MD_NAME
            if skill_md.exists():
                try:
                    text = skill_md.read_text(encoding='utf-8')
                    fm, _ = _parse_frontmatter(text)
                    resolved_name = fm.get('name', entry.name)
                except Exception:
                    resolved_name = entry.name
            else:
                resolved_name = entry.name

            if resolved_name == skill_name:
                return entry

    return None


def list_skill_resources(skill_name: str) -> list[str]:
    """Return the names of resource files available in *skill_name*'s directory.

    Resources are every file in the skill directory other than ``SKILL.md``
    whose extension is in :data:`_RESOURCE_EXTENSIONS`.  Names are returned
    as bare filenames (e.g. ``"SETUP.md"``, ``"feeds.yaml"``).

    Returns an empty list if the skill is not found or has no resources.
    """
    skill_dir = _find_skill_dir(skill_name)
    if skill_dir is None:
        return []

    resources: list[str] = []
    for f in sorted(skill_dir.iterdir()):
        if f.is_file() and f.name != _SKILL_MD_NAME and f.suffix.lower() in _RESOURCE_EXTENSIONS:
            resources.append(f.name)
    return resources


def get_skill_resource(skill_name: str, resource_name: str) -> str | None:
    """Return the content of a named resource file within *skill_name*'s directory.

    Resources are any files in the skill directory other than ``SKILL.md``
    with a recognised extension (Markdown, YAML, JSON, CSV, plain text).

    Matching is case-insensitive and tries both:
    - exact filename (e.g. ``"SETUP.md"``, ``"feeds.yaml"``)
    - stem only (e.g. ``"setup"`` → ``"SETUP.md"``, ``"feeds"`` → ``"feeds.yaml"``)

    Args:
        skill_name:   Skill name as declared in SKILL.md frontmatter.
        resource_name: Filename or stem to load.

    Returns:
        File content as a string, or ``None`` if not found.
    """
    skill_dir = _find_skill_dir(skill_name)
    if skill_dir is None:
        return None

    needle = resource_name.lower()
    candidates: list[Path] = [
        f
        for f in skill_dir.iterdir()
        if f.is_file() and f.name != _SKILL_MD_NAME and f.suffix.lower() in _RESOURCE_EXTENSIONS
    ]

    # 1. Exact filename match (case-insensitive)
    for candidate in candidates:
        if candidate.name.lower() == needle:
            return candidate.read_text(encoding='utf-8')

    # 2. Stem-only match (e.g. "feeds" matches "feeds.yaml")
    for candidate in candidates:
        if candidate.stem.lower() == needle:
            return candidate.read_text(encoding='utf-8')

    return None


def format_components_catalog(skills: list[SkillDoc]) -> str:
    """Format the A2UI component catalog for injection into the system prompt.

    Produces a compact bullet list of every component declared by the loaded
    skills together with its top-level prop keys, so the agent knows what
    components exist and what props they accept without consuming a large
    token budget on full JSON Schemas.

    Returns an empty string if no components are declared.
    """
    sections: list[str] = []
    for skill in sorted(skills, key=lambda s: s.name):
        if not skill.components:
            continue
        for component in skill.components:
            top_keys = _top_level_prop_keys(component.props)
            keys_str = ', '.join(top_keys) if top_keys else '(no props)'
            desc = component.description or '(no description)'
            sections.append(f'- **{component.name}** ({skill.name}) — {desc} · props: {keys_str}')

    return '\n'.join(sections)


def _top_level_prop_keys(props_schema: dict) -> list[str]:
    """Return the top-level property keys from a JSON Schema dict.

    Handles the common shape ``{type: object, properties: {a: ..., b: ...}}``.
    Returns an empty list for schemas that don't declare object properties.
    """
    if not isinstance(props_schema, dict):
        return []
    properties = props_schema.get('properties')
    if not isinstance(properties, dict):
        return []
    return list(properties.keys())
