"""Kernel scenario suite — the feature's Gherkin scenarios against the real loop.

Each test mirrors one scenario from the FEAT-260706-8d6824 requirements page.
Nothing in `marcel_core.harness` is patched: the production `stream_turn`
runs end to end, with only the LLM replaced by an odile scripted double and
the outside world replaced by terrarium fakes.
"""

from __future__ import annotations

import pathlib

import httpx
import pytest
from odile import call_tool, reply
from pydantic_ai import models as pai_models


class TestPlainReplyPersists:
    """Scenario: plain scripted reply persists like a real turn."""

    async def test_reply_and_disk_state(self, terrarium):
        terrarium.user('alice')
        scenario = terrarium.scenario(reply('Hello Alice!'), user='alice', channel='cli')
        result = await scenario.run('hi marcel')

        assert result.reply == 'Hello Alice!'
        from marcel_core.storage.conversation import read_active_segment

        roles = [(m.role, m.text) for m in read_active_segment('alice', 'cli')]
        assert ('user', 'hi marcel') in roles
        assert ('assistant', 'Hello Alice!') in roles


class TestStreamingDeltasArriveIncrementally:
    """Scenario: the reply reaches the channel through the streaming path.

    odile's ScriptedModel splits replies into two stream chunks, but the
    production ``stream_text(delta=True, debounce_by=0.01)`` window merges
    chunks that arrive instantly, so a scripted double cannot prove a
    minimum delta *count* without pacing support in odile (follow-up noted
    on STORY-260718-9a48fc). What is provable and non-vacuous: the full
    reply text reconstructs solely from TextDelta events (so text flows
    through the streaming path, not around it), and the first delta
    precedes RunFinished (feature AC 4).
    """

    async def test_deltas_before_run_finished(self, terrarium):
        from marcel_core.harness.runner import RunFinished, TextDelta

        terrarium.user('alice')
        scenario = terrarium.scenario(reply('Streaming works!'), user='alice', channel='cli')
        result = await scenario.run('hi marcel')

        deltas = [e for e in result.events if isinstance(e, TextDelta)]
        assert deltas, 'streaming path must yield TextDelta events'
        assert ''.join(d.text for d in deltas) == 'Streaming works!'
        types = [type(e) for e in result.events]
        assert types.index(TextDelta) < types.index(RunFinished)


from tests.scenarios import probe_hooks  # noqa: E402

_PROBE_PARK = pathlib.Path(__file__).resolve().parent / 'probe'


class TestToolCallThroughBus:
    """Scenario: scripted tool call flows through the real event bus."""

    async def test_registered_test_tool_echoes(self, terrarium):
        seen: list[str] = []

        probe_hooks.reset()
        probe_hooks.HANDLERS['ping'] = lambda text, user_slug: (seen.append(text), f'pong: {text}')[1]
        terrarium.install_connector(_PROBE_PARK)

        scenario = terrarium.scenario(
            call_tool('load_capability', id='probe'),
            call_tool('ping', text='ping'),
            reply('done'),
        )
        result = await scenario.run('ping the echo tool')

        assert result.reply == 'done'
        assert seen == ['ping']
        assert 'ping' in [e.tool_name for e in result.tool_calls]
        names = [e.NAME for e in result.bus_events]
        assert names.index('tool_call') < names.index('tool_result')
        completion = next(c for c in result.completions if c.tool_name == 'ping')
        assert 'pong: ping' in completion.result


class TestExtensionDenyThroughSDK:
    """Scenario: an extension tool_call handler denies via the SDK, unchanged.

    The FEAT-260718-01da2e headline promise — extensions must not notice
    the WrapperToolset → MarcelPolicy migration. The handler rides the real
    extension replay (``extension_registry().apply_to_bus`` per turn), runs
    after the three core gates, and its deny reason becomes the tool result.
    """

    async def test_extension_deny_blocks_bash(self, terrarium):
        from marcel_core.plugin.extension import extension_registry

        def deny_bash(event, ctx):
            if event.tool_name == 'run_command':
                event.deny('extension says no')

        extension_registry().handlers.append(('tool_call', deny_bash))

        terrarium.user('root', role='admin')
        scenario = terrarium.scenario(
            call_tool('run_command', command='echo hi'),
            reply('okay'),
            user='root',
        )
        result = await scenario.run('run echo')

        denial = next(c for c in result.completions if c.tool_name == 'run_command')
        assert 'extension says no' in denial.result
        assert 'hi' not in denial.result, 'denied command must never execute'
        # The recorder (an earlier extension handler) recorded the live event;
        # the later extension handler's deny is visible on it, and the blocked
        # path emits no tool_result at all — the tool never ran.
        [recorded] = result.tool_calls
        assert recorded.blocked and recorded.block_reason == 'extension says no'
        assert result.tool_results == []


class TestExtensionRewriteReachesHistory:
    """Scenario: an extension tool_result handler's rewrite is what persists.

    Proves feature AC 3: the rewritten form is what the model sees in the
    completion AND what lands in the conversation segment on disk.
    """

    async def test_rewrite_persists_to_segment(self, terrarium):
        from marcel_core.plugin.extension import extension_registry

        def redact(event, ctx):
            event.result = event.result.replace('SECRET', '[redacted]')

        extension_registry().handlers.append(('tool_result', redact))

        probe_hooks.reset()
        probe_hooks.HANDLERS['leak'] = lambda user_slug: 'value is SECRET'
        terrarium.install_connector(_PROBE_PARK)

        terrarium.user('alice')
        scenario = terrarium.scenario(
            call_tool('load_capability', id='probe'),
            call_tool('leak'),
            reply('done'),
            user='alice',
            channel='cli',
        )
        result = await scenario.run('leak it')

        completion = next(c for c in result.completions if c.tool_name == 'leak')
        assert '[redacted]' in completion.result
        assert 'SECRET' not in completion.result

        from marcel_core.storage.conversation import read_active_segment

        texts = [m.text for m in read_active_segment('alice', 'cli') if m.text]
        assert any('[redacted]' in t for t in texts), 'rewritten form must persist'
        assert not any('SECRET' in t for t in texts), 'original must not persist anywhere'


class TestPolicyDenies:
    """Scenario: command policy denies through the real handler chain."""

    async def test_self_mod_shell_never_executes(self, terrarium):
        terrarium.user('root', role='admin')
        scenario = terrarium.scenario(
            call_tool('run_command', command='cat .env.local'),
            reply('cannot do that'),
            user='root',
        )
        result = await scenario.run('show me the secrets')

        denial = next(c for c in result.completions if c.tool_name == 'run_command')
        assert 'Blocked by command policy' in denial.result
        assert result.tool_results == []


class TestApprovalResolvesWithoutHuman:
    """Scenario: approval ask resolves without a human, audited on disk."""

    async def test_allow_once_executes_and_audits(self, terrarium):
        scratch = terrarium.data_root / 'scratch'
        scratch.mkdir()
        terrarium.user('root', role='admin')
        requests = terrarium.resolve_approvals('allow_once', channel='cli')

        scenario = terrarium.scenario(
            call_tool('run_command', command=f'rm -rf {scratch}'),
            reply('cleaned'),
            user='root',
        )
        result = await scenario.run('clear the scratch dir')

        assert result.reply == 'cleaned'
        assert not scratch.exists()
        assert len(requests) == 1
        audit = terrarium.data_root / 'approvals' / 'audit.jsonl'
        assert audit.is_file() and 'allow_once' in audit.read_text()


class TestFakeAPINeverNetwork:
    """Scenario: external API calls hit the in-process fake, never the network."""

    async def test_toolkit_handler_reaches_fake(self, terrarium):
        terrarium.fake_api('https://api.weather.test').returns('/today', json={'sky': 'sunny'})

        async def _fetch(url, user_slug):
            async with httpx.AsyncClient() as client:
                response = await client.get('https://api.weather.test/today')
            return response.json()['sky']

        probe_hooks.reset()
        probe_hooks.HANDLERS['fetch'] = _fetch
        terrarium.install_connector(_PROBE_PARK)

        scenario = terrarium.scenario(
            call_tool('load_capability', id='probe'),
            call_tool('fetch', url='https://api.weather.test/today'),
            reply('it is sunny'),
        )
        result = await scenario.run('weather?')

        assert result.reply == 'it is sunny'
        completion = next(c for c in result.completions if c.tool_name == 'fetch')
        assert 'sunny' in completion.result

    async def test_unmocked_host_raises(self, terrarium):
        terrarium.fake_api('https://api.weather.test').returns('/today', json={})
        async with httpx.AsyncClient() as client:
            with pytest.raises(Exception, match='mock'):
                await client.get('https://somewhere-else.test/x')


class TestRealProviderImpossible:
    """Scenario: a real provider request is structurally impossible."""

    def test_gate_is_down_suite_wide(self):
        assert pai_models.ALLOW_MODEL_REQUESTS is False
        with pytest.raises(RuntimeError, match='ALLOW_MODEL_REQUESTS is False'):
            pai_models.check_allow_model_requests()


class TestInjectedModelPinsTheTier:
    """Scenario: a tier pin routes the turn without fallback machinery."""

    async def test_single_provider_event_with_label(self, terrarium):
        scenario = terrarium.scenario(reply('pinned'))
        result = await scenario.run('hello')

        provider_events = result.bus('before_provider_request')
        assert len(provider_events) == 1, 'an injected model must produce a single-entry chain'
        assert provider_events[0].model == 'scripted'


class TestOversizedResultsSpillWithHandle:
    """Scenario: an oversized tool result spills at return time, unmocked.

    OverflowingToolOutput reduces the return before the model or the
    segment ever sees it: the full payload lands in the user's paste store,
    the model receives a preview plus a read_tool_result handle, and the
    reduced form is what persists (FEAT-260718-ed6d63, feature AC 3).
    """

    async def test_spill_preview_and_readback(self, terrarium):
        from marcel_core.capabilities.persistence.overflow import (
            PasteOverflowStore,
            current_overflow_user,
        )
        from marcel_core.config import settings

        big = 'x' * (settings.marcel_overflow_spill_chars + 1_000)

        probe_hooks.reset()
        probe_hooks.HANDLERS['big'] = lambda n, user_slug: big
        terrarium.install_connector(_PROBE_PARK)

        scenario = terrarium.scenario(
            call_tool('load_capability', id='probe'),
            call_tool('big'),
            reply('stored'),
            user='alice',
        )
        result = await scenario.run('dump the blob')

        assert result.reply == 'stored'
        pastes = list((terrarium.data_root / 'users' / 'alice' / '.pastes').glob('*'))
        assert pastes, 'the oversized payload must spill to the paste store'

        completion = next(c for c in result.completions if c.tool_name == 'big')
        assert big not in completion.result, 'the full payload never reaches the model inline'
        assert 'read_tool_result' in completion.result, 'the model gets a read-back handle'

        from marcel_core.storage.conversation import read_active_segment

        tool_msgs = [m for m in read_active_segment('alice', 'cli') if m.role == 'tool']
        assert tool_msgs and len(tool_msgs[0].text or '') < len(big), 'segment stores the reduced form'

        # The spill is losslessly retrievable through the store, and only
        # by its owner.
        import re

        handle_match = re.search(r"handle[\"'=:\s]+([a-z0-9_-]+/sha256:[0-9a-f]+)", completion.result)
        assert handle_match, f'no handle found in: {completion.result[:300]}'
        token = current_overflow_user.set('alice')
        try:
            payload = await PasteOverflowStore().read(handle_match.group(1))
            assert payload.decode() == big
        finally:
            current_overflow_user.reset(token)


class TestMultiTurnContext:
    """Scenario: a second turn rebuilds context from the first turn's history
    (user, assistant, and tool entries all round-trip through the store)."""

    async def test_two_turns_share_one_history(self, terrarium):
        terrarium.user('alice')

        first = terrarium.scenario(
            call_tool('list_jobs'),
            reply('you have no jobs'),
            user='alice',
        )
        await first.run('list my jobs', conversation_id='conv-1')

        second = terrarium.scenario(reply('as I said: none'), user='alice')
        result = await second.run('and now?', conversation_id='conv-1')

        assert result.reply == 'as I said: none'
        from marcel_core.storage.conversation import read_active_segment

        messages = read_active_segment('alice', 'cli')
        assert [m.role for m in messages].count('user') == 2
        assert any(m.role == 'tool' for m in messages), 'turn-1 tool history must persist'
