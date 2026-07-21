"""DeduplicateFileReads in the admin stack (FEAT-260721-51f9e3, STORY-1d3005).

Superseded identical-window ``read_file`` results are blanked in place —
content-aware, zero LLM cost, count-preserving (safe for the persistence
delta guard). Keyed on (path, offset, limit): a *different* window never
supersedes, because it may hold lines the newest read does not.
"""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities.hooks import Hooks
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness.compaction import DeduplicateFileReads

from marcel_core.composition import _read_file_key, build_capabilities
from marcel_core.harness.context import MarcelDeps, TurnState
from marcel_core.storage import _root


def _read_pair(i: int, path: str, content: str, offset: int = 0) -> list[ModelMessage]:
    args: dict = {'path': path}
    if offset:
        args['offset'] = offset
    return [
        ModelResponse(parts=[ToolCallPart(tool_name='read_file', args=args, tool_call_id=f'rd-{i}')]),
        ModelRequest(parts=[ToolReturnPart(tool_name='read_file', content=content, tool_call_id=f'rd-{i}')]),
    ]


def _deps() -> MarcelDeps:
    return MarcelDeps(
        user_slug='alice',
        conversation_id='alice:cli',
        channel='cli',
        role='admin',
        turn=TurnState(event_bus=None),
    )


class TestFileKey:
    def test_non_read_tools_ignored(self):
        call = ToolCallPart(tool_name='bash', args={'path': 'x'}, tool_call_id='t1')
        assert _read_file_key(call) is None

    def test_read_without_path_ignored(self):
        call = ToolCallPart(tool_name='read_file', args={}, tool_call_id='t1')
        assert _read_file_key(call) is None

    def test_identical_windows_share_a_key(self):
        a = ToolCallPart(tool_name='read_file', args={'path': 'a.py'}, tool_call_id='t1')
        b = ToolCallPart(tool_name='read_file', args={'path': 'a.py'}, tool_call_id='t2')
        assert _read_file_key(a) == _read_file_key(b)

    def test_different_windows_never_supersede(self):
        full = ToolCallPart(tool_name='read_file', args={'path': 'a.py'}, tool_call_id='t1')
        windowed = ToolCallPart(tool_name='read_file', args={'path': 'a.py', 'offset': 200}, tool_call_id='t2')
        assert _read_file_key(full) != _read_file_key(windowed)


class TestComposition:
    def test_admin_stack_includes_dedup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        caps = build_capabilities(role='admin', user_slug='alice')
        assert any(isinstance(c, DeduplicateFileReads) for c in caps)

    def test_user_stack_has_no_dedup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        caps = build_capabilities(role='user', user_slug='alice')
        assert not any(isinstance(c, DeduplicateFileReads) for c in caps)


class TestBlanking:
    @pytest.mark.asyncio
    async def test_superseded_read_blanked_count_preserved(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        history: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content='start')])]
        history += _read_pair(0, 'src/app.py', 'OLD CONTENT v1')
        history += _read_pair(1, 'src/other.py', 'other file body')
        history += _read_pair(2, 'src/app.py', 'NEW CONTENT v2')
        history += _read_pair(3, 'src/app.py', 'WINDOWED LINES', offset=200)

        seen: list[list[ModelMessage]] = []
        hooks = Hooks()

        @hooks.on.before_model_request
        async def capture(ctx, request_context):
            seen.append(list(request_context.messages))
            return request_context

        agent = Agent(
            TestModel(call_tools=[]),
            deps_type=MarcelDeps,
            capabilities=[*build_capabilities(role='admin', user_slug='alice'), hooks],
        )
        await agent.run('and now?', deps=_deps(), message_history=history)

        assert seen
        request = seen[0]
        returns = {
            p.tool_call_id: p.content
            for msg in request
            if isinstance(msg, ModelRequest)
            for p in msg.parts
            if isinstance(p, ToolReturnPart)
        }
        assert returns['rd-0'] != 'OLD CONTENT v1', 'superseded identical-window read must be blanked'
        assert returns['rd-2'] == 'NEW CONTENT v2', 'latest read keeps its content'
        assert returns['rd-1'] == 'other file body', 'other files untouched'
        assert returns['rd-3'] == 'WINDOWED LINES', 'a different window is never superseded'
        # Count preserved — the persistence delta guard depends on this.
        assert len(request) == len(history) + 1  # +1 for the new user prompt
