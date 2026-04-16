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

    from marcel_core.skills.loader import get_skill_content, list_skill_resources, load_skills

    log.info('[marcel:read_skill] user=%s skill=%s', ctx.deps.user_slug, name)

    content = get_skill_content(name, ctx.deps.user_slug)
    if content is None:
        available = [s.name for s in load_skills(ctx.deps.user_slug)]
        return f'Unknown skill: {name!r}. Available skills: {", ".join(available)}'

    # Track that this skill has been read (prevents duplicate auto-inject in integration tool)
    ctx.deps.turn.read_skills.add(name)

    # Append a list of available resources so the agent knows what it can fetch.
    resources = list_skill_resources(name)
    if resources:
        resource_list = ', '.join(f'"{r}"' for r in resources)
        content = (
            content
            + f'\n\n**Available resources:** {resource_list} — '
            + f'load with `marcel(action="read_skill_resource", name="{name}", resource=<name>)`'
        )

    return content


async def read_skill_resource(
    ctx: RunContext[MarcelDeps],
    skill: str | None,
    resource: str | None,
) -> str:
    """Load a named resource file from a skill's directory.

    Resources are files in the skill directory other than ``SKILL.md``:
    typically ``SETUP.md``, ``components.yaml``, ``feeds.yaml``, etc.

    Use ``marcel(action="read_skill", name="<skill>")`` first to see what
    resources are available for a skill.

    Args:
        skill:    Skill name (same as used in ``read_skill``).
        resource: Filename or stem to load, e.g. ``"feeds"`` or ``"feeds.yaml"``.
    """
    if not skill:
        return 'Error: name= (skill name) is required for read_skill_resource action.'
    if not resource:
        return 'Error: resource= is required for read_skill_resource action.'

    from marcel_core.skills.loader import get_skill_resource, list_skill_resources

    log.info('[marcel:read_skill_resource] user=%s skill=%s resource=%s', ctx.deps.user_slug, skill, resource)

    content = get_skill_resource(skill, resource)
    if content is None:
        available = list_skill_resources(skill)
        if not available:
            return f'Skill {skill!r} not found or has no resource files.'
        resource_list = ', '.join(f'"{r}"' for r in available)
        return f'Resource {resource!r} not found in skill {skill!r}. Available resources: {resource_list}'

    return content
