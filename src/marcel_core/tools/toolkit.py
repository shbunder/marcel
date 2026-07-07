"""Toolkit dispatcher tool for Marcel.

Exposes a single pydantic-ai tool that dispatches to the toolkit registry
(toolkit habitats in marcel-zoo that register handlers via
:func:`marcel_core.plugin.marcel_tool`). The model calls it as::

    toolkit(id="docker.list", params={"filter": "running"})

Habitats run **in-process** (lean isolation, ADR-260628-6101c5). A slow or
hung handler is contained here by a **call-boundary timeout**
(``_HANDLER_TIMEOUT``): the dispatch is wrapped in ``asyncio.wait_for`` so
a misbehaving handler cannot stall the turn — the process-isolation the
old UDS mesh provided is replaced by this timeout plus try/except.

When the model calls a toolkit handler without having previously loaded
the skill's documentation via ``marcel(action="read_skill")``, this tool
auto-injects the skill docs as a prefix to the response (safety net).
"""

from __future__ import annotations

import asyncio
import logging

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps
from marcel_core.skills.executor import run
from marcel_core.skills.registry import get_skill, list_skills

log = logging.getLogger(__name__)

_HANDLER_TIMEOUT = 60.0
"""Seconds a single in-process toolkit handler may run before it is
cancelled — the call-boundary containment that replaces UDS isolation."""


async def toolkit(
    ctx: RunContext[MarcelDeps],
    id: str,
    params: dict[str, str] | None = None,
) -> str:
    """Execute a registered toolkit handler.

    Toolkit habitats are external services/capabilities that Marcel can call:
    calendar, banking, smart home, etc. Each habitat is documented in
    ``~/.marcel/skills/{name}/SKILL.md``.

    Use ``marcel(action="read_skill", name="...")`` first to load full skill
    documentation before calling a handler you haven't used before.

    Args:
        ctx: Agent context with user information.
        id: The handler ID (e.g., ``"banking.balance"``).
        params: Handler-specific parameters (see SKILL.md for each habitat).

    Returns:
        Result string from the handler.
    """
    log.info('[toolkit] user=%s id=%s', ctx.deps.user_slug, id)

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
    if skill_family not in ctx.deps.turn.read_skills:
        from marcel_core.skills.loader import get_skill_content

        content = get_skill_content(skill_family, ctx.deps.user_slug)
        if content:
            prefix = f'[Auto-loaded {skill_family} skill docs]\n{content}\n\n---\n\n'
        ctx.deps.turn.read_skills.add(skill_family)

    try:
        result = await asyncio.wait_for(
            run(config, params, ctx.deps.user_slug),
            timeout=_HANDLER_TIMEOUT,
        )
        return prefix + result
    except asyncio.TimeoutError:
        log.error('[toolkit] handler %s timed out after %.0fs', id, _HANDLER_TIMEOUT)
        return f'{prefix}Error executing {id}: handler timed out after {_HANDLER_TIMEOUT:.0f}s'
    except Exception as exc:
        log.exception('[toolkit] handler execution failed')
        return f'{prefix}Error executing {id}: {exc}'
