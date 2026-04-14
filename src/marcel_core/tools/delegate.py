"""``delegate`` tool — spawn a subagent with a scoped system prompt and tool pool.

Parent agents invoke ``delegate(subagent_type="explore", prompt="...")`` to
hand off a focused task to a purpose-built child agent. The child runs in a
fresh pydantic-ai ``Agent`` with:

- A fresh system prompt drawn from the subagent's markdown body
- A filtered tool pool (the agent frontmatter's ``tools`` allowlist minus
  ``disallowed_tools``, always minus ``delegate`` itself unless the agent
  explicitly opts in)
- Its own model (either ``inherit`` from the parent or a fully-qualified
  pydantic-ai string from the frontmatter)
- A usage limit derived from ``max_requests``
- A wall-clock timeout from ``timeout_seconds``

The result of the run is returned as a single string back to the parent.
Background / async delegation is deferred to a follow-up issue (see the v1
scope note in ISSUE-074).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging

from pydantic_ai import RunContext
from pydantic_ai.usage import UsageLimits

from marcel_core.agents import AgentNotFoundError, load_agent
from marcel_core.harness.context import MarcelDeps, TurnState

log = logging.getLogger(__name__)


def _resolve_tool_filter(agent_tools: list[str] | None, disallowed: list[str]) -> set[str] | None:
    """Compute the ``tool_filter`` set passed to ``create_marcel_agent``.

    Precedence:

    1. If the agent declares a ``tools`` allowlist, start from that. Otherwise
       return ``None`` — meaning "use the role default pool" — after applying
       the disallowed-tools subtraction and the recursion guard below.
    2. Always remove ``delegate`` from the pool unless the agent *explicitly*
       lists it in its allowlist. This is the recursion guard: a subagent
       cannot spawn further subagents by default.
    3. Subtract ``disallowed_tools`` last so an agent can allowlist broadly
       and then carve out specific exclusions.
    """
    disallowed_set = set(disallowed)

    if agent_tools is None:
        # No allowlist — we return None, but we still need to enforce the
        # recursion guard. That's done in the caller by special-casing the
        # None path: we emit an allowlist that is "everything except
        # delegate + disallowed", built from the registry at call time.
        return None

    allowlist = set(agent_tools)
    allowlist -= disallowed_set
    # Recursion guard — only keep ``delegate`` if the agent asked for it.
    if 'delegate' not in agent_tools:
        allowlist.discard('delegate')
    return allowlist


def _default_pool_minus(role: str, disallowed: list[str], include_delegate: bool) -> set[str]:
    """Build a tool_filter equivalent to the role default with exclusions.

    Used when an agent frontmatter omits ``tools`` entirely — we still need
    an explicit set so we can enforce the recursion guard (delegate off by
    default) and any ``disallowed_tools`` the agent declared.
    """
    from marcel_core.harness.agent import available_tool_names

    names = available_tool_names(role)
    names -= set(disallowed)
    if not include_delegate:
        names.discard('delegate')
    return names


async def delegate(
    ctx: RunContext[MarcelDeps],
    subagent_type: str,
    prompt: str,
    description: str = '',
) -> str:
    """Delegate a focused subtask to a named subagent.

    The subagent runs in a fresh pydantic-ai ``Agent`` built from the
    markdown definition at ``<data_root>/agents/<subagent_type>.md``. Its
    tool pool, model, and usage limits are drawn from the agent's
    frontmatter; see ``docs/subagents.md`` for the schema.

    Use this when the parent task breaks naturally into a scoped, short-lived
    investigation or planning step that benefits from a constrained context —
    for example, a read-only ``explore`` pass to find the right files before
    editing, or a ``plan`` pass to turn a fuzzy ask into concrete steps.

    Args:
        ctx: Agent context with user, conversation, and model info.
        subagent_type: The ``name`` of the subagent to invoke. Must match a
            file under ``<data_root>/agents/`` (without the ``.md`` suffix).
            Call ``marcel(action="list_agents")`` if you're unsure which
            agents exist.
        prompt: The task instructions for the subagent. Be specific — the
            subagent starts with no memory of the parent conversation.
            Include any file paths, line numbers, and context it will need.
        description: Optional 3-5 word summary of what the subagent will do.
            Shown in logs; does not affect execution.

    Returns:
        The subagent's final output string. Errors (agent not found,
        timeout, model failure) are returned as error messages prefixed
        with ``'delegate error:'`` rather than raised, so the parent agent
        can decide how to recover.
    """
    from marcel_core.config import settings
    from marcel_core.harness.agent import create_marcel_agent, default_model

    try:
        agent_doc = load_agent(subagent_type)
    except AgentNotFoundError as exc:
        log.warning('delegate: agent not found: %s', subagent_type)
        return f'delegate error: {exc}'

    # Tool filter resolution — explicit allowlist if given, otherwise start
    # from the role-default pool. Always enforce the recursion guard.
    if agent_doc.tools is None:
        explicit_delegate = False  # agent didn't set `tools:` at all
        tool_filter = _default_pool_minus(
            role=ctx.deps.role,
            disallowed=agent_doc.disallowed_tools,
            include_delegate=explicit_delegate,
        )
    else:
        resolved = _resolve_tool_filter(agent_doc.tools, agent_doc.disallowed_tools)
        assert resolved is not None  # _resolve_tool_filter only returns None for the ``None`` input branch
        tool_filter = resolved

    # Model resolution — agent's own model, parent's model, or default.
    # ``tier:<name>`` sentinels (from agent frontmatter like ``model: power``)
    # are resolved against settings here so every agent that references a
    # tier picks up env-var changes without a restart. See ISSUE-076.
    model = agent_doc.model or ctx.deps.model or default_model()
    if model.startswith('tier:'):
        tier_name = model[len('tier:') :]
        tier_map = {
            'standard': settings.marcel_standard_model,
            'backup': settings.marcel_backup_model,
            'fallback': settings.marcel_fallback_model,
            'power': settings.marcel_power_model,
        }
        if tier_name not in tier_map:
            return f'delegate error: subagent {subagent_type!r} references unknown tier {tier_name!r}'
        resolved_model = tier_map[tier_name]
        if not resolved_model:
            return (
                f'delegate error: subagent {subagent_type!r} requires tier {tier_name!r} but '
                f'MARCEL_{tier_name.upper()}_MODEL is not set'
            )
        model = resolved_model

    # Fresh system prompt: the subagent knows only what its markdown says
    # plus the parent's prompt text. No MARCEL.md, no memory, no channel
    # guidance — a clean slate. If the parent needs the subagent to know
    # context, it has to pass that in the ``prompt`` argument.
    system_prompt = agent_doc.system_prompt or 'You are a Marcel subagent.'

    # Fresh deps — same user and role, but a derived conversation id so the
    # subagent's tool calls don't commingle with the parent turn's state.
    # We deliberately copy the *immutable* identity fields and build a fresh
    # ``TurnState`` so per-turn flags (notified, web_search_count) do not
    # leak in either direction.
    sub_deps = dataclasses.replace(
        ctx.deps,
        conversation_id=f'{ctx.deps.conversation_id}:delegate:{subagent_type}',
        model=model,
        turn=TurnState(),
    )

    usage_limits = None
    if agent_doc.max_requests is not None:
        usage_limits = UsageLimits(request_limit=agent_doc.max_requests)

    log.info(
        'delegate: subagent=%s model=%s tools=%d timeout=%ds description=%r',
        subagent_type,
        model,
        len(tool_filter),
        agent_doc.timeout_seconds,
        description,
    )

    try:
        sub_agent = create_marcel_agent(
            model=model,
            system_prompt=system_prompt,
            role=sub_deps.role,
            tool_filter=tool_filter,
        )
    except Exception as exc:
        log.exception('delegate: failed to build subagent %s', subagent_type)
        return f'delegate error: could not build subagent {subagent_type!r}: {exc}'

    try:
        result = await asyncio.wait_for(
            sub_agent.run(prompt, deps=sub_deps, usage_limits=usage_limits),
            timeout=agent_doc.timeout_seconds,
        )
    except asyncio.TimeoutError:
        log.warning('delegate: subagent %s timed out after %ds', subagent_type, agent_doc.timeout_seconds)
        return f'delegate error: subagent {subagent_type!r} timed out after {agent_doc.timeout_seconds}s'
    except Exception as exc:
        log.exception('delegate: subagent %s failed', subagent_type)
        return f'delegate error: subagent {subagent_type!r} failed: {exc}'

    return str(result.output)
