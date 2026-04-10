"""Skill document loader — discovers SKILL.md files from .marcel/skills/ directories.

Reads skills from two locations (in order of precedence):
1. ``~/.marcel/skills/`` — user-level overrides and custom skills
2. ``<project>/.marcel/skills/`` — built-in skills shipped with Marcel

When a skill exists in both locations, the home directory version wins.

Each integration skill can have a ``SETUP.md`` fallback that activates when
the integration's requirements are not met (missing credentials, env vars,
or files).  This guides new users through first-time setup.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# Project root: assumes the loader is at src/marcel_core/skills/loader.py
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PROJECT_SKILLS = _PROJECT_ROOT / '.marcel' / 'skills'


def _home_skills_dir() -> Path:
    """Return the user-level skills directory.

    Uses ``MARCEL_DATA_DIR`` when set (Docker/production), otherwise falls
    back to ``~/.marcel/skills/``.  This matches the data-root resolution
    in ``storage._root``.
    """
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

    if not skill_md.exists() and not setup_md.exists():
        return None

    # Try SKILL.md first
    if skill_md.exists():
        text = skill_md.read_text(encoding='utf-8')
        fm, body = _parse_frontmatter(text)
        name = fm.get('name', skill_path.name)
        description = fm.get('description', '')
        requires = fm.get('requires', {})

        if _check_requirements(requires, user_slug):
            return SkillDoc(
                name=name,
                description=description,
                content=body,
                is_setup=False,
                source=source,
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
            )

        # No SETUP.md — still return SKILL.md (agent can handle the error at runtime)
        return SkillDoc(
            name=name,
            description=description,
            content=body,
            is_setup=False,
            source=source,
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
        )

    return None


def load_skills(user_slug: str) -> list[SkillDoc]:
    """Discover and load all skills from .marcel/skills/ directories.

    Scans both the project directory and the user's home directory.
    Home directory skills override project skills with the same name.

    Args:
        user_slug: The user slug, used for per-user requirement checks.

    Returns:
        List of SkillDoc instances sorted by name.
    """
    skills: dict[str, SkillDoc] = {}

    # Load project skills first (base layer)
    if _PROJECT_SKILLS.is_dir():
        for entry in sorted(_PROJECT_SKILLS.iterdir()):
            if entry.is_dir() and not entry.name.startswith(('_', '.')):
                doc = _load_skill_dir(entry, user_slug, source='project')
                if doc:
                    skills[doc.name] = doc

    # Load home skills (override layer)
    home_skills = _home_skills_dir()
    if home_skills.is_dir():
        for entry in sorted(home_skills.iterdir()):
            if entry.is_dir() and not entry.name.startswith(('_', '.')):
                doc = _load_skill_dir(entry, user_slug, source='home')
                if doc:
                    skills[doc.name] = doc

    return sorted(skills.values(), key=lambda s: s.name)


def format_skills_for_prompt(skills: list[SkillDoc]) -> str:
    """Format loaded skills into a string suitable for the system prompt.

    Each skill becomes a section with its name and content.  Setup docs
    are clearly marked so the agent knows to guide setup rather than
    attempt to use the integration.
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
