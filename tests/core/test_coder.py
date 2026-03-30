"""Tests for ISSUE-018: coder agent runner, session ID capture, concurrency."""

import asyncio

import claude_agent_sdk
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

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


def _result_message(
    text: str = 'Done',
    *,
    session_id: str = 'sess-123',
    cost: float = 0.05,
    turns: int = 3,
    is_error: bool = False,
) -> ResultMessage:
    return ResultMessage(
        subtype='result',
        duration_ms=1000,
        duration_api_ms=800,
        is_error=is_error,
        num_turns=turns,
        session_id=session_id,
        result=text,
        total_cost_usd=cost,
    )


def _assistant_message(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)], model='claude-sonnet-4-6')


# ---------------------------------------------------------------------------
# run_coder_task
# ---------------------------------------------------------------------------


class TestRunCoderTask:
    def test_returns_result_message_text(self, monkeypatch):
        """ResultMessage.result is the authoritative output."""
        monkeypatch.setattr(
            claude_agent_sdk,
            'query',
            lambda **_: _agen(
                _assistant_message('intermediate'),
                _result_message('Final result', session_id='sess-1', cost=0.10, turns=5),
            ),
        )
        result: CoderResult = asyncio.run(run_coder_task('do something'))
        assert result.response == 'Final result'
        assert result.session_id == 'sess-1'
        assert result.cost_usd == 0.10
        assert result.num_turns == 5
        assert result.is_error is False

    def test_falls_back_to_assistant_message(self, monkeypatch):
        """If no ResultMessage, use last AssistantMessage text."""
        monkeypatch.setattr(
            claude_agent_sdk,
            'query',
            lambda **_: _agen(
                _assistant_message('first'),
                _assistant_message('last response'),
            ),
        )
        result = asyncio.run(run_coder_task('do something'))
        assert result.response == 'last response'

    def test_result_message_error(self, monkeypatch):
        monkeypatch.setattr(
            claude_agent_sdk,
            'query',
            lambda **_: _agen(_result_message('error text', is_error=True)),
        )
        result = asyncio.run(run_coder_task('do something'))
        assert result.is_error is True
        assert result.response == 'error text'

    def test_passes_resume_session_id(self, monkeypatch):
        captured_opts = {}

        def mock_query(*, prompt, options=None, **kwargs):
            if options:
                captured_opts['resume'] = options.resume
            return _agen(_result_message('ok'))

        monkeypatch.setattr(claude_agent_sdk, 'query', mock_query)
        asyncio.run(run_coder_task('follow up', resume_session_id='prev-sess'))
        assert captured_opts['resume'] == 'prev-sess'

    def test_rejects_concurrent_tasks(self, monkeypatch):
        """Second task should raise RuntimeError while first is running."""

        async def slow_query(**_):
            await asyncio.sleep(10)
            yield _result_message('done')  # pragma: no cover

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
