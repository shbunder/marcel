"""Summarize-on-rotate + token-budget (FEAT-260707-89a886, STORY-12dfc2 + 31dae4).

The bug these close: a size-triggered rotation created a fresh segment and
left the outgoing one unsummarized, so ``load_context`` — which loads only the
active segment + latest summary — silently dropped it. Now rotation queues the
outgoing segment and ``summarize_pending_segments`` folds it into the rolling
summary before the next turn's context is built.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from marcel_core.storage import _root
from marcel_core.storage.conversation import (
    append_to_segment,
    ensure_channel,
    load_channel_meta,
    load_latest_summary,
    read_active_segment,
)
from marcel_core.storage.history import HistoryMessage


@pytest.fixture(autouse=True)
def _data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
    from marcel_core.memory import summarizer

    summarizer._summarization_state.clear()
    return tmp_path


def _msg(text: str) -> HistoryMessage:
    return HistoryMessage(role='user', text=text, timestamp=datetime.now(UTC), conversation_id='shaun:cli')


class TestRotationQueuesForSummary:
    def test_rotation_marks_outgoing_pending(self, monkeypatch):
        """A byte-budget rotation queues the outgoing segment (was dropped)."""
        from marcel_core.config import settings

        # Tiny budget so a couple of messages force a rotation.
        monkeypatch.setattr(settings, 'marcel_context_budget_tokens', 20)  # 80 bytes
        ensure_channel('shaun', 'cli')
        for i in range(6):
            append_to_segment('shaun', 'cli', _msg(f'message number {i} with enough text to exceed the budget'))

        meta = load_channel_meta('shaun', 'cli')
        assert meta is not None
        assert meta.pending_summary_segments, 'outgoing segment was not queued for summary'
        assert meta.active_segment not in meta.pending_summary_segments

    def test_pending_survives_meta_round_trip(self, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'marcel_context_budget_tokens', 20)
        ensure_channel('shaun', 'cli')
        for i in range(6):
            append_to_segment('shaun', 'cli', _msg(f'msg {i} padded out to force a rotation here'))
        pending = load_channel_meta('shaun', 'cli').pending_summary_segments  # type: ignore[union-attr]
        # Re-read from disk — the field must serialize.
        assert load_channel_meta('shaun', 'cli').pending_summary_segments == pending  # type: ignore[union-attr]


class TestReconcileFoldsIntoContext:
    @pytest.mark.asyncio
    async def test_pending_segment_becomes_a_summary(self, monkeypatch):
        """summarize_pending_segments folds a queued segment into the rolling
        summary (scripted Haiku) — the content that used to vanish is now
        recoverable from context."""
        from marcel_core.config import settings
        from marcel_core.memory.summarizer import summarize_pending_segments

        monkeypatch.setattr(settings, 'marcel_context_budget_tokens', 20)
        ensure_channel('shaun', 'cli')
        for i in range(6):
            append_to_segment('shaun', 'cli', _msg(f'dinner plan detail {i} that must not be lost from context'))

        assert load_latest_summary('shaun', 'cli') is None  # nothing summarized yet

        with patch(
            'marcel_core.memory.summarizer._generate_summary',
            AsyncMock(return_value='The family discussed dinner plans across several messages.'),
        ):
            folded = await summarize_pending_segments('shaun', 'cli')

        assert folded >= 1
        summary = load_latest_summary('shaun', 'cli')
        assert summary is not None
        assert 'dinner plans' in summary.summary
        # Queue drained.
        assert load_channel_meta('shaun', 'cli').pending_summary_segments == []  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_load_context_reconciles_before_building(self, monkeypatch):
        """The store's load_context folds pending segments first, so the
        rotated content is present (as summary) in the built context."""
        from marcel_core.capabilities.persistence.store import MarcelStepStore
        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'marcel_context_budget_tokens', 20)
        monkeypatch.setattr(settings, 'marcel_idle_summarize_minutes', 9999)  # no idle interference
        ensure_channel('shaun', 'cli')
        for i in range(6):
            append_to_segment('shaun', 'cli', _msg(f'grocery item {i} the model should still know about'))

        with patch(
            'marcel_core.memory.summarizer._generate_summary',
            AsyncMock(return_value='Rolling summary: the user listed several grocery items.'),
        ):
            context = await MarcelStepStore().load_context('shaun', 'cli')

        rendered = str(context)
        assert 'Previous conversation summary' in rendered
        assert 'grocery items' in rendered
        assert load_channel_meta('shaun', 'cli').pending_summary_segments == []  # type: ignore[union-attr]


class TestContinuousUnchanged:
    def test_small_conversation_never_rotates(self):
        """A conversation under budget rotates nothing and queues nothing —
        continuous behaviour is unchanged for normal use."""
        ensure_channel('shaun', 'telegram')
        for i in range(20):
            append_to_segment('shaun', 'telegram', _msg(f'short {i}'))
        meta = load_channel_meta('shaun', 'telegram')
        assert meta is not None
        assert meta.pending_summary_segments == []
        assert len(read_active_segment('shaun', 'telegram')) == 20
