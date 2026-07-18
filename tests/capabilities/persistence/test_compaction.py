"""Compaction stack behavior through the real composition (STORY-260718-406de0).

Drives an Agent built with the actual ``build_capabilities()`` list and a
seeded over-threshold history, capturing the request the model receives via
a Hooks capability — the pre-harness age-tier trimming is gone, so this is
the proof the in-run stack does the shaping now.
"""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities.hooks import Hooks
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel

from marcel_core.composition import build_capabilities
from marcel_core.config import settings
from marcel_core.harness.context import MarcelDeps, TurnState
from marcel_core.storage import _root

BIG = 'x' * 40_000  # ~10k estimated tokens per result


def _pair(i: int, tool: str = 'bash', content: str = BIG) -> list[ModelMessage]:
    return [
        ModelResponse(parts=[ToolCallPart(tool_name=tool, args={}, tool_call_id=f'tc-{i}')]),
        ModelRequest(parts=[ToolReturnPart(tool_name=tool, content=content, tool_call_id=f'tc-{i}')]),
    ]


def _deps() -> MarcelDeps:
    return MarcelDeps(
        user_slug='alice',
        conversation_id='alice:cli',
        channel='cli',
        role='admin',
        turn=TurnState(event_bus=None),
    )


@pytest.mark.asyncio
async def test_old_results_blanked_pairs_and_marcel_survive(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

    history: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content='start')])]
    history += _pair(0, tool='marcel', content='skill docs ' + BIG)
    for i in range(1, 7):
        history += _pair(i)
    history.append(ModelResponse(parts=[TextPart(content='earlier reply')]))

    seen: list[list[ModelMessage]] = []

    hooks = Hooks()

    @hooks.on.before_model_request
    async def capture(ctx, request_context):
        seen.append(list(request_context.messages))
        return request_context

    agent = Agent(
        TestModel(call_tools=[]),
        deps_type=MarcelDeps,
        capabilities=[*build_capabilities(), hooks],
    )
    await agent.run('and now?', deps=_deps(), message_history=history)

    assert seen, 'hook must observe the model request'
    request = seen[0]
    returns = [
        part
        for msg in request
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, ToolReturnPart)
    ]
    bash_returns = [p for p in returns if p.tool_name == 'bash']
    blanked = [p for p in bash_returns if p.content == '[tool result cleared]']
    intact = [p for p in bash_returns if p.content == BIG]
    assert blanked, 'aged results past the token trigger must be blanked'
    assert len(intact) == settings.marcel_compaction_keep_pairs, 'the most recent pairs stay intact'
    # Every tool call still has its (possibly blanked) paired return.
    call_ids = {
        p.tool_call_id
        for msg in request
        if isinstance(msg, ModelResponse)
        for p in msg.parts
        if isinstance(p, ToolCallPart)
    }
    assert call_ids == {p.tool_call_id for p in returns}
    # `marcel` results are exempt from clearing (survive past the token trigger).
    marcel_returns = [p for p in returns if p.tool_name == 'marcel']
    assert marcel_returns and all(
        isinstance(p.content, str) and p.content.startswith('skill docs') for p in marcel_returns
    )


@pytest.mark.asyncio
async def test_under_threshold_history_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

    history: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content='start')])]
    history += _pair(0, content='small result')
    history.append(ModelResponse(parts=[TextPart(content='earlier reply')]))

    seen: list[list[ModelMessage]] = []
    hooks = Hooks()

    @hooks.on.before_model_request
    async def capture(ctx, request_context):
        seen.append(list(request_context.messages))
        return request_context

    agent = Agent(
        TestModel(call_tools=[]),
        deps_type=MarcelDeps,
        capabilities=[*build_capabilities(), hooks],
    )
    await agent.run('and now?', deps=_deps(), message_history=history)

    returns = [
        part
        for msg in seen[0]
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert [p.content for p in returns] == ['small result'], 'below the trigger nothing is blanked'


@pytest.mark.asyncio
async def test_loaded_skill_survives_compaction(tmp_path, monkeypatch):
    """NFR2 (FEAT-260718-85b545): a loaded skill's body survives a compacted
    long conversation.

    A skill is a deferred capability; ``load_capability`` marks it loaded via a
    call/return pair in history. Compaction may blank the return *content* (like
    any aged tool result), but the pair persists — so pydantic-ai's
    ``parse_loaded_capabilities`` still reports the skill loaded, and the
    framework re-injects its instructions from the ``Capability`` on the next
    request. This asserts the loaded state survives the over-threshold trim.
    """
    from pydantic_ai._deferred_capabilities import parse_loaded_capabilities
    from pydantic_ai.messages import LoadCapabilityCallPart, LoadCapabilityReturnPart

    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

    history: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content='start')])]
    # The skill was loaded early in the conversation…
    history += [
        ModelResponse(parts=[LoadCapabilityCallPart(args={'id': 'news'}, tool_call_id='lc-1')]),
        ModelRequest(
            parts=[LoadCapabilityReturnPart(content={'instructions': 'NEWS BODY ' + BIG}, tool_call_id='lc-1')]
        ),
    ]
    # …then many big tool results push the run well past the clearing trigger.
    for i in range(1, 7):
        history += _pair(i)
    history.append(ModelResponse(parts=[TextPart(content='earlier reply')]))

    seen: list[list[ModelMessage]] = []
    hooks = Hooks()

    @hooks.on.before_model_request
    async def capture(ctx, request_context):
        seen.append(list(request_context.messages))
        return request_context

    agent = Agent(
        TestModel(call_tools=[]),
        deps_type=MarcelDeps,
        capabilities=[*build_capabilities(), hooks],
    )
    await agent.run('and now?', deps=_deps(), message_history=history)

    assert seen, 'hook must observe the model request'
    request = seen[0]
    # The skill is still reported loaded after compaction — its body re-injects.
    assert 'news' in parse_loaded_capabilities(request)
    # The load_capability pair persists (its return may be blanked, but the part stays).
    lc_returns = [
        p
        for msg in request
        if isinstance(msg, ModelRequest)
        for p in msg.parts
        if isinstance(p, LoadCapabilityReturnPart)
    ]
    assert len(lc_returns) == 1
