"""Messaging-tuned context decay (FEAT-260721-a59c21, S1 tail + S2 age).

The operator's model for continuous channels: the last messages visible in
the chat view stay verbatim through every automatic seal; older content
folds gradually into the rolling summary; a week-stale conversation is
gist + tail. Manual /forget and session-end still fold everything.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from marcel_core.config import settings
from marcel_core.storage import _root
from marcel_core.storage.conversation import (
    _split_tail,
    append_to_segment,
    ensure_channel,
    load_channel_meta,
    load_latest_summary,
    read_active_segment,
)
from marcel_core.storage.history import HistoryMessage, MessageRole


@pytest.fixture(autouse=True)
def _data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
    from marcel_core.memory import summarizer

    summarizer._summarization_state.clear()
    return tmp_path


def _msg(text: str, role: MessageRole = 'user', age_days: float = 0) -> HistoryMessage:
    return HistoryMessage(
        role=role,
        text=text,
        timestamp=datetime.now(UTC) - timedelta(days=age_days),
        conversation_id='shaun:telegram',
    )


def _seed(n: int, channel: str = 'telegram', age_days: float = 0) -> None:
    ensure_channel('shaun', channel)
    for i in range(n):
        append_to_segment('shaun', channel, _msg(f'message {i}', age_days=age_days))


_SUMMARY = AsyncMock(return_value='Rolling summary of the folded part.')


class TestIdleSealKeepsTail:
    @pytest.mark.asyncio
    async def test_tail_stays_verbatim_after_idle_seal(self, monkeypatch):
        from marcel_core.memory.summarizer import summarize_active_segment

        monkeypatch.setattr(settings, 'marcel_context_tail_messages', 5)
        _seed(20)
        with patch('marcel_core.memory.summarizer._generate_summary', _SUMMARY):
            assert await summarize_active_segment('shaun', 'telegram', trigger='idle') is True

        tail = read_active_segment('shaun', 'telegram')
        assert len(tail) >= 5
        assert [m.text for m in tail][-1] == 'message 19'  # the last message is always in context
        summary = load_latest_summary('shaun', 'telegram')
        assert summary is not None  # the folded part became the summary

    @pytest.mark.asyncio
    async def test_short_conversation_never_collapses_to_gist(self, monkeypatch):
        from marcel_core.memory.summarizer import summarize_active_segment

        monkeypatch.setattr(settings, 'marcel_context_tail_messages', 15)
        _seed(4)
        with patch('marcel_core.memory.summarizer._generate_summary', _SUMMARY):
            assert await summarize_active_segment('shaun', 'telegram', trigger='idle') is False

        assert len(read_active_segment('shaun', 'telegram')) == 4  # untouched
        assert load_latest_summary('shaun', 'telegram') is None

    @pytest.mark.asyncio
    async def test_manual_forget_still_folds_everything(self, monkeypatch):
        from marcel_core.memory.summarizer import summarize_active_segment

        monkeypatch.setattr(settings, 'marcel_context_tail_messages', 5)
        _seed(20)
        with patch('marcel_core.memory.summarizer._generate_summary', _SUMMARY):
            assert await summarize_active_segment('shaun', 'telegram', trigger='manual') is True

        assert read_active_segment('shaun', 'telegram') == []  # clean slate
        assert load_latest_summary('shaun', 'telegram') is not None

    @pytest.mark.asyncio
    async def test_session_end_still_folds_everything(self, monkeypatch):
        from marcel_core.memory.summarizer import summarize_active_segment

        monkeypatch.setattr(settings, 'marcel_context_tail_messages', 5)
        _seed(20, channel='cli')
        with patch('marcel_core.memory.summarizer._generate_summary', _SUMMARY):
            assert await summarize_active_segment('shaun', 'cli', trigger='session_end') is True
        assert read_active_segment('shaun', 'cli') == []


class TestRotationKeepsTail:
    def test_budget_rotation_keeps_tail_verbatim(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_context_budget_tokens', 40)  # 160 bytes
        monkeypatch.setattr(settings, 'marcel_context_tail_messages', 3)
        _seed(8)
        meta = load_channel_meta('shaun', 'telegram')
        assert meta is not None
        assert meta.pending_summary_segments, 'budget rotation must queue the folded part'
        tail = read_active_segment('shaun', 'telegram')
        assert tail, 'the visible tail must survive a budget rotation'
        assert tail[-1].text == 'message 7'


class TestTailBoundary:
    def test_tail_snaps_back_to_user_message(self):
        import json

        lines = [
            json.dumps({'role': 'user', 'text': 'q1'}),
            json.dumps({'role': 'assistant', 'text': 'a1'}),
            json.dumps({'role': 'user', 'text': 'q2'}),
            json.dumps({'role': 'assistant', 'text': None, 'tool_calls': [{'name': 't'}]}),
            json.dumps({'role': 'tool', 'text': 'result'}),
            json.dumps({'role': 'assistant', 'text': 'a2'}),
        ]
        folded, tail = _split_tail(lines, 2)
        # Naive split would start the tail at the tool result; it must snap
        # back to the user message so the call/return pair stays together.
        assert json.loads(tail[0])['role'] == 'user'
        assert len(folded) + len(tail) == len(lines)

    def test_under_tail_returns_nothing_to_fold(self):
        import json

        lines = [json.dumps({'role': 'user', 'text': 'hi'})]
        folded, tail = _split_tail(lines, 15)
        assert folded == [] and tail == lines

    def test_zero_tail_folds_everything(self):
        import json

        lines = [json.dumps({'role': 'user', 'text': 'hi'})] * 3
        folded, tail = _split_tail(lines, 0)
        assert len(folded) == 3 and tail == []


class TestAgeFolding:
    @pytest.mark.asyncio
    async def test_week_stale_content_folds_keeping_tail(self, monkeypatch):
        from marcel_core.memory.summarizer import summarize_if_stale

        monkeypatch.setattr(settings, 'marcel_context_tail_messages', 5)
        _seed(20, age_days=8)  # oldest message is 8 days old
        with patch('marcel_core.memory.summarizer._generate_summary', _SUMMARY):
            assert await summarize_if_stale('shaun', 'telegram', max_age_days=7) is True

        tail = read_active_segment('shaun', 'telegram')
        assert len(tail) >= 5 and tail[-1].text == 'message 19'
        assert load_latest_summary('shaun', 'telegram') is not None

    @pytest.mark.asyncio
    async def test_fresh_content_not_age_folded(self, monkeypatch):
        from marcel_core.memory.summarizer import summarize_if_stale

        _seed(20, age_days=2)
        with patch('marcel_core.memory.summarizer._generate_summary', _SUMMARY):
            assert await summarize_if_stale('shaun', 'telegram', max_age_days=7) is False
        assert len(read_active_segment('shaun', 'telegram')) == 20

    @pytest.mark.asyncio
    async def test_load_context_age_folds_end_to_end(self, monkeypatch):
        from marcel_core.capabilities.persistence.store import MarcelStepStore

        monkeypatch.setattr(settings, 'marcel_context_tail_messages', 5)
        monkeypatch.setattr(settings, 'marcel_context_max_age_days', 7)
        monkeypatch.setattr(settings, 'marcel_idle_summarize_minutes', 999999)
        _seed(20, age_days=10)
        with patch('marcel_core.memory.summarizer._generate_summary', _SUMMARY):
            context = await MarcelStepStore().load_context('shaun', 'telegram')

        rendered = str(context)
        assert 'Previous conversation summary' in rendered  # gist of the old part
        assert 'message 19' in rendered  # the visible tail, verbatim
        assert 'message 0' not in rendered  # week-stale content left verbatim context
