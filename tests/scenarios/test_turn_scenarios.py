"""Kernel scenario suite — the feature's Gherkin scenarios against the real loop.

Each test mirrors one scenario from the FEAT-260706-8d6824 requirements page.
Nothing in `marcel_core.harness` is patched: the production `stream_turn`
runs end to end, with only the LLM replaced by an odile scripted double and
the outside world replaced by terrarium fakes.
"""

from __future__ import annotations

import httpx
import pytest
from odile import call_tool, reply
from pydantic_ai import models as pai_models

from marcel_core.toolkit import marcel_tool


class TestPlainReplyPersists:
    """Scenario: plain scripted reply persists like a real turn."""

    async def test_reply_and_disk_state(self, terrarium):
        terrarium.user('alice')
        scenario = terrarium.scenario(reply('Hello Alice!'), user='alice', channel='cli')
        result = await scenario.run('hi marcel')

        assert result.reply == 'Hello Alice!'
        from marcel_core.memory.conversation import read_active_segment

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


class TestToolCallThroughBus:
    """Scenario: scripted tool call flows through the real event bus."""

    async def test_registered_test_tool_echoes(self, terrarium):
        seen: list[dict] = []

        @marcel_tool('echo.ping')
        async def ping(params: dict, user_slug: str) -> str:
            seen.append(params)
            return f'pong: {params.get("text", "")}'

        scenario = terrarium.scenario(
            call_tool('toolkit', id='echo.ping', params={'text': 'ping'}),
            reply('done'),
        )
        result = await scenario.run('ping the echo tool')

        assert result.reply == 'done'
        assert seen == [{'text': 'ping'}]
        assert [e.tool_name for e in result.tool_calls] == ['toolkit']
        names = [e.NAME for e in result.bus_events]
        assert names.index('tool_call') < names.index('tool_result')
        completion = result.completions[0]
        assert 'pong: ping' in completion.result


class TestPolicyDenies:
    """Scenario: command policy denies through the real handler chain."""

    async def test_self_mod_shell_never_executes(self, terrarium):
        terrarium.user('root', role='admin')
        scenario = terrarium.scenario(
            call_tool('bash', command='cat .env.local'),
            reply('cannot do that'),
            user='root',
        )
        result = await scenario.run('show me the secrets')

        denial = next(c for c in result.completions if c.tool_name == 'bash')
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
            call_tool('bash', command=f'rm -rf {scratch}'),
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

        @marcel_tool('weather.today')
        async def today(params: dict, user_slug: str) -> str:
            async with httpx.AsyncClient() as client:
                response = await client.get('https://api.weather.test/today')
            return response.json()['sky']

        scenario = terrarium.scenario(
            call_tool('toolkit', id='weather.today', params={}),
            reply('it is sunny'),
        )
        result = await scenario.run('weather?')

        assert result.reply == 'it is sunny'
        assert 'sunny' in result.completions[0].result

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


class TestLargeToolResultsOffloadToPastes:
    """Scenario: a large tool result is offloaded to the paste store, and the
    conversation keeps only a preview — the runner's paste path, unmocked."""

    async def test_paste_offload(self, terrarium):
        big = 'x' * 2048  # PASTE_THRESHOLD is 1KB

        @marcel_tool('blob.dump')
        async def dump(params: dict, user_slug: str) -> str:
            return big

        scenario = terrarium.scenario(
            call_tool('toolkit', id='blob.dump', params={}),
            reply('stored'),
            user='alice',
        )
        result = await scenario.run('dump the blob')

        assert result.reply == 'stored'
        pastes = list((terrarium.data_root / 'users' / 'alice' / '.pastes').glob('*'))
        assert pastes, 'a >1KB tool result must be offloaded to the paste store'
        from marcel_core.memory.conversation import read_active_segment

        tool_msgs = [m for m in read_active_segment('alice', 'cli') if m.role == 'tool']
        assert tool_msgs and len(tool_msgs[0].text or '') < len(big), 'segment keeps a preview, not the blob'


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
        from marcel_core.memory.conversation import read_active_segment

        messages = read_active_segment('alice', 'cli')
        assert [m.role for m in messages].count('user') == 2
        assert any(m.role == 'tool' for m in messages), 'turn-1 tool history must persist'
