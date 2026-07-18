"""Scenario-based tests for tools/marcel.py — the unified internal utilities tool.

Covers: all actions (search_conversations, compact, notify, list_models,
get_model, set_model, render) through realistic invocations, plus the retired
``read_skill`` / ``read_skill_resource`` actions that now redirect to
``load_capability``.
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
        assert 'search_conversations' in result


# ---------------------------------------------------------------------------
# retired memory actions redirect to the Memory capability tools
# ---------------------------------------------------------------------------


class TestRetiredMemoryActions:
    @pytest.mark.asyncio
    async def test_retired_actions_point_at_the_new_tools(self):
        for action in ('search_memory', 'read_memory', 'save_memory'):
            result = await marcel(_ctx(), action=action)
            assert 'moved to dedicated tools' in result
            assert 'write_memory' in result


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
    async def test_set_model_rejects_unqualified(self):
        """Legacy short names without a provider: prefix are rejected."""
        result = await marcel(_ctx(), action='set_model', name='telegram:nonexistent-model')
        assert 'Error' in result
        assert 'fully qualified' in result

    @pytest.mark.asyncio
    async def test_set_model_success(self):
        from marcel_core.harness.agent import all_models

        models = all_models()
        model_id = next(iter(models))  # pick first available — already qualified

        result = await marcel(_ctx(), action='set_model', name=f'telegram:{model_id}')
        assert 'set to' in result

    @pytest.mark.asyncio
    async def test_set_model_no_value(self):
        result = await marcel(_ctx(), action='set_model')
        assert 'Error' in result


# ---------------------------------------------------------------------------
# retired read_skill / read_skill_resource actions
# ---------------------------------------------------------------------------


class TestRetiredSkillActions:
    """Skills are deferred capabilities now — these actions redirect (FEAT-260718-85b545)."""

    @pytest.mark.asyncio
    async def test_read_skill_redirects_to_load_capability(self):
        result = await marcel(_ctx(), action='read_skill', name='recipes')
        assert 'retired' in result
        assert 'load_capability' in result

    @pytest.mark.asyncio
    async def test_read_skill_resource_redirects(self):
        result = await marcel(_ctx(), action='read_skill_resource', name='recipes', resource='feeds')
        assert 'retired' in result
        assert 'load_capability' in result


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def _one_component_registry():
    from marcel_core.skills.component_registry import ComponentRegistry
    from marcel_core.skills.components import ComponentSchema

    return ComponentRegistry(
        [ComponentSchema(name='balance_card', description='Account balance', skill='banking', props={})]
    )


class TestRender:
    @pytest.mark.asyncio
    async def test_missing_component(self):
        result = await marcel(_ctx(), action='render')
        assert 'render failed' in result
        assert 'component' in result

    @pytest.mark.asyncio
    async def test_registry_build_failure(self, monkeypatch):
        def boom(user_slug):
            raise RuntimeError('registry exploded')

        monkeypatch.setattr('marcel_core.skills.component_registry.build_registry', boom)
        result = await marcel(_ctx(), action='render', component='balance_card', props={})
        assert 'render failed' in result
        assert 'component registry' in result

    @pytest.mark.asyncio
    async def test_none_props_default_to_empty_dict(self, monkeypatch):
        from marcel_core.storage.artifacts import load_artifact

        monkeypatch.setattr(
            'marcel_core.skills.component_registry.build_registry', lambda user_slug: _one_component_registry()
        )
        result = await marcel(_ctx(channel='cli'), action='render', component='balance_card')
        assert 'rendered component' in result
        artifact_id = result.rsplit('artifact ', 1)[1].split(';')[0].strip()
        artifact = load_artifact(artifact_id)
        assert artifact is not None
        assert artifact.content == '{}'
        assert artifact.component_name == 'balance_card'

    @pytest.mark.asyncio
    async def test_artifact_store_failure(self, monkeypatch):
        monkeypatch.setattr(
            'marcel_core.skills.component_registry.build_registry', lambda user_slug: _one_component_registry()
        )

        def boom(**kwargs):
            raise OSError('disk full')

        monkeypatch.setattr('marcel_core.storage.artifacts.create_artifact', boom)
        result = await marcel(_ctx(channel='cli'), action='render', component='balance_card', props={})
        assert 'render failed' in result
        assert 'could not store artifact' in result

    @pytest.mark.asyncio
    async def test_telegram_delivery_failure_still_reports_artifact(self, monkeypatch):
        monkeypatch.setattr(
            'marcel_core.skills.component_registry.build_registry', lambda user_slug: _one_component_registry()
        )

        class _BrokenChannel:
            async def send_artifact_link(self, user_slug, artifact_id, title):
                raise RuntimeError('telegram down')

        monkeypatch.setattr('marcel_core.plugin.get_channel', lambda name: _BrokenChannel())
        result = await marcel(_ctx(channel='telegram'), action='render', component='balance_card', props={'x': 1})
        assert 'rendered component' in result
        assert 'failed to send Telegram button' in result
