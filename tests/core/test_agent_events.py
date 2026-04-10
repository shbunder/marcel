"""Tests for agent/events.py — AG-UI event dataclasses and helpers."""

from __future__ import annotations

from marcel_core.agent.events import (
    RunError,
    RunFinished,
    RunStarted,
    TextMessageContent,
    TextMessageEnd,
    TextMessageStart,
    ToolCallEnd,
    ToolCallResult,
    ToolCallStart,
    _truncate,
)


class TestRunStarted:
    def test_type_field(self):
        e = RunStarted()
        assert e.type == 'run_started'

    def test_to_dict_no_thread_id(self):
        e = RunStarted()
        d = e.to_dict()
        assert d == {'type': 'run_started'}
        assert 'thread_id' not in d

    def test_to_dict_with_thread_id(self):
        e = RunStarted(thread_id='t-123')
        d = e.to_dict()
        assert d['type'] == 'run_started'
        assert d['thread_id'] == 't-123'


class TestRunFinished:
    def test_to_dict_minimal(self):
        e = RunFinished()
        d = e.to_dict()
        assert d == {'type': 'run_finished'}

    def test_to_dict_with_cost(self):
        e = RunFinished(total_cost_usd=0.042)
        d = e.to_dict()
        assert d['cost_usd'] == 0.042
        assert 'turns' not in d

    def test_to_dict_with_turns(self):
        e = RunFinished(num_turns=3)
        d = e.to_dict()
        assert d['turns'] == 3
        assert 'cost_usd' not in d

    def test_to_dict_with_cost_and_turns(self):
        e = RunFinished(total_cost_usd=0.01, num_turns=5)
        d = e.to_dict()
        assert d['cost_usd'] == 0.01
        assert d['turns'] == 5


class TestRunError:
    def test_to_dict(self):
        e = RunError(message='something broke')
        d = e.to_dict()
        assert d == {'type': 'run_error', 'message': 'something broke'}

    def test_type_field(self):
        assert RunError().type == 'run_error'


class TestTextMessageStart:
    def test_to_dict_no_message_id(self):
        e = TextMessageStart()
        d = e.to_dict()
        assert d == {'type': 'text_message_start'}

    def test_to_dict_with_message_id(self):
        e = TextMessageStart(message_id='msg-1')
        d = e.to_dict()
        assert d['message_id'] == 'msg-1'


class TestTextMessageContent:
    def test_to_dict(self):
        e = TextMessageContent(text='hello world')
        d = e.to_dict()
        assert d == {'type': 'text_message_content', 'text': 'hello world'}


class TestTextMessageEnd:
    def test_to_dict(self):
        e = TextMessageEnd()
        assert e.to_dict() == {'type': 'text_message_end'}


class TestToolCallStart:
    def test_to_dict(self):
        e = ToolCallStart(tool_call_id='tc-1', tool_name='bash')
        d = e.to_dict()
        assert d == {'type': 'tool_call_start', 'tool_call_id': 'tc-1', 'tool_name': 'bash'}


class TestToolCallEnd:
    def test_to_dict(self):
        e = ToolCallEnd(tool_call_id='tc-1')
        assert e.to_dict() == {'type': 'tool_call_end', 'tool_call_id': 'tc-1'}


class TestToolCallResult:
    def test_to_dict_success(self):
        e = ToolCallResult(tool_call_id='tc-1', summary='done')
        d = e.to_dict()
        assert d['type'] == 'tool_call_result'
        assert d['tool_call_id'] == 'tc-1'
        assert d['is_error'] is False
        assert d['summary'] == 'done'

    def test_to_dict_error(self):
        e = ToolCallResult(tool_call_id='tc-2', is_error=True, summary='failed')
        d = e.to_dict()
        assert d['is_error'] is True


class TestTruncate:
    def test_short_string_unchanged(self):
        assert _truncate('hello', max_len=200) == 'hello'

    def test_exactly_max_len_unchanged(self):
        s = 'x' * 200
        assert _truncate(s, max_len=200) == s

    def test_long_string_truncated(self):
        s = 'a' * 300
        result = _truncate(s, max_len=200)
        assert result.endswith('...')
        assert len(result) == 203  # 200 + len('...')

    def test_custom_max_len(self):
        result = _truncate('abcdefgh', max_len=5)
        assert result == 'abcde...'
