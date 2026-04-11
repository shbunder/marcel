"""Integration dispatcher tool for Marcel.

Exposes a single pydantic-ai tool that dispatches to the skills registry.
This preserves the @register decorator pattern while keeping tool count minimal.

When the model calls an integration without having previously loaded the
skill's documentation via ``marcel(action="read_skill")``, this tool
auto-injects the skill docs as a prefix to the response (safety net).
"""

from __future__ import annotations

import logging

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps
from marcel_core.skills.executor import run
from marcel_core.skills.registry import get_skill, list_skills

log = logging.getLogger(__name__)


async def integration(
    ctx: RunContext[MarcelDeps],
    id: str,
    params: dict[str, str] | None = None,
) -> str:
    """Execute a registered integration skill.

    Integrations are external services that Marcel can call: calendar, banking,
    smart home, etc. Each integration is documented in .marcel/skills/{name}/SKILL.md.

    Use ``marcel(action="read_skill", name="...")`` first to load full skill
    documentation before calling an integration you haven't used before.

    Args:
        ctx: Agent context with user information.
        id: The integration ID (e.g., "banking.balance").
        params: Skill-specific parameters (see SKILL.md for each integration).

    Returns:
        Result string from the integration.
    """
    log.info('[integration] user=%s id=%s', ctx.deps.user_slug, id)

    if params is None:
        params = {}

    try:
        config = get_skill(id)
    except KeyError as exc:
        available = list_skills()
        return f'Error: {exc}\n\nAvailable skills: {", ".join(available)}'

    # Auto-inject skill docs if the model hasn't read them yet this turn.
    # This is a safety net — the model should ideally call
    # marcel(action="read_skill") first, but if it doesn't, we prepend the
    # docs so the model has full context for interpreting the result.
    prefix = ''
    skill_family = id.split('.')[0]
    if skill_family not in ctx.deps.read_skills:
        from marcel_core.skills.loader import get_skill_content

        content = get_skill_content(skill_family, ctx.deps.user_slug)
        if content:
            prefix = f'[Auto-loaded {skill_family} skill docs]\n{content}\n\n---\n\n'
        ctx.deps.read_skills.add(skill_family)

    try:
        result = await run(config, params, ctx.deps.user_slug)
        return prefix + result
    except Exception as exc:
        log.exception('[integration] Skill execution failed')
        return f'{prefix}Error executing {id}: {exc}'
