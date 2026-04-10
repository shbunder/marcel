"""Tests for the claude_code delegation tool."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marcel_core.harness.context import MarcelDeps
from marcel_core.tools.claude_code import PAUSED_PREFIX, claude_code

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deps(channel: str = 'cli') -> MarcelDeps:
    return MarcelDeps(user_slug='test', conversation_id='conv-1', channel=channel)


def _ctx(channel: str = 'cli') -> MagicMock:
    ctx = MagicMock()
    ctx.deps = _deps(channel)
    return ctx


def _jsonl(*events: dict) -> bytes:
    """Encode a sequence of dicts as newline-delimited JSON bytes."""
    return b'\n'.join(json.dumps(e).encode() for e in events) + b'\n'


def _init_event(session_id: str = 'sess-1') -> dict:
    return {'type': 'system', 'subtype': 'init', 'session_id': session_id}


def _assistant_text(text: str) -> dict:
    return {
        'type': 'assistant',
        'message': {'content': [{'type': 'text', 'text': text}]},
    }


def _ask_user_question(question: str) -> dict:
    return {
        'type': 'assistant',
        'message': {
            'content': [
                {
                    'type': 'tool_use',
                    'name': 'AskUserQuestion',
                    'input': {'question': question},
                }
            ]
        },
    }


def _result_event(result: str = 'Done.') -> dict:
    return {'type': 'result', 'subtype': 'success', 'result': result}


# ---------------------------------------------------------------------------
# Fake async readline iterator
# ---------------------------------------------------------------------------


class _FakeStream:
    """Mimics asyncio subprocess stdout — yields lines one at a time."""

    def __init__(self, data: bytes) -> None:
        self._lines = iter(data.splitlines(keepends=True))

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._lines)
        except StopIteration:
            raise StopAsyncIteration


def _make_proc(stdout_data: bytes, returncode: int = 0, session_id: str = 'sess-1'):
    """Build a mock subprocess with the given stdout content."""
    proc = MagicMock()
    proc.stdout = _FakeStream(stdout_data)
    proc.stderr = AsyncMock()
    proc.stderr.read = AsyncMock(return_value=b'')
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    return proc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_completion():
    """Normal task: emits text blocks, returns final result."""
    data = _jsonl(
        _init_event(),
        _assistant_text('Analysing the code...'),
        _assistant_text('Making the changes.'),
        _result_event('All done.'),
    )
    proc = _make_proc(data)

    with (
        patch('marcel_core.tools.claude_code._claude_binary', return_value='claude'),
        patch('asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)),
        patch('marcel_core.tools.claude_code.send_notify', new=AsyncMock(return_value='ok')),
    ):
        result = await claude_code(_ctx(), 'Refactor foo.py')

    assert result == 'All done.'


@pytest.mark.asyncio
async def test_ask_user_question_returns_paused():
    """AskUserQuestion tool use → returns PAUSED: prefix with session_id and question."""
    question = 'Which file should I modify?'
    data = _jsonl(
        _init_event('my-session'),
        _assistant_text('Let me think...'),
        _ask_user_question(question),
        # No result event — process is killed before reaching it
    )
    proc = _make_proc(data)

    with (
        patch('marcel_core.tools.claude_code._claude_binary', return_value='claude'),
        patch('asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)),
        patch('marcel_core.tools.claude_code.send_notify', new=AsyncMock(return_value='ok')),
    ):
        result = await claude_code(_ctx(), 'Do some work')

    assert result.startswith(PAUSED_PREFIX)
    assert 'my-session' in result
    assert question in result
    proc.kill.assert_called()


@pytest.mark.asyncio
async def test_resume_passes_session_flag():
    """resume_session causes --resume flag to appear in subprocess command."""
    data = _jsonl(_init_event('resumed'), _result_event('Resumed and done.'))
    proc = _make_proc(data)
    captured_cmd: list[str] = []

    async def _fake_exec(*args, **kwargs):
        captured_cmd.extend(args)
        return proc

    with (
        patch('marcel_core.tools.claude_code._claude_binary', return_value='claude'),
        patch('asyncio.create_subprocess_exec', new=_fake_exec),
        patch('marcel_core.tools.claude_code.send_notify', new=AsyncMock(return_value='ok')),
    ):
        result = await claude_code(_ctx(), 'the answer', resume_session='sess-abc')

    assert result == 'Resumed and done.'
    assert '--resume' in captured_cmd
    assert 'sess-abc' in captured_cmd


@pytest.mark.asyncio
async def test_timeout_kills_process():
    """Timeout returns a friendly error and kills the subprocess."""

    async def _slow_stream():
        await asyncio.sleep(10)
        yield b''

    proc = MagicMock()
    proc.returncode = None
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=-9)

    class _SlowStdout:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(10)
            raise StopAsyncIteration

    proc.stdout = _SlowStdout()

    with (
        patch('marcel_core.tools.claude_code._claude_binary', return_value='claude'),
        patch('asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)),
        patch('marcel_core.tools.claude_code.send_notify', new=AsyncMock(return_value='ok')),
    ):
        result = await claude_code(_ctx(), 'slow task', timeout=1)

    assert 'timed out' in result.lower()
    proc.kill.assert_called()


@pytest.mark.asyncio
async def test_cli_not_found():
    """FileNotFoundError from subprocess → friendly install message."""
    with (
        patch('marcel_core.tools.claude_code._claude_binary', return_value='claude'),
        patch(
            'asyncio.create_subprocess_exec',
            new=AsyncMock(side_effect=FileNotFoundError),
        ),
    ):
        result = await claude_code(_ctx(), 'any task')

    assert 'not found' in result.lower()
    assert 'npm install' in result


@pytest.mark.asyncio
async def test_nonzero_exit_logs_but_returns_result():
    """Non-zero exit still returns whatever result text was captured."""
    data = _jsonl(
        _init_event(),
        _result_event('Partial result.'),
    )
    proc = _make_proc(data, returncode=1)

    with (
        patch('marcel_core.tools.claude_code._claude_binary', return_value='claude'),
        patch('asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)),
        patch('marcel_core.tools.claude_code.send_notify', new=AsyncMock(return_value='ok')),
    ):
        result = await claude_code(_ctx(), 'failing task')

    assert result == 'Partial result.'


@pytest.mark.asyncio
async def test_empty_output_fallback():
    """No result event and no text → returns fallback string."""
    data = _jsonl(_init_event())  # just init, no result
    proc = _make_proc(data)

    with (
        patch('marcel_core.tools.claude_code._claude_binary', return_value='claude'),
        patch('asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)),
        patch('marcel_core.tools.claude_code.send_notify', new=AsyncMock(return_value='ok')),
    ):
        result = await claude_code(_ctx(), 'empty task')

    assert result == '(no output from Claude Code)'


@pytest.mark.asyncio
async def test_notify_called_for_text_blocks():
    """Text blocks accumulate and trigger notify calls."""
    # 3 chunks each >= _NOTIFY_CHUNK so each should flush separately
    chunk = 'x' * 500
    data = _jsonl(
        _init_event(),
        _assistant_text(chunk),
        _assistant_text(chunk),
        _result_event('done'),
    )
    proc = _make_proc(data)
    mock_notify = AsyncMock(return_value='ok')

    with (
        patch('marcel_core.tools.claude_code._claude_binary', return_value='claude'),
        patch('asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)),
        patch('marcel_core.tools.claude_code.send_notify', mock_notify),
    ):
        await claude_code(_ctx(), 'long task')

    assert mock_notify.call_count >= 2
