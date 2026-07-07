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


# ---------------------------------------------------------------------------
# ratchet edges (STORY-260707-c332a7)
# ---------------------------------------------------------------------------


def test_claude_binary_resolution():
    from marcel_core.tools.claude_code import _claude_binary

    with patch('marcel_core.tools.claude_code.shutil.which', return_value=None):
        # Nothing installed → bare name, lets the exec raise FileNotFoundError.
        assert _claude_binary() == 'claude'
    with patch(
        'marcel_core.tools.claude_code.shutil.which',
        side_effect=lambda name: '/usr/local/bin/claude' if name == 'claude' else None,
    ):
        assert _claude_binary() == '/usr/local/bin/claude'


@pytest.mark.asyncio
async def test_subprocess_start_failure_returns_error():
    """A non-FileNotFoundError startup failure returns a readable error."""
    with (
        patch('marcel_core.tools.claude_code._claude_binary', return_value='claude'),
        patch('asyncio.create_subprocess_exec', new=AsyncMock(side_effect=RuntimeError('no pty available'))),
    ):
        result = await claude_code(_ctx(), 'any task')

    assert result.startswith('Error starting Claude Code')
    assert 'no pty available' in result


@pytest.mark.asyncio
async def test_noise_lines_and_foreign_events_are_skipped():
    """Blank lines, non-JSON, unknown events, other tool_use, empty text —
    none of it derails the stream."""
    events = _jsonl(
        _init_event(),
        {'type': 'user', 'noise': True},
        {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': ''}]}},
        {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Bash', 'input': {}}]}},
        _result_event('Survived.'),
    )
    data = b'\n' + b'this is not json\n' + events
    proc = _make_proc(data)

    with (
        patch('marcel_core.tools.claude_code._claude_binary', return_value='claude'),
        patch('asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)),
        patch('marcel_core.tools.claude_code.send_notify', new=AsyncMock(return_value='ok')),
    ):
        result = await claude_code(_ctx(), 'noisy task')

    assert result == 'Survived.'


@pytest.mark.asyncio
async def test_question_without_session_or_text_does_not_pause():
    """AskUserQuestion before init (no session) or with an empty question is
    ignored — the task runs to completion."""
    data = _jsonl(
        _ask_user_question('Which file?'),  # before init → no session_id yet
        _init_event(),
        _ask_user_question(''),  # empty question
        _result_event('Done anyway.'),
    )
    proc = _make_proc(data)

    with (
        patch('marcel_core.tools.claude_code._claude_binary', return_value='claude'),
        patch('asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)),
        patch('marcel_core.tools.claude_code.send_notify', new=AsyncMock(return_value='ok')),
    ):
        result = await claude_code(_ctx(), 'questionable task')

    assert result == 'Done anyway.'
    proc.kill.assert_not_called()


@pytest.mark.asyncio
async def test_whitespace_only_text_never_notifies():
    data = _jsonl(_init_event(), _assistant_text('   '), _result_event('ok'))
    proc = _make_proc(data)
    mock_notify = AsyncMock(return_value='ok')

    with (
        patch('marcel_core.tools.claude_code._claude_binary', return_value='claude'),
        patch('asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)),
        patch('marcel_core.tools.claude_code.send_notify', mock_notify),
    ):
        result = await claude_code(_ctx(), 'quiet task')

    assert result == 'ok'
    mock_notify.assert_not_called()


@pytest.mark.asyncio
async def test_timeout_when_process_already_exited_skips_kill():
    """Stream hangs but the process already exited → no kill needed."""

    class _SlowStdout:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(10)
            raise StopAsyncIteration

    proc = MagicMock()
    proc.returncode = 0  # already exited
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    proc.stdout = _SlowStdout()
    proc.stderr = None

    with (
        patch('marcel_core.tools.claude_code._claude_binary', return_value='claude'),
        patch('asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)),
        patch('marcel_core.tools.claude_code.send_notify', new=AsyncMock(return_value='ok')),
    ):
        result = await claude_code(_ctx(), 'slow task', timeout=1)

    assert 'timed out' in result.lower()
    proc.kill.assert_not_called()


@pytest.mark.asyncio
async def test_reap_timeout_in_finally_is_swallowed():
    """The paused path kills the proc; a hanging wait() must not block the
    return past its 5s cap."""
    data = _jsonl(_init_event('sess-hang'), _ask_user_question('Proceed?'))
    proc = _make_proc(data, returncode=0)
    proc.returncode = None  # still running when the question pauses the task
    proc.wait = AsyncMock(side_effect=asyncio.TimeoutError)

    with (
        patch('marcel_core.tools.claude_code._claude_binary', return_value='claude'),
        patch('asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)),
        patch('marcel_core.tools.claude_code.send_notify', new=AsyncMock(return_value='ok')),
    ):
        result = await claude_code(_ctx(), 'pausing task')

    assert result.startswith(PAUSED_PREFIX)
    proc.kill.assert_called()


@pytest.mark.asyncio
async def test_nonzero_exit_without_stderr_stream():
    data = _jsonl(_init_event(), _result_event('r'))
    proc = _make_proc(data, returncode=2)
    proc.stderr = None

    with (
        patch('marcel_core.tools.claude_code._claude_binary', return_value='claude'),
        patch('asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)),
        patch('marcel_core.tools.claude_code.send_notify', new=AsyncMock(return_value='ok')),
    ):
        result = await claude_code(_ctx(), 'task')

    assert result == 'r'


@pytest.mark.asyncio
async def test_nonzero_exit_stderr_read_timeout_is_swallowed():
    data = _jsonl(_init_event(), _result_event('r'))
    proc = _make_proc(data, returncode=3)
    proc.stderr.read = AsyncMock(side_effect=asyncio.TimeoutError)

    with (
        patch('marcel_core.tools.claude_code._claude_binary', return_value='claude'),
        patch('asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)),
        patch('marcel_core.tools.claude_code.send_notify', new=AsyncMock(return_value='ok')),
    ):
        result = await claude_code(_ctx(), 'task')

    assert result == 'r'


@pytest.mark.asyncio
async def test_long_output_is_truncated():
    from marcel_core.tools.claude_code import MAX_OUTPUT_LENGTH

    data = _jsonl(_init_event(), _result_event('y' * (MAX_OUTPUT_LENGTH + 10)))
    proc = _make_proc(data)

    with (
        patch('marcel_core.tools.claude_code._claude_binary', return_value='claude'),
        patch('asyncio.create_subprocess_exec', new=AsyncMock(return_value=proc)),
        patch('marcel_core.tools.claude_code.send_notify', new=AsyncMock(return_value='ok')),
    ):
        result = await claude_code(_ctx(), 'huge task')

    assert 'Output truncated' in result
    assert len(result) < MAX_OUTPUT_LENGTH + 100
