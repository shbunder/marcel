"""Tests for segment-based continuous conversation storage."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from marcel_core.memory import conversation as conversation_module
from marcel_core.memory.conversation import (
    ChannelMeta,
    SegmentSummary,
    append_to_segment,
    ensure_channel,
    extract_keywords,
    has_active_content,
    is_idle,
    list_channels,
    list_summaries,
    load_channel_meta,
    load_latest_summary,
    load_summary,
    read_active_segment,
    read_segment,
    save_summary,
    seal_active_segment,
    search_conversations,
    strip_tool_results_from_segment,
)
from marcel_core.memory.history import HistoryMessage, MessageRole


def _msg(role: MessageRole = 'user', text: str = 'Hello', minute: int = 0) -> HistoryMessage:
    return HistoryMessage(
        role=role,
        text=text,
        timestamp=datetime(2026, 4, 10, 12, minute, tzinfo=timezone.utc),
        conversation_id='conv-1',
    )


def _segment_file(root: Path, channel: str = 'telegram', segment: str = 'seg-0001') -> Path:
    return root / 'users' / 'shaun' / 'conversation' / channel / 'segments' / f'{segment}.jsonl'


@pytest.fixture
def temp_data_root(tmp_path: Path):
    """Patch data_root to use temporary directory."""
    with patch('marcel_core.memory.conversation.data_root', return_value=tmp_path):
        yield tmp_path


# ---------------------------------------------------------------------------
# Channel metadata
# ---------------------------------------------------------------------------


class TestEnsureChannel:
    def test_creates_channel_structure(self, temp_data_root):
        meta = ensure_channel('shaun', 'telegram')
        assert meta.channel == 'telegram'
        assert meta.active_segment == 'seg-0001'
        assert meta.next_segment_num == 2
        assert meta.total_messages == 0

        # Verify directory structure
        conv_dir = temp_data_root / 'users' / 'shaun' / 'conversation' / 'telegram'
        assert (conv_dir / 'segments').is_dir()
        assert (conv_dir / 'summaries').is_dir()
        assert (conv_dir / 'segments' / 'seg-0001.jsonl').exists()

    def test_idempotent(self, temp_data_root):
        meta1 = ensure_channel('shaun', 'telegram')
        meta2 = ensure_channel('shaun', 'telegram')
        assert meta1.active_segment == meta2.active_segment

    def test_different_channels_independent(self, temp_data_root):
        meta_tg = ensure_channel('shaun', 'telegram')
        meta_cli = ensure_channel('shaun', 'cli')
        assert meta_tg.active_segment == 'seg-0001'
        assert meta_cli.active_segment == 'seg-0001'


class TestChannelMetaSerialization:
    def test_round_trip(self):
        now = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
        meta = ChannelMeta(
            channel='telegram',
            created_at=now,
            last_active=now,
            active_segment='seg-0005',
            next_segment_num=6,
            total_messages=100,
            last_summary_at=now,
        )
        d = meta.to_dict()
        parsed = ChannelMeta.from_dict(d)
        assert parsed.channel == 'telegram'
        assert parsed.active_segment == 'seg-0005'
        assert parsed.next_segment_num == 6
        assert parsed.total_messages == 100


# ---------------------------------------------------------------------------
# Segment read/write
# ---------------------------------------------------------------------------


class TestAppendToSegment:
    def test_appends_and_reads(self, temp_data_root):
        msg = HistoryMessage(
            role='user',
            text='Hello Marcel',
            timestamp=datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
            conversation_id='telegram-123',
        )
        meta = append_to_segment('shaun', 'telegram', msg)
        assert meta.total_messages == 1

        messages = read_active_segment('shaun', 'telegram')
        assert len(messages) == 1
        assert messages[0].text == 'Hello Marcel'
        assert messages[0].role == 'user'

    def test_multiple_messages(self, temp_data_root):
        for i in range(5):
            msg = HistoryMessage(
                role='user' if i % 2 == 0 else 'assistant',
                text=f'Message {i}',
                timestamp=datetime(2026, 4, 10, 12, i, tzinfo=timezone.utc),
                conversation_id='conv-1',
            )
            append_to_segment('shaun', 'telegram', msg)

        messages = read_active_segment('shaun', 'telegram')
        assert len(messages) == 5

    def test_updates_search_index(self, temp_data_root):
        msg = HistoryMessage(
            role='user',
            text='Book a dentist appointment please',
            timestamp=datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
            conversation_id='conv-1',
        )
        append_to_segment('shaun', 'telegram', msg)

        # Search for the keyword
        results = search_conversations('shaun', 'telegram', 'dentist')
        assert len(results) == 1
        entry, context = results[0]
        assert 'dentist' in entry.keywords


# ---------------------------------------------------------------------------
# Segment sealing
# ---------------------------------------------------------------------------


class TestSealActiveSegment:
    def test_seals_and_opens_new(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        msg = HistoryMessage(
            role='user',
            text='Hello',
            timestamp=datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
            conversation_id='conv-1',
        )
        append_to_segment('shaun', 'telegram', msg)

        sealed_id, meta = seal_active_segment('shaun', 'telegram')
        assert sealed_id == 'seg-0001'
        assert meta.active_segment == 'seg-0002'
        assert meta.next_segment_num == 3

        # Old segment still readable
        old_messages = read_segment('shaun', 'telegram', 'seg-0001')
        assert len(old_messages) == 1

        # New segment is empty
        new_messages = read_active_segment('shaun', 'telegram')
        assert len(new_messages) == 0


class TestStripToolResults:
    def test_strips_tool_messages(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        # Add a tool result message
        msgs = [
            HistoryMessage(
                role='user',
                text='Check the weather',
                timestamp=datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
                conversation_id='conv-1',
            ),
            HistoryMessage(
                role='tool',
                text='Temperature: 22C, sunny with clouds. Humidity: 65%. Wind: 10km/h NW.',
                timestamp=datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
                conversation_id='conv-1',
                tool_name='weather',
                tool_call_id='tc-1',
            ),
            HistoryMessage(
                role='assistant',
                text="It's 22 degrees and sunny!",
                timestamp=datetime(2026, 4, 10, 12, 1, tzinfo=timezone.utc),
                conversation_id='conv-1',
            ),
        ]
        for m in msgs:
            append_to_segment('shaun', 'telegram', m)

        stripped = strip_tool_results_from_segment('shaun', 'telegram', 'seg-0001')
        assert stripped == 1

        # Read back and verify
        messages = read_segment('shaun', 'telegram', 'seg-0001')
        tool_msg = [m for m in messages if m.role == 'tool'][0]
        assert tool_msg.text == '[weather: completed]'
        assert tool_msg.result_ref is None


# ---------------------------------------------------------------------------
# Summary operations
# ---------------------------------------------------------------------------


class TestSegmentSummary:
    def test_markdown_round_trip(self):
        summary = SegmentSummary(
            segment_id='seg-0001',
            created_at=datetime(2026, 4, 10, 13, 0, tzinfo=timezone.utc),
            trigger='idle',
            message_count=42,
            time_span_from=datetime(2026, 4, 10, 10, 0, tzinfo=timezone.utc),
            time_span_to=datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
            summary='The user discussed dinner plans and scheduled a dentist appointment.',
            key_facts=['Dinner at 7pm', 'Dentist on Monday'],
            previous_summary_segment='seg-0000',
        )
        md = summary.to_markdown()
        parsed = SegmentSummary.from_markdown(md)
        assert parsed.segment_id == 'seg-0001'
        assert parsed.trigger == 'idle'
        assert parsed.message_count == 42
        assert parsed.summary == 'The user discussed dinner plans and scheduled a dentist appointment.'
        assert parsed.key_facts == ['Dinner at 7pm', 'Dentist on Monday']
        assert parsed.previous_summary_segment == 'seg-0000'

    def test_save_and_load(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        summary = SegmentSummary(
            segment_id='seg-0001',
            created_at=datetime(2026, 4, 10, 13, 0, tzinfo=timezone.utc),
            trigger='idle',
            message_count=10,
            time_span_from=datetime(2026, 4, 10, 10, 0, tzinfo=timezone.utc),
            time_span_to=datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
            summary='Test summary content.',
        )
        save_summary('shaun', 'telegram', summary)
        loaded = load_summary('shaun', 'telegram', 'seg-0001')
        assert loaded is not None
        assert loaded.summary == 'Test summary content.'

    def test_load_latest(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        for i in range(3):
            summary = SegmentSummary(
                segment_id=f'seg-{i + 1:04d}',
                created_at=datetime(2026, 4, 10, 10 + i, tzinfo=timezone.utc),
                trigger='idle',
                message_count=10,
                time_span_from=datetime(2026, 4, 10, 10, tzinfo=timezone.utc),
                time_span_to=datetime(2026, 4, 10, 12, tzinfo=timezone.utc),
                summary=f'Summary {i + 1}',
            )
            save_summary('shaun', 'telegram', summary)

        latest = load_latest_summary('shaun', 'telegram')
        assert latest is not None
        assert latest.summary == 'Summary 3'


# ---------------------------------------------------------------------------
# Keyword search
# ---------------------------------------------------------------------------


class TestExtractKeywords:
    def test_basic_extraction(self):
        kw = extract_keywords('Book a dentist appointment please')
        assert 'book' in kw
        assert 'dentist' in kw
        assert 'appointment' in kw
        assert 'please' not in kw  # stopword

    def test_deduplication(self):
        kw = extract_keywords('the cat sat on the cat')
        assert kw.count('cat') == 1

    def test_short_words_filtered(self):
        kw = extract_keywords('I am OK')
        assert 'am' not in kw

    def test_empty_string(self):
        assert extract_keywords('') == []


class TestSearchConversations:
    def test_finds_matching_messages(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        messages = [
            HistoryMessage(
                role='user',
                text='I need to book a dentist appointment',
                timestamp=datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
                conversation_id='conv-1',
            ),
            HistoryMessage(
                role='assistant',
                text='I can help with that dentist booking',
                timestamp=datetime(2026, 4, 10, 12, 1, tzinfo=timezone.utc),
                conversation_id='conv-1',
            ),
            HistoryMessage(
                role='user',
                text='What is the weather like?',
                timestamp=datetime(2026, 4, 10, 12, 2, tzinfo=timezone.utc),
                conversation_id='conv-1',
            ),
        ]
        for m in messages:
            append_to_segment('shaun', 'telegram', m)

        results = search_conversations('shaun', 'telegram', 'dentist')
        assert len(results) >= 1

    def test_no_results_for_unknown_query(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        msg = HistoryMessage(
            role='user',
            text='Hello world',
            timestamp=datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
            conversation_id='conv-1',
        )
        append_to_segment('shaun', 'telegram', msg)

        results = search_conversations('shaun', 'telegram', 'xyznonexistent')
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Idle detection
# ---------------------------------------------------------------------------


class TestIdleDetection:
    def test_not_idle_when_recent(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        msg = HistoryMessage(
            role='user',
            text='Hello',
            timestamp=datetime.now(tz=timezone.utc),
            conversation_id='conv-1',
        )
        append_to_segment('shaun', 'telegram', msg)
        assert is_idle('shaun', 'telegram', idle_minutes=60) is False

    def test_idle_when_no_channel(self, temp_data_root):
        assert is_idle('shaun', 'telegram', idle_minutes=60) is False

    def test_has_active_content(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        assert has_active_content('shaun', 'telegram') is False

        msg = HistoryMessage(
            role='user',
            text='Hello',
            timestamp=datetime.now(tz=timezone.utc),
            conversation_id='conv-1',
        )
        append_to_segment('shaun', 'telegram', msg)
        assert has_active_content('shaun', 'telegram') is True


# ---------------------------------------------------------------------------
# list_channels
# ---------------------------------------------------------------------------


class TestListChannels:
    def test_empty_user(self, temp_data_root):
        assert list_channels('nobody') == []

    def test_lists_channels_sorted_by_last_active(self, temp_data_root):
        ensure_channel('shaun', 'cli')
        ensure_channel('shaun', 'telegram')

        # Append a message to telegram to make it more recent
        msg = HistoryMessage(
            role='user',
            text='hi',
            timestamp=datetime.now(tz=timezone.utc),
            conversation_id='tg-conv',
        )
        append_to_segment('shaun', 'telegram', msg)

        channels = list_channels('shaun')
        assert len(channels) == 2
        assert channels[0].channel == 'telegram'  # most recent
        assert channels[1].channel == 'cli'


# ---------------------------------------------------------------------------
# SegmentSummary parsing edge cases
# ---------------------------------------------------------------------------


class TestSegmentSummaryParsingEdgeCases:
    def test_missing_frontmatter_raises(self):
        with pytest.raises(ValueError, match='frontmatter'):
            SegmentSummary.from_markdown('Just a plain summary with no frontmatter.')

    def test_frontmatter_line_without_colon_ignored(self):
        md = (
            '---\n'
            'segment_id: seg-0001\n'
            'created_at: 2026-04-10T13:00:00+00:00\n'
            'trigger: idle\n'
            'message_count: 5\n'
            'time_span_from: 2026-04-10T10:00:00+00:00\n'
            'time_span_to: 2026-04-10T12:00:00+00:00\n'
            'a stray line with no separator\n'
            '---\n'
            '\n'
            'Talked about groceries.\n'
        )
        parsed = SegmentSummary.from_markdown(md)
        assert parsed.segment_id == 'seg-0001'
        assert parsed.summary == 'Talked about groceries.'

    def test_blank_line_inside_key_facts_section_skipped(self):
        md = (
            '---\n'
            'segment_id: seg-0002\n'
            'created_at: 2026-04-10T13:00:00+00:00\n'
            'trigger: manual\n'
            'message_count: 3\n'
            'time_span_from: 2026-04-10T10:00:00+00:00\n'
            'time_span_to: 2026-04-10T12:00:00+00:00\n'
            '---\n'
            '\n'
            'Planned the week.\n'
            '\n'
            '## Key Facts\n'
            '\n'
            '- Dentist on Monday\n'
        )
        parsed = SegmentSummary.from_markdown(md)
        assert parsed.key_facts == ['Dentist on Monday']
        assert 'Dentist' not in parsed.summary


# ---------------------------------------------------------------------------
# Channel metadata edge cases
# ---------------------------------------------------------------------------


class TestChannelMetaEdgeCases:
    def test_corrupt_meta_file_returns_none(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        meta_path = temp_data_root / 'users' / 'shaun' / 'conversation' / 'telegram' / 'channel.meta.json'
        meta_path.write_text('{this is not valid json', encoding='utf-8')
        assert load_channel_meta('shaun', 'telegram') is None

    def test_stray_file_in_conversation_root_ignored(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        (temp_data_root / 'users' / 'shaun' / 'conversation' / 'notes.txt').write_text('scratch', encoding='utf-8')
        channels = list_channels('shaun')
        assert [c.channel for c in channels] == ['telegram']

    def test_channel_dir_without_meta_skipped(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        (temp_data_root / 'users' / 'shaun' / 'conversation' / 'ghost').mkdir()
        channels = list_channels('shaun')
        assert [c.channel for c in channels] == ['telegram']


# ---------------------------------------------------------------------------
# Segment read edge cases
# ---------------------------------------------------------------------------


class TestReadSegmentEdgeCases:
    def test_missing_segment_returns_empty(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        assert read_segment('shaun', 'telegram', 'seg-0099') == []

    def test_blank_and_corrupt_lines_skipped(self, temp_data_root):
        append_to_segment('shaun', 'telegram', _msg(text='Real message'))
        seg = _segment_file(temp_data_root)
        with open(seg, 'a', encoding='utf-8') as f:
            f.write('\n')
            f.write('{broken json line\n')
        messages = read_segment('shaun', 'telegram', 'seg-0001')
        assert len(messages) == 1
        assert messages[0].text == 'Real message'


# ---------------------------------------------------------------------------
# Segment rotation
# ---------------------------------------------------------------------------


class TestSegmentRotation:
    def test_rotates_when_message_count_exceeded(self, temp_data_root, monkeypatch):
        monkeypatch.setattr(conversation_module, 'MAX_SEGMENT_MESSAGES', 2)
        meta = None
        for i in range(3):
            meta = append_to_segment('shaun', 'telegram', _msg(text=f'Message number {i}', minute=i))
        assert meta is not None
        assert meta.active_segment == 'seg-0002'
        assert meta.next_segment_num == 3
        assert len(read_segment('shaun', 'telegram', 'seg-0001')) == 2
        assert len(read_active_segment('shaun', 'telegram')) == 1

    def test_rotates_when_byte_size_exceeded(self, temp_data_root, monkeypatch):
        monkeypatch.setattr(conversation_module, 'MAX_SEGMENT_BYTES', 1)
        append_to_segment('shaun', 'telegram', _msg(text='First message'))
        meta = append_to_segment('shaun', 'telegram', _msg(text='Second message', minute=1))
        assert meta.active_segment == 'seg-0002'
        assert len(read_segment('shaun', 'telegram', 'seg-0001')) == 1
        assert len(read_segment('shaun', 'telegram', 'seg-0002')) == 1

    def test_append_recreates_deleted_segment_file(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        _segment_file(temp_data_root).unlink()
        meta = append_to_segment('shaun', 'telegram', _msg(text='After deletion'))
        assert meta.active_segment == 'seg-0001'  # no rotation
        messages = read_active_segment('shaun', 'telegram')
        assert [m.text for m in messages] == ['After deletion']


# ---------------------------------------------------------------------------
# strip_tool_results edge cases
# ---------------------------------------------------------------------------


class TestStripToolResultsEdgeCases:
    def test_missing_segment_returns_zero(self, temp_data_root):
        assert strip_tool_results_from_segment('shaun', 'telegram', 'seg-0042') == 0

    def test_segment_without_tool_messages_untouched(self, temp_data_root):
        append_to_segment('shaun', 'telegram', _msg(role='user', text='Hi Marcel'))
        append_to_segment('shaun', 'telegram', _msg(role='assistant', text='Hi Shaun', minute=1))
        before = _segment_file(temp_data_root).read_text(encoding='utf-8')
        assert strip_tool_results_from_segment('shaun', 'telegram', 'seg-0001') == 0
        assert _segment_file(temp_data_root).read_text(encoding='utf-8') == before


# ---------------------------------------------------------------------------
# Summary loading edge cases
# ---------------------------------------------------------------------------


class TestSummaryLoadingEdgeCases:
    def _summaries_dir(self, root: Path) -> Path:
        return root / 'users' / 'shaun' / 'conversation' / 'telegram' / 'summaries'

    def _valid_summary(self, segment_id: str) -> SegmentSummary:
        return SegmentSummary(
            segment_id=segment_id,
            created_at=datetime(2026, 4, 10, 13, 0, tzinfo=timezone.utc),
            trigger='idle',
            message_count=4,
            time_span_from=datetime(2026, 4, 10, 10, 0, tzinfo=timezone.utc),
            time_span_to=datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
            summary=f'Summary for {segment_id}',
        )

    def test_load_summary_missing_returns_none(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        assert load_summary('shaun', 'telegram', 'seg-0001') is None

    def test_load_summary_corrupt_returns_none(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        corrupt = self._summaries_dir(temp_data_root) / 'seg-0001.summary.md'
        corrupt.write_text('no frontmatter at all', encoding='utf-8')
        assert load_summary('shaun', 'telegram', 'seg-0001') is None

    def test_load_latest_skips_corrupt_newest(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        save_summary('shaun', 'telegram', self._valid_summary('seg-0001'))
        corrupt = self._summaries_dir(temp_data_root) / 'seg-0002.summary.md'
        corrupt.write_text('garbage', encoding='utf-8')
        latest = load_latest_summary('shaun', 'telegram')
        assert latest is not None
        assert latest.segment_id == 'seg-0001'

    def test_load_latest_all_corrupt_returns_none(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        corrupt = self._summaries_dir(temp_data_root) / 'seg-0001.summary.md'
        corrupt.write_text('garbage', encoding='utf-8')
        assert load_latest_summary('shaun', 'telegram') is None


class TestListSummaries:
    def test_missing_dir_returns_empty(self, temp_data_root):
        assert list_summaries('shaun', 'telegram') == []

    def test_sorted_oldest_first(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        for seg in ('seg-0002', 'seg-0001'):
            save_summary(
                'shaun',
                'telegram',
                SegmentSummary(
                    segment_id=seg,
                    created_at=datetime(2026, 4, 10, 13, 0, tzinfo=timezone.utc),
                    trigger='idle',
                    message_count=1,
                    time_span_from=datetime(2026, 4, 10, 10, 0, tzinfo=timezone.utc),
                    time_span_to=datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
                    summary=f'Summary {seg}',
                ),
            )
        assert list_summaries('shaun', 'telegram') == ['seg-0001', 'seg-0002']


# ---------------------------------------------------------------------------
# Search edge cases
# ---------------------------------------------------------------------------


class TestSearchEdgeCases:
    def test_stopword_only_query_returns_empty(self, temp_data_root):
        append_to_segment('shaun', 'telegram', _msg(text='Book a dentist appointment'))
        assert search_conversations('shaun', 'telegram', 'the is a') == []

    def test_blank_and_corrupt_index_lines_skipped(self, temp_data_root):
        append_to_segment('shaun', 'telegram', _msg(text='Book a dentist appointment'))
        index_path = temp_data_root / 'users' / 'shaun' / 'conversation' / 'telegram' / 'search_index.jsonl'
        with open(index_path, 'a', encoding='utf-8') as f:
            f.write('\n')
            f.write('{broken index entry\n')
        results = search_conversations('shaun', 'telegram', 'dentist')
        assert len(results) == 1

    def test_index_skips_message_with_empty_text(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        conversation_module._append_search_index('shaun', 'telegram', 'seg-0001', _msg(text=''))
        index_path = temp_data_root / 'users' / 'shaun' / 'conversation' / 'telegram' / 'search_index.jsonl'
        assert not index_path.exists()


# ---------------------------------------------------------------------------
# has_active_content edge cases
# ---------------------------------------------------------------------------


class TestHasActiveContentEdgeCases:
    def test_missing_segment_file_returns_false(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        _segment_file(temp_data_root).unlink()
        assert has_active_content('shaun', 'telegram') is False

    def test_assistant_only_content_returns_false(self, temp_data_root):
        append_to_segment('shaun', 'telegram', _msg(role='assistant', text='Proactive reminder'))
        assert has_active_content('shaun', 'telegram') is False

    def test_blank_and_corrupt_lines_skipped(self, temp_data_root):
        ensure_channel('shaun', 'telegram')
        seg = _segment_file(temp_data_root)
        with open(seg, 'a', encoding='utf-8') as f:
            f.write('\n')
            f.write('{broken line\n')
            f.write(_msg(role='user', text='Real question').to_jsonl() + '\n')
        assert has_active_content('shaun', 'telegram') is True
