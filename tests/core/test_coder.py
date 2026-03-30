"""Tests for ISSUE-018: coder agent runner, session ID capture, concurrency."""

import asyncio

import claude_agent_sdk
from claude_agent_sdk import AssistantMessage, StreamEvent, TextBlock

from marcel_core.agent.coder import (
    CoderResult,
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


# ---------------------------------------------------------------------------
# run_coder_task
# ---------------------------------------------------------------------------


class TestRunCoderTask:
    def test_prefers_streamed_text(self, monkeypatch):
        """When stream events carry text, use that over AssistantMessage."""
        monkeypatch.setattr(
            claude_agent_sdk,
            'query',
            lambda **_: _agen(
                _stream_event('Hello', session_id='sess-123'),
                _stream_event(' world', session_id='sess-123'),
                _assistant_message('Full text'),
            ),
        )
        result: CoderResult = asyncio.run(run_coder_task('do something'))
        assert result.response == 'Hello world'
        assert result.session_id == 'sess-123'

    def test_falls_back_to_last_assistant_message(self, monkeypatch):
        """Multi-turn agent: no streamed text, use last AssistantMessage."""
        monkeypatch.setattr(
            claude_agent_sdk,
            'query',
            lambda **_: _agen(
                _stream_event('', session_id='sess-123'),  # tool-use event (no text)
                _assistant_message('intermediate tool result'),
                _assistant_message('Final summary'),
            ),
        )
        result = asyncio.run(run_coder_task('do something'))
        assert result.response == 'Final summary'
        assert result.session_id == 'sess-123'

    def test_single_assistant_message(self, monkeypatch):
        monkeypatch.setattr(
            claude_agent_sdk,
            'query',
            lambda **_: _agen(_assistant_message('Only response')),
        )
        result = asyncio.run(run_coder_task('do something'))
        assert result.response == 'Only response'
        assert result.session_id is None

    def test_captures_session_id_from_first_event(self, monkeypatch):
        monkeypatch.setattr(
            claude_agent_sdk,
            'query',
            lambda **_: _agen(
                _stream_event('', session_id='first'),
                _stream_event('', session_id='second'),
                _assistant_message('done'),
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
