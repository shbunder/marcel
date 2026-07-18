"""Injection behavior through the real composition (FEAT-260718-30d45a).

Drives an Agent with the actual ``build_capabilities()`` list and a Hooks
request capture: the Memory capability's bounded ``<memory>`` snapshot is
present for turns and absent on the lean paths (``memory=False``).
"""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities.hooks import Hooks
from pydantic_ai.models.test import TestModel

from marcel_core.capabilities.memory import reset_memory_stores
from marcel_core.composition import build_capabilities
from marcel_core.harness.context import MarcelDeps, TurnState
from marcel_core.storage import _root


@pytest.fixture(autouse=True)
def _rooted(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
    reset_memory_stores()
    yield tmp_path
    reset_memory_stores()


def _deps() -> MarcelDeps:
    return MarcelDeps(
        user_slug='alice',
        conversation_id='alice:cli',
        channel='cli',
        role='user',
        turn=TurnState(event_bus=None),
    )


def _capture():
    seen: list[str] = []
    hooks = Hooks()

    @hooks.on.before_model_request
    async def capture(ctx, request_context):
        seen.append(str(request_context.messages))
        return request_context

    return seen, hooks


@pytest.mark.asyncio
async def test_notebook_snapshot_injected_for_turns(_rooted):
    mem_dir = _rooted / 'users' / 'alice' / 'memory'
    mem_dir.mkdir(parents=True)
    (mem_dir / 'MEMORY.md').write_text('- garden plan: tomatoes in June\n')
    (mem_dir / 'news.md').write_text('---\nname: news\n---\nReads VRT NWS.\n')

    seen, hooks = _capture()
    agent = Agent(
        TestModel(call_tools=[]),
        deps_type=MarcelDeps,
        capabilities=[*build_capabilities(), hooks],
    )
    await agent.run('hi', deps=_deps())

    request = seen[0]
    assert '<memory>' in request
    assert 'tomatoes in June' in request, 'MEMORY.md excerpt is injected'
    assert 'news.md' in request, 'other files are listed by name'
    assert 'Reads VRT NWS.' not in request, 'fragment bodies stay on demand'


@pytest.mark.asyncio
async def test_seeded_user_gets_the_legacy_map_injected(_rooted):
    """A pre-capability user (index.md, no MEMORY.md) sees their seeded map."""
    mem_dir = _rooted / 'users' / 'alice' / 'memory'
    mem_dir.mkdir(parents=True)
    (mem_dir / 'index.md').write_text('- **news** — Reads VRT NWS and De Tijd\n')

    seen, hooks = _capture()
    agent = Agent(
        TestModel(call_tools=[]),
        deps_type=MarcelDeps,
        capabilities=[*build_capabilities(), hooks],
    )
    await agent.run('hi', deps=_deps())

    assert 'Reads VRT NWS and De Tijd' in seen[0], 'the seeded notebook is injected'


@pytest.mark.asyncio
async def test_injection_respects_the_settings_budget(_rooted, monkeypatch):
    """NFR2: the injected snapshot honors marcel_memory_inject_max_tokens."""
    from marcel_core.config import settings

    # Budget covers the guidance plus a small excerpt — not the 15KB notebook.
    monkeypatch.setattr(settings, 'marcel_memory_inject_max_tokens', 400)

    mem_dir = _rooted / 'users' / 'alice' / 'memory'
    mem_dir.mkdir(parents=True)
    (mem_dir / 'MEMORY.md').write_text('- fact line about the garden\n' * 500)

    seen, hooks = _capture()
    agent = Agent(
        TestModel(call_tools=[]),
        deps_type=MarcelDeps,
        capabilities=[*build_capabilities(), hooks],
    )
    await agent.run('hi', deps=_deps())

    request = seen[0]
    start = request.index('<memory>')
    end = request.index('</memory>') if '</memory>' in request else len(request)
    injected = request[start:end]
    # ~4 chars/token heuristic upstream; generous slack for delimiters and
    # the read-more hint — the point is the 15KB notebook did not ship.
    assert len(injected) < 400 * 4 * 3
    assert request.count('fact line about the garden') < 100


@pytest.mark.asyncio
async def test_lean_paths_have_no_memory(_rooted):
    seen, hooks = _capture()
    agent = Agent(
        TestModel(),
        deps_type=MarcelDeps,
        capabilities=[*build_capabilities(memory=False), hooks],
    )
    result = await agent.run('hi', deps=_deps())

    assert '<memory>' not in seen[0]
    called = {
        part.tool_name
        for message in result.all_messages()
        for part in getattr(message, 'parts', [])
        if hasattr(part, 'tool_name')
    }
    assert not {'write_memory', 'read_memory', 'search_memory', 'delete_memory'} & called
