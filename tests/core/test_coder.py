"""Tests for ISSUE-018: coder agent — restricted file guard, runner, session ID capture."""

import asyncio

import claude_agent_sdk
from claude_agent_sdk import AssistantMessage, StreamEvent, TextBlock
from claude_agent_sdk.types import ToolPermissionContext

from marcel_core.agent.coder import (
    CoderResult,
    _restricted_file_guard,
    run_coder_task,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _agen(*items):
    """Yield items as an async generator — used to mock claude_agent_sdk.query."""
    for item in items:
        yield item


def _stream_event(text: str, *, session_id: str = 'sess-abc') -> StreamEvent:
    return StreamEvent(
        uuid='u',
        session_id=session_id,
        event={'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': text}},
    )


def _assistant_message(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)], model='claude-sonnet-4-6')


_CTX = ToolPermissionContext()


# ---------------------------------------------------------------------------
# _restricted_file_guard
# ---------------------------------------------------------------------------


class TestRestrictedFileGuard:
    def test_allows_normal_write(self):
        result = asyncio.run(
            _restricted_file_guard('Write', {'file_path': '/home/user/projects/marcel/src/foo.py'}, _CTX)
        )
        assert result.behavior == 'allow'

    def test_denies_claude_md_write(self):
        result = asyncio.run(
            _restricted_file_guard('Write', {'file_path': '/home/user/projects/marcel/CLAUDE.md'}, _CTX)
        )
        assert result.behavior == 'deny'

    def test_denies_nested_claude_md(self):
        result = asyncio.run(
            _restricted_file_guard('Edit', {'file_path': '/home/user/projects/marcel/project/CLAUDE.md'}, _CTX)
        )
        assert result.behavior == 'deny'

    def test_denies_auth_path(self):
        result = asyncio.run(
            _restricted_file_guard(
                'Write', {'file_path': '/home/user/projects/marcel/src/marcel_core/auth/login.py'}, _CTX
            )
        )
        assert result.behavior == 'deny'

    def test_allows_read_of_restricted(self):
        result = asyncio.run(
            _restricted_file_guard('Read', {'file_path': '/home/user/projects/marcel/CLAUDE.md'}, _CTX)
        )
        assert result.behavior == 'allow'

    def test_allows_bash(self):
        result = asyncio.run(_restricted_file_guard('Bash', {'command': 'cat CLAUDE.md'}, _CTX))
        assert result.behavior == 'allow'

    def test_allows_write_without_file_path(self):
        result = asyncio.run(_restricted_file_guard('Write', {}, _CTX))
        assert result.behavior == 'allow'


# ---------------------------------------------------------------------------
# run_coder_task
# ---------------------------------------------------------------------------


class TestRunCoderTask:
    def test_returns_streamed_response_and_session_id(self, monkeypatch):
        monkeypatch.setattr(
            claude_agent_sdk,
            'query',
            lambda **_: _agen(
                _stream_event('Hello', session_id='sess-123'),
                _stream_event(' world', session_id='sess-123'),
            ),
        )
        result: CoderResult = asyncio.run(run_coder_task('do something'))
        assert result.response == 'Hello world'
        assert result.session_id == 'sess-123'

    def test_falls_back_to_assistant_message(self, monkeypatch):
        monkeypatch.setattr(
            claude_agent_sdk,
            'query',
            lambda **_: _agen(_assistant_message('Fallback')),
        )
        result = asyncio.run(run_coder_task('do something'))
        assert result.response == 'Fallback'
        assert result.session_id is None

    def test_captures_session_id_from_first_event(self, monkeypatch):
        monkeypatch.setattr(
            claude_agent_sdk,
            'query',
            lambda **_: _agen(
                _stream_event('a', session_id='first'),
                _stream_event('b', session_id='second'),
            ),
        )
        result = asyncio.run(run_coder_task('do something'))
        assert result.session_id == 'first'

    def test_passes_resume_session_id(self, monkeypatch):
        captured_opts = {}

        def mock_query(*, prompt, options=None, **kwargs):
            if options:
                captured_opts['resume'] = options.resume
            return _agen(_assistant_message('ok'))

        monkeypatch.setattr(claude_agent_sdk, 'query', mock_query)
        asyncio.run(run_coder_task('follow up', resume_session_id='prev-sess'))
        assert captured_opts['resume'] == 'prev-sess'

    def test_rejects_concurrent_tasks(self, monkeypatch):
        """Second task should raise RuntimeError while first is running."""

        async def slow_query(**_):
            await asyncio.sleep(10)
            yield _assistant_message('done')  # pragma: no cover

        monkeypatch.setattr(claude_agent_sdk, 'query', slow_query)

        async def run_both():
            task1 = asyncio.create_task(run_coder_task('first'))
            await asyncio.sleep(0.05)  # Let task1 acquire the lock
            try:
                await run_coder_task('second')
                return False  # Should not reach here
            except RuntimeError as exc:
                assert 'already running' in str(exc)
                task1.cancel()
                return True

        assert asyncio.run(run_both())
