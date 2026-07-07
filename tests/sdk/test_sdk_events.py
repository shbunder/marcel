"""Tests for the marcel_sdk lifecycle event bus."""

from __future__ import annotations

import pytest

from marcel_sdk.events import (
    AgentEndEvent,
    BeforeAgentStartEvent,
    BeforeProviderRequestEvent,
    EventBus,
    EventContext,
    InputEvent,
    ResourcesDiscoverEvent,
    SessionStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)

CTX = EventContext(user_slug='alice', role='user')


def test_event_names_are_stable():
    """The subscribed-to NAME of each event is part of the contract."""
    assert SessionStartEvent.NAME == 'session_start'
    assert InputEvent.NAME == 'input'
    assert BeforeAgentStartEvent.NAME == 'before_agent_start'
    assert BeforeProviderRequestEvent.NAME == 'before_provider_request'
    assert ToolCallEvent.NAME == 'tool_call'
    assert ToolResultEvent.NAME == 'tool_result'
    assert AgentEndEvent.NAME == 'agent_end'
    assert ResourcesDiscoverEvent.NAME == 'resources_discover'


def test_on_and_handler_count():
    bus = EventBus()
    assert bus.handler_count('tool_call') == 0
    bus.on('tool_call', lambda e, c: None)
    bus.on('tool_call', lambda e, c: None)
    assert bus.handler_count('tool_call') == 2
    assert bus.handler_count('agent_end') == 0


async def test_emit_runs_handlers_in_order():
    bus = EventBus()
    order: list[int] = []
    bus.on('session_start', lambda e, c: order.append(1))
    bus.on('session_start', lambda e, c: order.append(2))
    bus.on('session_start', lambda e, c: order.append(3))
    await bus.emit(SessionStartEvent(), CTX)
    assert order == [1, 2, 3]


async def test_emit_supports_sync_and_async_handlers():
    bus = EventBus()
    seen: list[str] = []

    def sync_handler(event, ctx):
        seen.append(f'sync:{ctx.user_slug}')

    async def async_handler(event, ctx):
        seen.append(f'async:{event.text}')

    bus.on('input', sync_handler)
    bus.on('input', async_handler)
    await bus.emit(InputEvent(text='hello'), CTX)
    assert seen == ['sync:alice', 'async:hello']


async def test_emit_returns_the_same_event():
    bus = EventBus()
    event = AgentEndEvent(response_text='done')
    returned = await bus.emit(event, CTX)
    assert returned is event


async def test_tool_call_block_short_circuits():
    """First handler to deny stops the rest (first-blocker-wins)."""
    bus = EventBus()
    calls: list[str] = []

    def first(event: ToolCallEvent, ctx):
        calls.append('first')
        event.deny('nope')

    def second(event: ToolCallEvent, ctx):
        calls.append('second')

    bus.on('tool_call', first)
    bus.on('tool_call', second)
    event = await bus.emit(ToolCallEvent(tool_name='bash', args={}), CTX)
    assert event.blocked is True
    assert event.block_reason == 'nope'
    assert calls == ['first']  # second never ran


async def test_tool_call_arg_mutation_propagates():
    """A handler mutating args in place is visible to later handlers."""
    bus = EventBus()

    def rewriter(event: ToolCallEvent, ctx):
        event.args['command'] = 'safe ' + event.args['command']

    seen: dict = {}

    def observer(event: ToolCallEvent, ctx):
        seen.update(event.args)

    bus.on('tool_call', rewriter)
    bus.on('tool_call', observer)
    event = await bus.emit(ToolCallEvent(tool_name='bash', args={'command': 'ls'}), CTX)
    assert event.args['command'] == 'safe ls'
    assert seen['command'] == 'safe ls'


async def test_tool_result_rewrite():
    bus = EventBus()

    def redact(event: ToolResultEvent, ctx):
        event.result = event.result.replace('secret', '***')
        event.is_error = True

    bus.on('tool_result', redact)
    event = await bus.emit(
        ToolResultEvent(tool_name='read_file', result='the secret is 42'),
        CTX,
    )
    assert event.result == 'the *** is 42'
    assert event.is_error is True


async def test_before_agent_start_system_prompt_mutation():
    bus = EventBus()
    bus.on('before_agent_start', lambda e, c: setattr(e, 'system_prompt', e.system_prompt + '\nEXTRA'))
    event = await bus.emit(BeforeAgentStartEvent(system_prompt='BASE'), CTX)
    assert event.system_prompt == 'BASE\nEXTRA'


async def test_resources_discover_contribution():
    bus = EventBus()

    def contribute(event: ResourcesDiscoverEvent, ctx):
        event.skill_paths.append('/zoo/skills/demo/SKILL.md')
        event.prompt_paths.append('/zoo/prompts/demo.md')

    bus.on('resources_discover', contribute)
    event = await bus.emit(ResourcesDiscoverEvent(reason='startup'), CTX)
    assert event.skill_paths == ['/zoo/skills/demo/SKILL.md']
    assert event.prompt_paths == ['/zoo/prompts/demo.md']


async def test_handler_exception_is_isolated():
    """A raising handler is logged and skipped; later handlers still run."""
    bus = EventBus()
    ran: list[str] = []

    def boom(event, ctx):
        ran.append('boom')
        raise RuntimeError('handler bug')

    def after(event, ctx):
        ran.append('after')

    bus.on('agent_end', boom)
    bus.on('agent_end', after)
    # emit must not raise
    await bus.emit(AgentEndEvent(response_text='x'), CTX)
    assert ran == ['boom', 'after']


async def test_emit_with_no_handlers_is_noop():
    bus = EventBus()
    event = await bus.emit(ToolCallEvent(tool_name='web', args={}), CTX)
    assert event.blocked is False


async def test_async_handler_exception_isolated():
    bus = EventBus()
    ran: list[str] = []

    async def boom(event, ctx):
        ran.append('boom')
        raise ValueError('async bug')

    async def after(event, ctx):
        ran.append('after')

    bus.on('input', boom)
    bus.on('input', after)
    await bus.emit(InputEvent(text='hi'), CTX)
    assert ran == ['boom', 'after']


def test_deny_sets_fields():
    event = ToolCallEvent(tool_name='bash', args={})
    assert event.blocked is False
    event.deny('too dangerous')
    assert event.blocked is True
    assert event.block_reason == 'too dangerous'


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
