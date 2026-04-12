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
    """Return the skills directory under the data root."""
    from marcel_core.config import settings

    return settings.data_dir / 'skills'


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


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown file.

    Returns (frontmatter_dict, body_text).  If no frontmatter is found,
    returns an empty dict and the full text.
    """
    if not text.startswith('---'):
        return {}, text
    end = text.find('---', 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + 3 :].strip()
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, body


def _check_requirements(requires: dict, user_slug: str) -> bool:
    """Check whether a skill's requirements are met for the given user.

    Requirement types (all fields in the ``requires`` dict are optional):
    - ``credentials``: list of key names that must exist in the user's
      credential store.
    - ``env``: list of environment variable names that must be set.
    - ``files``: list of filenames that must exist in the user's data
      directory.

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
        cred_keys = requires.get('credentials', []) if requires else []

        # Update component skill names to match the resolved skill name
        for c in components:
            c.skill = name

        if _check_requirements(requires, user_slug):
            return SkillDoc(
                name=name,
                description=description,
                content=body,
                is_setup=False,
                source=source,
                credential_keys=cred_keys,
                components=components,
            )

        # Requirements not met — fall back to SETUP.md
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
    """Discover and load all skills from the data root skills directory.

    Scans ``<data_root>/skills/`` for skill directories.

    Args:
        user_slug: The user slug, used for per-user requirement checks.

    Returns:
        List of SkillDoc instances sorted by name.
    """
    skills: dict[str, SkillDoc] = {}

    skills_path = _skills_dir()
    if skills_path.is_dir():
        for entry in sorted(skills_path.iterdir()):
            if entry.is_dir() and not entry.name.startswith(('_', '.')):
                doc = _load_skill_dir(entry, user_slug, source='data')
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
