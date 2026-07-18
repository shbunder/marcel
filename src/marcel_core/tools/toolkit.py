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

Skills are deferred capabilities (FEAT-260718-85b545): the model loads a
skill's documentation via ``load_capability`` before calling its toolkit,
so there is no auto-inject-on-first-call safety net here anymore.
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

    Load the skill via ``load_capability`` first to bring its documentation
    into context before calling a handler you haven't used before.

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

    # Skills are deferred capabilities (FEAT-260718-85b545): the model loads
    # a skill's docs via load_capability before using its toolkit, so the
    # old auto-inject-on-first-call crutch is gone.
    try:
        return await asyncio.wait_for(
            run(config, params, ctx.deps.user_slug),
            timeout=_HANDLER_TIMEOUT,
        )
    except asyncio.TimeoutError:
        log.error('[toolkit] handler %s timed out after %.0fs', id, _HANDLER_TIMEOUT)
        return f'Error executing {id}: handler timed out after {_HANDLER_TIMEOUT:.0f}s'
    except Exception as exc:
        log.exception('[toolkit] handler execution failed')
        return f'Error executing {id}: {exc}'
