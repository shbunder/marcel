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
# read_skill / read_skill_resource
# ---------------------------------------------------------------------------


@pytest.fixture
def skills_dir(tmp_path, monkeypatch):
    """A hermetic skills directory with one documented skill.

    ``recipes`` has a SKILL.md plus two resource files; ``plain`` ships only
    its SKILL.md (no resources).
    """
    import marcel_core.skills.loader as loader
    from marcel_core.config import settings

    root = tmp_path / 'skills'
    recipes = root / 'recipes'
    recipes.mkdir(parents=True)
    (recipes / 'SKILL.md').write_text('---\nname: recipes\ndescription: Family recipes\n---\n\nCook things.\n')
    (recipes / 'SETUP.md').write_text('Set up the recipe book first.\n')
    (recipes / 'feeds.yaml').write_text('feeds:\n  - https://example.test/rss\n')

    plain = root / 'plain'
    plain.mkdir()
    (plain / 'SKILL.md').write_text('---\nname: plain\ndescription: No extras\n---\n\nJust the doc.\n')

    monkeypatch.setattr(settings, 'marcel_zoo_dir', None)
    monkeypatch.setattr(loader, '_skills_dir', lambda: root)
    return root


class TestReadSkill:
    @pytest.mark.asyncio
    async def test_skill_without_resources_has_no_resource_footer(self, skills_dir):
        result = await marcel(_ctx(), action='read_skill', name='plain')
        assert 'Just the doc.' in result
        assert 'Available resources' not in result

    @pytest.mark.asyncio
    async def test_skill_with_resources_lists_them(self, skills_dir):
        result = await marcel(_ctx(), action='read_skill', name='recipes')
        assert 'Cook things.' in result
        assert 'Available resources' in result
        assert 'SETUP.md' in result
        assert 'feeds.yaml' in result


class TestReadSkillResource:
    @pytest.mark.asyncio
    async def test_missing_skill_name(self, skills_dir):
        result = await marcel(_ctx(), action='read_skill_resource', resource='feeds')
        assert 'Error' in result
        assert 'name=' in result

    @pytest.mark.asyncio
    async def test_missing_resource_name(self, skills_dir):
        result = await marcel(_ctx(), action='read_skill_resource', name='recipes')
        assert 'Error' in result
        assert 'resource=' in result

    @pytest.mark.asyncio
    async def test_loads_resource_by_stem(self, skills_dir):
        result = await marcel(_ctx(), action='read_skill_resource', name='recipes', resource='feeds')
        assert 'https://example.test/rss' in result

    @pytest.mark.asyncio
    async def test_loads_resource_by_filename(self, skills_dir):
        result = await marcel(_ctx(), action='read_skill_resource', name='recipes', resource='SETUP.md')
        assert 'Set up the recipe book' in result

    @pytest.mark.asyncio
    async def test_unknown_resource_lists_available(self, skills_dir):
        result = await marcel(_ctx(), action='read_skill_resource', name='recipes', resource='bogus')
        assert 'not found' in result
        assert 'SETUP.md' in result
        assert 'feeds.yaml' in result

    @pytest.mark.asyncio
    async def test_skill_without_resources(self, skills_dir):
        result = await marcel(_ctx(), action='read_skill_resource', name='plain', resource='anything')
        assert 'no resource files' in result

    @pytest.mark.asyncio
    async def test_unknown_skill(self, skills_dir):
        result = await marcel(_ctx(), action='read_skill_resource', name='ghost-skill', resource='feeds')
        assert 'no resource files' in result or 'not found' in result


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


# ---------------------------------------------------------------------------
# ratchet edges (STORY-260707-c332a7)
# ---------------------------------------------------------------------------


class TestMemoryEdges:
    @pytest.mark.asyncio
    async def test_search_hit_in_description_renders_without_snippet(self, tmp_path):
        mem_dir = tmp_path / 'users' / 'alice' / 'memory'
        mem_dir.mkdir(parents=True)
        # Empty body: the description matches, no snippet can exist.
        (mem_dir / 'coffee.md').write_text('---\nname: coffee\ndescription: Alice loves espresso.\ntype: fact\n---\n')
        result = await marcel(_ctx(), action='search_memory', query='espresso')
        assert 'coffee' in result

    @pytest.mark.asyncio
    async def test_read_memory_stale_note_for_old_file(self, tmp_path):
        import os
        import time

        mem_dir = tmp_path / 'users' / 'alice' / 'memory'
        mem_dir.mkdir(parents=True)
        f = mem_dir / 'family.md'
        f.write_text('---\nname: family\ntype: fact\n---\n\nTwo kids.\n')
        old = time.time() - 200 * 24 * 3600
        os.utime(f, (old, old))

        result = await marcel(_ctx(), action='read_memory', name='family')
        assert 'Two kids.' in result
        assert 'days old' in result

    @pytest.mark.asyncio
    async def test_read_memory_without_header_entry_skips_age(self, tmp_path):
        # index.md is loadable by name but excluded from header scans — the
        # age/staleness header is skipped rather than crashing.
        mem_dir = tmp_path / 'users' / 'alice' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'index.md').write_text('# Memory Index\n\n- coffee\n')
        result = await marcel(_ctx(), action='read_memory', name='index')
        assert 'Memory Index' in result

    @pytest.mark.asyncio
    async def test_save_memory_keeps_existing_md_suffix(self, tmp_path):
        mem_dir = tmp_path / 'users' / 'alice' / 'memory'
        mem_dir.mkdir(parents=True)
        result = await marcel(_ctx(), action='save_memory', name='routine.md', message='Espresso at 7.')
        assert 'Saved' in result
        assert (mem_dir / 'routine.md').exists()
        assert not (mem_dir / 'routine.md.md').exists()


class TestNotifyEdges:
    @pytest.mark.asyncio
    async def test_send_notify_helper_delegates_to_notify(self):
        from marcel_core.tools.marcel.notifications import send_notify

        # cli channel → logged, reported ok.
        assert await send_notify(_ctx(channel='cli'), 'heads up') == 'ok'

    @pytest.mark.asyncio
    async def test_notify_job_channel_without_telegram_channel_is_ok(self, monkeypatch):
        import marcel_core.plugin as plugin_mod

        monkeypatch.setattr(plugin_mod, 'get_channel', lambda name: None)
        result = await marcel(_ctx(channel='job'), action='notify', message='job done')
        assert result == 'ok'


class TestSetModelEdges:
    @pytest.mark.asyncio
    async def test_set_model_rejects_empty_halves(self):
        result = await marcel(_ctx(), action='set_model', name='telegram:anthropic:')
        assert 'non-empty provider and model halves' in result
