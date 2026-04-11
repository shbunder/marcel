"""Skill-related actions for the ``marcel`` tool."""

from __future__ import annotations

import logging

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps

log = logging.getLogger(__name__)


async def read_skill(ctx: RunContext[MarcelDeps], name: str | None) -> str:
    """Load full documentation for a skill by name."""
    if not name:
        return 'Error: name= is required for read_skill action.'

    from marcel_core.skills.loader import get_skill_content, load_skills

    log.info('[marcel:read_skill] user=%s skill=%s', ctx.deps.user_slug, name)

    content = get_skill_content(name, ctx.deps.user_slug)
    if content is None:
        available = [s.name for s in load_skills(ctx.deps.user_slug)]
        return f'Unknown skill: {name!r}. Available skills: {", ".join(available)}'

    # Track that this skill has been read (prevents duplicate auto-inject in integration tool)
    ctx.deps.turn.read_skills.add(name)
    return content
