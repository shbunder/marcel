"""Scenario-based tests for tools/marcel.py — the unified internal utilities tool.

Covers: all actions (read_skill, search_memory, save_memory, search_conversations,
compact, notify, list_models, get_model, set_model) through realistic invocations.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marcel_core.harness.context import MarcelDeps
from marcel_core.storage import _root
from marcel_core.tools.marcel import marcel


def _ctx(user: str = 'alice', channel: str = 'telegram') -> MagicMock:
    deps = MarcelDeps(user_slug=user, conversation_id='conv-1', channel=channel)
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)


# ---------------------------------------------------------------------------
# Unknown action
# ---------------------------------------------------------------------------


class TestUnknownAction:
    @pytest.mark.asyncio
    async def test_unknown_action(self):
        result = await marcel(_ctx(), action='bogus')
        assert 'Unknown action' in result
        assert 'read_skill' in result


# ---------------------------------------------------------------------------
# search_memory
# ---------------------------------------------------------------------------


class TestSearchMemory:
    @pytest.mark.asyncio
    async def test_missing_query(self):
        result = await marcel(_ctx(), action='search_memory')
        assert 'Error' in result

    @pytest.mark.asyncio
    async def test_invalid_type_filter(self):
        result = await marcel(_ctx(), action='search_memory', query='test', type_filter='bogus')
        assert 'Invalid type filter' in result

    @pytest.mark.asyncio
    async def test_no_results(self):
        result = await marcel(_ctx(), action='search_memory', query='nonexistent')
        assert 'No memories found' in result

    @pytest.mark.asyncio
    async def test_with_results(self, tmp_path):
        mem_dir = tmp_path / 'users' / 'alice' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'index.md').write_text('# Memory Index\n- [coffee](coffee.md)\n')
        (mem_dir / 'coffee.md').write_text(
            '---\nname: coffee pref\ndescription: likes lattes\ntype: preference\n---\nAlice loves lattes.\n'
        )

        result = await marcel(_ctx(), action='search_memory', query='coffee')
        assert 'coffee' in result


# ---------------------------------------------------------------------------
# read_memory
# ---------------------------------------------------------------------------


class TestReadMemory:
    @pytest.mark.asyncio
    async def test_missing_name(self):
        result = await marcel(_ctx(), action='read_memory')
        assert 'Error' in result

    @pytest.mark.asyncio
    async def test_unknown_name_lists_available(self, tmp_path):
        mem_dir = tmp_path / 'users' / 'alice' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'family.md').write_text('---\nname: family\ndescription: Family members\n---\nBody.\n')

        result = await marcel(_ctx(), action='read_memory', name='nonexistent')
        assert 'Unknown memory' in result
        assert 'family' in result

    @pytest.mark.asyncio
    async def test_loads_full_file(self, tmp_path):
        mem_dir = tmp_path / 'users' / 'alice' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'family.md').write_text(
            '---\nname: family\ndescription: Family members\ntype: household\n---\nCosette is the partner.\n'
        )

        result = await marcel(_ctx(), action='read_memory', name='family')
        assert 'Cosette' in result
        assert 'family' in result
        assert '[household]' in result

    @pytest.mark.asyncio
    async def test_accepts_filename_with_md_suffix(self, tmp_path):
        mem_dir = tmp_path / 'users' / 'alice' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'work.md').write_text('---\nname: work\ndescription: job\n---\nShifts.\n')

        result = await marcel(_ctx(), action='read_memory', name='work.md')
        assert 'Shifts' in result


# ---------------------------------------------------------------------------
# save_memory
# ---------------------------------------------------------------------------


class TestSaveMemory:
    @pytest.mark.asyncio
    async def test_missing_name(self):
        result = await marcel(_ctx(), action='save_memory', message='content')
        assert 'Error' in result

    @pytest.mark.asyncio
    async def test_missing_content(self):
        result = await marcel(_ctx(), action='save_memory', name='test.md')
        assert 'Error' in result

    @pytest.mark.asyncio
    async def test_saves_file(self, tmp_path):
        mem_dir = tmp_path / 'users' / 'alice' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'index.md').write_text('# Memory Index\n')

        content = '---\nname: test\ndescription: test memory\ntype: fact\n---\nSome content.\n'
        result = await marcel(_ctx(), action='save_memory', name='test', message=content)
        assert 'Saved' in result
        assert (mem_dir / 'test.md').exists()


# ---------------------------------------------------------------------------
# search_conversations
# ---------------------------------------------------------------------------


class TestSearchConversations:
    @pytest.mark.asyncio
    async def test_missing_query(self):
        result = await marcel(_ctx(), action='search_conversations')
        assert 'Error' in result

    @pytest.mark.asyncio
    async def test_no_results(self):
        result = await marcel(_ctx(), action='search_conversations', query='xyzzy')
        assert 'No past conversation' in result

    @pytest.mark.asyncio
    async def test_with_results(self):
        from unittest.mock import MagicMock

        entry = MagicMock()
        entry.segment = 'seg-001'
        entry.timestamp = '2026-04-10T10:00:00'

        msg1 = MagicMock()
        msg1.role = 'user'
        msg1.text = 'What is the weather in Brussels?'
        msg2 = MagicMock()
        msg2.role = 'assistant'
        msg2.text = 'It is sunny and 22°C in Brussels today.'

        with patch(
            'marcel_core.memory.conversation.search_conversations',
            return_value=[(entry, [msg1, msg2])],
        ):
            result = await marcel(_ctx(), action='search_conversations', query='Brussels')
        assert 'Brussels' in result
        assert 'seg-001' in result

    @pytest.mark.asyncio
    async def test_truncates_long_text(self):
        entry = MagicMock()
        entry.segment = 'seg-001'
        entry.timestamp = '2026-04-10T10:00:00'

        msg = MagicMock()
        msg.role = 'user'
        msg.text = 'x' * 400

        with patch(
            'marcel_core.memory.conversation.search_conversations',
            return_value=[(entry, [msg])],
        ):
            result = await marcel(_ctx(), action='search_conversations', query='test')
        assert '...' in result


# ---------------------------------------------------------------------------
# compact
# ---------------------------------------------------------------------------


class TestCompact:
    @pytest.mark.asyncio
    async def test_nothing_to_compact(self):
        result = await marcel(_ctx(), action='compact')
        assert 'Nothing to compress' in result

    @pytest.mark.asyncio
    async def test_successful_compact(self):
        from datetime import datetime, timezone

        from marcel_core.memory.conversation import SegmentSummary

        summary = SegmentSummary(
            segment_id='seg-001',
            created_at=datetime.now(timezone.utc),
            trigger='manual',
            message_count=15,
            time_span_from=datetime(2026, 4, 11, 9, 0, tzinfo=timezone.utc),
            time_span_to=datetime(2026, 4, 11, 10, 30, tzinfo=timezone.utc),
            summary='The user discussed weather and scheduling.',
        )
        with (
            patch('marcel_core.memory.summarizer.summarize_active_segment', new_callable=AsyncMock, return_value=True),
            patch('marcel_core.memory.conversation.load_latest_summary', return_value=summary),
        ):
            result = await marcel(_ctx(), action='compact')
        assert 'Conversation compressed' in result
        assert '15 messages' in result
        assert 'weather' in result

    @pytest.mark.asyncio
    async def test_compact_success_no_summary(self):
        with (
            patch('marcel_core.memory.summarizer.summarize_active_segment', new_callable=AsyncMock, return_value=True),
            patch('marcel_core.memory.conversation.load_latest_summary', return_value=None),
        ):
            result = await marcel(_ctx(), action='compact')
        assert 'compressed successfully' in result


# ---------------------------------------------------------------------------
# notify
# ---------------------------------------------------------------------------


class TestNotify:
    @pytest.mark.asyncio
    async def test_empty_message(self):
        result = await marcel(_ctx(), action='notify')
        assert result == 'ok'

    @pytest.mark.asyncio
    async def test_telegram_notify(self):
        with (
            patch('marcel_core.channels.telegram.sessions.get_chat_id', return_value='123'),
            patch('marcel_core.channels.telegram.bot.send_message', new_callable=AsyncMock, return_value=1),
        ):
            result = await marcel(
                _ctx(channel='telegram'),
                action='notify',
                message='Hello!',
            )
        assert result == 'ok'

    @pytest.mark.asyncio
    async def test_telegram_notify_failure(self):
        with (
            patch('marcel_core.channels.telegram.sessions.get_chat_id', return_value='123'),
            patch(
                'marcel_core.channels.telegram.bot.send_message',
                new_callable=AsyncMock,
                side_effect=RuntimeError('fail'),
            ),
        ):
            result = await marcel(
                _ctx(channel='telegram'),
                action='notify',
                message='Hello!',
            )
        assert 'failed' in result

    @pytest.mark.asyncio
    async def test_non_telegram_notify(self):
        result = await marcel(
            _ctx(channel='cli'),
            action='notify',
            message='Progress update',
        )
        assert result == 'ok'

    @pytest.mark.asyncio
    async def test_job_channel_uses_telegram(self):
        with (
            patch('marcel_core.channels.telegram.sessions.get_chat_id', return_value='123'),
            patch('marcel_core.channels.telegram.bot.send_message', new_callable=AsyncMock, return_value=1),
        ):
            result = await marcel(
                _ctx(channel='job'),
                action='notify',
                message='Job update',
            )
        assert result == 'ok'

    @pytest.mark.asyncio
    async def test_suppressed_by_policy_does_not_send(self):
        ctx = _ctx(channel='job')
        ctx.deps.turn.suppress_notify = True
        send = AsyncMock()
        with (
            patch('marcel_core.channels.telegram.sessions.get_chat_id', return_value='123'),
            patch('marcel_core.channels.telegram.bot.send_message', send),
        ):
            result = await marcel(ctx, action='notify', message='Should not reach user')
        assert 'suppressed' in result
        send.assert_not_called()
        assert ctx.deps.turn.notified is False


# ---------------------------------------------------------------------------
# Settings: list_models, get_model, set_model
# ---------------------------------------------------------------------------


class TestSettings:
    @pytest.mark.asyncio
    async def test_list_models(self):
        result = await marcel(_ctx(), action='list_models')
        assert 'Available models' in result
        assert 'Default' in result

    @pytest.mark.asyncio
    async def test_get_model_current_channel(self):
        result = await marcel(_ctx(), action='get_model')
        assert 'Current model' in result

    @pytest.mark.asyncio
    async def test_get_model_specific_channel(self):
        result = await marcel(_ctx(), action='get_model', name='cli')
        assert 'cli' in result

    @pytest.mark.asyncio
    async def test_set_model_missing_colon(self):
        result = await marcel(_ctx(), action='set_model', name='just-a-model')
        assert 'Error' in result

    @pytest.mark.asyncio
    async def test_set_model_missing_parts(self):
        result = await marcel(_ctx(), action='set_model', name=':')
        assert 'Error' in result

    @pytest.mark.asyncio
    async def test_set_model_unknown_model(self):
        result = await marcel(_ctx(), action='set_model', name='telegram:nonexistent-model')
        assert 'Error' in result
        assert 'unknown model' in result

    @pytest.mark.asyncio
    async def test_set_model_success(self):
        from marcel_core.harness.agent import all_models

        models = all_models()
        model_id = next(iter(models))  # pick first available

        result = await marcel(_ctx(), action='set_model', name=f'telegram:{model_id}')
        assert 'set to' in result

    @pytest.mark.asyncio
    async def test_set_model_no_value(self):
        result = await marcel(_ctx(), action='set_model')
        assert 'Error' in result
