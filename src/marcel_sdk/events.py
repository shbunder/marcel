"""Typed lifecycle event bus for Marcel turns.

This is the mechanism by which core behaviours (role-gating, the
self-modification path guard, the approval flow) and extensions subscribe
to points in a turn instead of being bespoke code welded into the harness.
It mirrors the pi-agents extension event model, adapted to Python:

- An **event** is a small mutable dataclass carrying the data for one
  point in a turn. Its ``NAME`` class attribute is the string handlers
  subscribe to via :meth:`EventBus.on`.
- A **handler** is ``(event, ctx) -> None | Awaitable[None]`` — sync or
  async. It *observes* by reading the event, *mutates* by writing the
  event's mutable fields in place (e.g. ``event.args`` for a tool call),
  and *blocks* by calling ``event.deny(reason)`` where supported.
- :class:`EventContext` carries turn-shared context (who, what role) so
  each event only holds its own event-specific payload.

Handlers run in subscription order. For a blockable event
(:class:`ToolCallEvent`), the first handler to ``deny`` short-circuits
the rest — matching pi's "first blocker wins" contract.

This module is dependency-free (no ``marcel_core`` import) so the kernel
can depend on it without an import cycle: the SDK owns the contract, the
kernel owns the implementation that emits at each seam.
"""

from __future__ import annotations

import inspect
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar, TypeVar

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# context + base event
# ---------------------------------------------------------------------------


@dataclass
class EventContext:
    """Turn-shared context handed to every event handler.

    Carries the identity and role of the turn so individual events only
    hold their own event-specific payload. Room to grow (logger, cwd,
    services) without changing the handler signature.
    """

    user_slug: str
    role: str


@dataclass
class Event:
    """Base class for lifecycle events. Subclasses set ``NAME``."""

    NAME: ClassVar[str] = ''


EventT = TypeVar('EventT', bound=Event)


# ---------------------------------------------------------------------------
# lifecycle events
# ---------------------------------------------------------------------------


@dataclass
class SessionStartEvent(Event):
    """Emitted once when a turn begins, before any context is built."""

    NAME: ClassVar[str] = 'session_start'


@dataclass
class InputEvent(Event):
    """Emitted with the user's cleaned input text before the agent runs.

    Observe-only in F0. ``text`` is the slash-prefix-stripped user text.
    """

    NAME: ClassVar[str] = 'input'
    text: str = ''


@dataclass
class BeforeAgentStartEvent(Event):
    """Emitted after the system prompt is built, before the agent runs.

    Handlers may rewrite ``system_prompt`` in place to inject guidance.
    """

    NAME: ClassVar[str] = 'before_agent_start'
    system_prompt: str = ''


@dataclass
class BeforeProviderRequestEvent(Event):
    """Emitted immediately before each provider (model) request.

    Carries the resolved model id and tier label for observation; a hook
    for request-shaping and telemetry.
    """

    NAME: ClassVar[str] = 'before_provider_request'
    model: str = ''
    tier: str = ''


@dataclass
class ToolCallEvent(Event):
    """Emitted before a tool handler runs. Blockable and mutable.

    - **Mutate arguments** by editing ``args`` in place (later handlers
      and the tool itself see the change).
    - **Block execution** by calling :meth:`deny`; the first handler to
      deny short-circuits the rest and the tool does not run.
    """

    NAME: ClassVar[str] = 'tool_call'
    tool_name: str = ''
    args: dict = field(default_factory=dict)
    blocked: bool = False
    block_reason: str | None = None

    def deny(self, reason: str) -> None:
        """Block this tool call with a human-readable reason."""
        self.blocked = True
        self.block_reason = reason


@dataclass
class ToolResultEvent(Event):
    """Emitted after a tool handler runs. Result is rewritable.

    Handlers may overwrite ``result`` and/or ``is_error`` in place to
    rewrite what the model (and channel) sees.
    """

    NAME: ClassVar[str] = 'tool_result'
    tool_name: str = ''
    result: str = ''
    is_error: bool = False


@dataclass
class AgentEndEvent(Event):
    """Emitted after the agent finishes, with the full response text."""

    NAME: ClassVar[str] = 'agent_end'
    response_text: str = ''


@dataclass
class ResourcesDiscoverEvent(Event):
    """Emitted at discovery so extensions can contribute resource paths.

    Handlers append to the path lists in place (skills, prompts) — the
    contribution model pi uses for resources that are files rather than
    imperative registrations.
    """

    NAME: ClassVar[str] = 'resources_discover'
    reason: str = 'startup'
    skill_paths: list[str] = field(default_factory=list)
    prompt_paths: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# the bus
# ---------------------------------------------------------------------------


# The event parameter is typed ``Any`` (not ``Event``) on purpose: a handler
# that annotates its first parameter as a *specific* event type — e.g.
# ``def h(event: ToolCallEvent, ctx): ...`` for autocomplete on ``.args`` /
# ``.deny`` — would otherwise be rejected by contravariance when passed to
# ``on(name, handler)``. ``Any`` accepts any concrete event signature. Stricter
# per-event ``on`` overloads (pi-style) can be added later without breaking this.
EventHandler = Callable[[Any, EventContext], Awaitable[None] | None]
"""A subscriber. Receives the event and turn context; observes by reading,
mutates by writing event fields in place, blocks via ``event.deny(...)``
where supported. May be sync or async."""


class EventBus:
    """Registers handlers per event name and emits events to them in order.

    One bus instance is scoped to a turn (carried on the turn state) so
    handlers can safely hold turn-local state. Registration is cheap and
    order-preserving; emission awaits async handlers and short-circuits a
    blockable event as soon as it is denied.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def on(self, name: str, handler: EventHandler) -> None:
        """Subscribe ``handler`` to the event named ``name``.

        Handlers fire in subscription order. The same handler may be
        registered for multiple events with separate calls.
        """
        self._handlers[name].append(handler)

    def handler_count(self, name: str) -> int:
        """Return how many handlers are subscribed to ``name`` (testing/introspection)."""
        return len(self._handlers.get(name, ()))

    async def emit(self, event: EventT, ctx: EventContext) -> EventT:
        """Dispatch ``event`` to its handlers and return it (possibly mutated).

        Handlers run in order. A handler exception is logged and
        swallowed so one bad subscriber cannot break the turn — except
        that a blockable event already marked ``blocked`` stops dispatch
        (first-blocker-wins). Returns the same event object for the
        caller to read mutated fields / the block decision.
        """
        for handler in list(self._handlers.get(event.NAME, ())):
            try:
                result = handler(event, ctx)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                log.exception(
                    'event handler for %r raised; continuing',
                    event.NAME,
                )
                continue
            if getattr(event, 'blocked', False):
                break
        return event
