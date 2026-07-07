"""Event-bus interception layer over the agent's tools.

Wraps the agent's ``FunctionToolset`` so every tool call passes through the
turn's lifecycle event bus (:mod:`marcel_sdk.events`):

- a ``tool_call`` event fires **before** the tool runs — handlers may
  mutate ``args`` in place (rewrite arguments) or call ``event.deny(...)``
  to block execution (first blocker wins);
- a ``tool_result`` event fires **after** — handlers may rewrite the
  returned text.

This is the single seam that lets role-gating, the self-modification path
guard, and extension handlers observe/gate tool execution without bespoke
checks inside each tool body. It is the Python equivalent of pi's
``beforeToolCall`` / ``afterToolCall`` hooks, implemented with pydantic-ai's
:class:`~pydantic_ai.toolsets.WrapperToolset` — no change to the agent loop
itself (ADR-260628-7b0cec).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets import ToolsetTool, WrapperToolset

from marcel_core.harness.context import MarcelDeps
from marcel_sdk.events import EventContext, ToolCallEvent, ToolResultEvent


@dataclass
class MarcelBusToolset(WrapperToolset[MarcelDeps]):
    """Routes every wrapped tool call through the turn's event bus.

    Passes calls straight through when no bus is wired for the turn
    (``ctx.deps.turn.event_bus is None``) — e.g. the job/subagent paths
    that do not set one up pre-F5 — so behaviour there is unchanged.
    """

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[MarcelDeps],
        tool: ToolsetTool[MarcelDeps],
    ) -> Any:
        bus = ctx.deps.turn.event_bus
        if bus is None:
            return await self.wrapped.call_tool(name, tool_args, ctx, tool)

        ectx = EventContext(user_slug=ctx.deps.user_slug, role=ctx.deps.role, channel=ctx.deps.channel)

        # tool_call — handlers may mutate `args` in place or block the call.
        call_event = await bus.emit(ToolCallEvent(tool_name=name, args=tool_args), ectx)
        if call_event.blocked:
            # Surface the reason to the model as the tool result so it can
            # adapt; the underlying tool never runs.
            return call_event.block_reason or f'Tool {name!r} was blocked.'

        result = await self.wrapped.call_tool(name, call_event.args, ctx, tool)

        # tool_result — handlers may rewrite the output. Only override the
        # return value when a handler actually changed it, so non-str tool
        # results (should any appear) keep their original object otherwise.
        original_text = result if isinstance(result, str) else str(result)
        result_event = await bus.emit(
            ToolResultEvent(tool_name=name, result=original_text),
            ectx,
        )
        if result_event.result != original_text:
            return result_event.result
        return result
