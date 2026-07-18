"""Tests for the policy capability (MarcelPolicy).

Drives a real pydantic-ai Agent with ``TestModel`` (which calls each tool
once) so the interception path is exercised end-to-end: block, mutate
args, rewrite result, pass-through when no bus is wired, and the
first-blocker-wins ordering the three core gates rely on.
"""

from __future__ import annotations

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset

from marcel_core.capabilities.policy import MarcelPolicy
from marcel_core.harness.context import MarcelDeps, TurnState
from marcel_sdk.events import EventBus, ToolCallEvent, ToolResultEvent


def _agent(fn) -> Agent[MarcelDeps, str]:
    fts: FunctionToolset[MarcelDeps] = FunctionToolset()
    fts.add_function(fn)
    return Agent(TestModel(), deps_type=MarcelDeps, toolsets=[fts], capabilities=[MarcelPolicy()])


def _deps(bus: EventBus | None, role: str = 'admin') -> MarcelDeps:
    return MarcelDeps(
        user_slug='alice',
        conversation_id='c',
        channel='cli',
        role=role,
        turn=TurnState(event_bus=bus),
    )


def test_passthrough_when_no_bus():
    calls: list[int] = []

    def tool(ctx: RunContext[MarcelDeps], x: int) -> str:
        calls.append(x)
        return f'ran {x}'

    _agent(tool).run_sync('go', deps=_deps(None))
    assert calls, 'tool should run unchanged when no bus is wired'


def test_tool_call_can_block():
    bus = EventBus()

    def deny(event: ToolCallEvent, ctx):
        if event.tool_name == 'dangerous':
            event.deny('not allowed')

    bus.on('tool_call', deny)
    calls: list[int] = []

    def dangerous(ctx: RunContext[MarcelDeps]) -> str:
        calls.append(1)
        return 'ran'

    result = _agent(dangerous).run_sync('go', deps=_deps(bus))
    assert calls == [], 'blocked tool must never execute'
    assert 'not allowed' in result.output


def test_tool_call_can_mutate_args():
    bus = EventBus()

    def rewrite(event: ToolCallEvent, ctx):
        if 'path' in event.args:
            event.args['path'] = 'SAFE/' + event.args['path']

    bus.on('tool_call', rewrite)
    seen: list[str] = []

    def reader(ctx: RunContext[MarcelDeps], path: str) -> str:
        seen.append(path)
        return path

    _agent(reader).run_sync('go', deps=_deps(bus))
    assert seen and seen[0].startswith('SAFE/'), 'tool must see mutated args'


def test_tool_result_can_rewrite():
    bus = EventBus()

    def upper(event: ToolResultEvent, ctx):
        event.result = event.result.upper()

    bus.on('tool_result', upper)

    def greet(ctx: RunContext[MarcelDeps]) -> str:
        return 'hello'

    result = _agent(greet).run_sync('go', deps=_deps(bus))
    assert 'HELLO' in result.output


def test_no_handlers_is_transparent():
    """A wired-but-empty bus does not alter args or result."""
    bus = EventBus()
    seen: list[str] = []

    def echo(ctx: RunContext[MarcelDeps], msg: str) -> str:
        seen.append(msg)
        return f'echo:{msg}'

    result = _agent(echo).run_sync('go', deps=_deps(bus))
    assert seen, 'tool should run'
    assert 'echo:' in result.output


def test_first_blocker_wins_in_registration_order():
    """Handlers fire in registration order; the first deny short-circuits.

    This is the ordering property the three core gates depend on
    (self-mod guard → role gate → command policy): a later handler must
    never see, override, or append to an already-denied call.
    """
    bus = EventBus()
    fired: list[str] = []

    def first(event: ToolCallEvent, ctx):
        fired.append('first')
        event.deny('first says no')

    def second(event: ToolCallEvent, ctx):  # pragma: no cover - must not run
        fired.append('second')
        event.deny('second says no')

    bus.on('tool_call', first)
    bus.on('tool_call', second)
    calls: list[int] = []

    def guarded(ctx: RunContext[MarcelDeps]) -> str:
        calls.append(1)
        return 'ran'

    result = _agent(guarded).run_sync('go', deps=_deps(bus))
    assert fired == ['first'], 'later handlers must not run after a deny'
    assert calls == [], 'denied tool must never execute'
    assert 'first says no' in result.output
