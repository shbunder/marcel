"""MarcelPolicy — tool interception as a pydantic-ai v2 capability.

Every tool call the agent makes passes through the turn's lifecycle event
bus (:mod:`marcel_sdk.events`) via the v2 ``wrap_tool_execute`` hook:

- a ``tool_call`` event fires **before** the tool runs — handlers may
  mutate ``args`` in place (rewrite arguments) or call ``event.deny(...)``
  to block execution (first blocker wins; the reason becomes the tool
  result so the model can adapt);
- a ``tool_result`` event fires **after** — handlers may rewrite the
  returned text.

This is the single seam for the three core gates (self-mod path guard,
role gate, command policy/approval — registered by
:func:`marcel_core.harness.core_handlers.register_core_handlers`) and for
``marcel_sdk`` extension handlers, replayed onto each turn's bus. It
replaces the pre-v2 ``MarcelBusToolset`` WrapperToolset with identical
semantics; unlike the toolset wrapper, the hook also covers tools any
future capability contributes, not just the kernel's own FunctionToolset.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.capabilities.abstract import ValidatedToolArgs, WrapToolExecuteHandler
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from marcel_core.harness.context import MarcelDeps
from marcel_sdk.events import EventContext, ToolCallEvent, ToolResultEvent


@dataclass
class MarcelPolicy(AbstractCapability[MarcelDeps]):
    """Routes every tool call through the turn's event bus.

    Passes calls straight through when no bus is wired for the turn
    (``ctx.deps.turn.event_bus is None``) — e.g. the job/subagent paths
    that do not set one up — so behaviour there is unchanged.
    """

    async def wrap_tool_execute(
        self,
        ctx: RunContext[MarcelDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        bus = ctx.deps.turn.event_bus
        if bus is None:
            return await handler(args)

        ectx = EventContext(user_slug=ctx.deps.user_slug, role=ctx.deps.role, channel=ctx.deps.channel)

        # tool_call — handlers may mutate `args` in place or block the call.
        call_event = await bus.emit(ToolCallEvent(tool_name=call.tool_name, args=args), ectx)
        if call_event.blocked:
            # Surface the reason to the model as the tool result so it can
            # adapt; the underlying tool never runs.
            return call_event.block_reason or f'Tool {call.tool_name!r} was blocked.'

        result = await handler(call_event.args)

        # tool_result — handlers may rewrite the output. Only override the
        # return value when a handler actually changed it, so non-str tool
        # results (should any appear) keep their original object otherwise.
        original_text = result if isinstance(result, str) else str(result)
        result_event = await bus.emit(
            ToolResultEvent(tool_name=call.tool_name, result=original_text),
            ectx,
        )
        if result_event.result != original_text:
            return result_event.result
        return result
