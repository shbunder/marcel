"""The Terrarium binding — sealed world, real turn loop, recorded evidence.

These tests cover ``marcel_testing`` itself (the harness must not be the
untested part — NFR4). Full kernel scenario suites live in
``tests/scenarios/``.
"""

from __future__ import annotations

import pytest
from odile import ScriptError, call_tool, reply
from pydantic_ai.models.test import TestModel

from marcel_core.storage.users import get_user_role
from marcel_testing import Terrarium


class TestSealedWorld:
    def test_data_root_redirected_and_restored(self, tmp_path):
        from marcel_core.storage import _root

        original = _root._DATA_ROOT
        with Terrarium(tmp_path / 'world') as terrarium:
            assert _root._DATA_ROOT == terrarium.data_root
            assert (tmp_path / 'world').is_dir()
        assert _root._DATA_ROOT == original

    def test_registries_snapshotted_and_restored(self, tmp_path):
        import marcel_core.harness.core_handlers as core_handlers_mod
        import marcel_core.plugin.channels as channels_mod
        from marcel_core.plugin.extension import extension_registry

        channels_before = dict(channels_mod._registry)
        handlers_before = list(extension_registry().handlers)
        policy_before = core_handlers_mod._POLICY
        with Terrarium(tmp_path / 'world') as terrarium:
            terrarium.resolve_approvals('allow_once', channel='cli')
            assert 'cli' in channels_mod._registry
            assert len(extension_registry().handlers) > len(handlers_before)
            core_handlers_mod.command_policy()  # instantiate the per-world policy
        assert dict(channels_mod._registry) == channels_before
        assert list(extension_registry().handlers) == handlers_before
        assert core_handlers_mod._POLICY is policy_before  # restored slot

    def test_two_terrariums_never_share_state(self, tmp_path):
        with Terrarium(tmp_path / 'a') as first:
            first.user('alice', role='admin')
            assert get_user_role('alice') == 'admin'
        with Terrarium(tmp_path / 'b'):
            assert get_user_role('alice') == 'user', 'default role — alice must not exist in world b'

    def test_methods_require_entered(self, tmp_path):
        terrarium = Terrarium(tmp_path / 'world')
        with pytest.raises(RuntimeError, match='not active'):
            terrarium.user('alice')


class TestWorldBuilding:
    def test_user_seeding_role_profile_memories(self, terrarium):
        terrarium.user(
            'bob',
            role='admin',
            profile='Bob likes espresso.',
            memories={
                'coffee': '---\nname: coffee\ndescription: how bob takes his coffee\n---\nDouble shot, no sugar.'
            },
        )
        from marcel_core.storage.memory import scan_memory_headers
        from marcel_core.storage.users import load_user_profile

        assert get_user_role('bob') == 'admin'
        assert 'espresso' in load_user_profile('bob')
        assert any(h.name == 'coffee' for h in scan_memory_headers('bob'))

    def test_seed_history_lands_in_active_segment(self, terrarium):
        terrarium.user('alice')
        terrarium.seed_history('alice', 'cli', [('user', 'earlier question'), ('assistant', 'earlier answer')])
        from marcel_core.storage.conversation import read_active_segment

        texts = [m.text for m in read_active_segment('alice', 'cli')]
        assert texts == ['earlier question', 'earlier answer']

    async def test_fake_api_serves_and_seals(self, terrarium):
        import httpx

        terrarium.fake_api('https://api.example.test').returns('/ping', json={'ok': True})
        async with httpx.AsyncClient() as client:
            assert (await client.get('https://api.example.test/ping')).json() == {'ok': True}
            with pytest.raises(Exception, match='mock'):
                await client.get('https://unfaked.example.test/x')


class TestScenarioTurns:
    async def test_plain_reply_persists_like_a_real_turn(self, terrarium):
        scenario = terrarium.scenario(reply('Hello Alice!'), user='alice')
        result = await scenario.run('hi marcel')

        assert result.reply == 'Hello Alice!'
        assert result.is_error is False
        from marcel_core.storage.conversation import read_active_segment

        roles = [(m.role, m.text) for m in read_active_segment('alice', 'cli')]
        assert ('user', 'hi marcel') in roles
        assert ('assistant', 'Hello Alice!') in roles

    async def test_tool_call_flows_through_the_bus(self, terrarium):
        scenario = terrarium.scenario(call_tool('list_jobs'), reply('no jobs yet'))
        result = await scenario.run('any jobs?')

        assert result.reply == 'no jobs yet'
        assert [e.tool_name for e in result.tool_calls] == ['list_jobs']
        assert [e.tool_name for e in result.tool_results] == ['list_jobs']
        bus_names = [e.NAME for e in result.bus_events]
        assert bus_names.index('tool_call') < bus_names.index('tool_result')
        assert {'session_start', 'input', 'before_agent_start', 'before_provider_request', 'agent_end'} <= set(
            bus_names
        )

    async def test_completions_carry_tool_output(self, terrarium):
        scenario = terrarium.scenario(call_tool('list_jobs'), reply('done'))
        result = await scenario.run('jobs?')
        assert [c.tool_name for c in result.completions] == ['list_jobs']
        assert result.finished.type == 'run_finished'

    async def test_unplayed_script_steps_fail_the_scenario(self, terrarium):
        scenario = terrarium.scenario(reply('first'), reply('never used'))
        with pytest.raises(ScriptError, match='never played'):
            await scenario.run('hi')

    async def test_check_script_can_be_disabled(self, terrarium):
        scenario = terrarium.scenario(reply('first'), reply('spare step'))
        result = await scenario.run('hi', check_script=False)
        assert result.reply == 'first'

    async def test_explicit_model_instance(self, terrarium):
        scenario = terrarium.scenario(model=TestModel(custom_output_text='from test model', call_tools=[]))
        result = await scenario.run('hi')
        assert result.reply == 'from test model'

    def test_steps_and_model_are_mutually_exclusive(self, terrarium):
        with pytest.raises(ValueError, match='not both'):
            terrarium.scenario(reply('x'), model=TestModel())

    async def test_existing_user_is_not_reseeded(self, terrarium):
        terrarium.user('alice', role='admin')
        scenario = terrarium.scenario(reply('hi'), user='alice')
        await scenario.run('hello')
        assert get_user_role('alice') == 'admin', 'scenario() must not downgrade a seeded user'


class TestPolicyDeny:
    async def test_self_mod_shell_command_is_denied_end_to_end(self, terrarium):
        terrarium.user('alice', role='admin')
        scenario = terrarium.scenario(
            call_tool('run_command', command='cat .env.local'),
            reply('understood'),
            user='alice',
        )
        result = await scenario.run('read the env file')

        assert result.reply == 'understood'
        denial = next(c for c in result.completions if c.tool_name == 'run_command')
        assert 'Blocked by command policy' in denial.result
        assert result.tool_results == [], 'a denied tool must never produce a tool_result event'


class TestApprovals:
    def _scratch_scenario(self, terrarium):
        scratch = terrarium.data_root / 'scratch'
        scratch.mkdir()
        terrarium.user('alice', role='admin')
        scenario = terrarium.scenario(
            call_tool('run_command', command=f'rm -rf {scratch}'),
            reply('cleaned up'),
            user='alice',
        )
        return scratch, scenario

    async def test_allow_once_runs_the_command(self, terrarium):
        scratch, scenario = self._scratch_scenario(terrarium)
        requests = terrarium.resolve_approvals('allow_once', channel='cli')
        result = await scenario.run('clean the scratch dir')

        assert result.reply == 'cleaned up'
        assert not scratch.exists(), 'approved command must actually run'
        assert len(requests) == 1
        assert 'rm -rf' in requests[0]['summary']

    async def test_deny_keeps_the_command_from_running(self, terrarium):
        scratch, scenario = self._scratch_scenario(terrarium)
        terrarium.resolve_approvals('deny', channel='cli')
        result = await scenario.run('clean the scratch dir')

        assert scratch.exists(), 'denied command must never run'
        denial = next(c for c in result.completions if c.tool_name == 'run_command')
        assert 'declined' in denial.result

    async def test_expire_denies_and_queues_for_later(self, terrarium):
        scratch, scenario = self._scratch_scenario(terrarium)
        terrarium.resolve_approvals('expire', channel='cli')
        result = await scenario.run('clean the scratch dir')

        assert scratch.exists()
        denial = next(c for c in result.completions if c.tool_name == 'run_command')
        assert 'queued for later' in denial.result
        queue_dir = terrarium.data_root / 'approvals' / 'queue'
        assert queue_dir.is_dir() and list(queue_dir.glob('*.json')), 'expired ask must be queued on disk'

    async def test_allow_always_amends_the_policy(self, terrarium):
        scratch, scenario = self._scratch_scenario(terrarium)
        requests = terrarium.resolve_approvals('allow_always', channel='cli')
        await scenario.run('clean the scratch dir')
        assert len(requests) == 1

        scratch.mkdir()
        rerun = terrarium.scenario(
            call_tool('run_command', command=f'rm -rf {scratch}'),
            reply('cleaned again'),
            user='alice',
        )
        result = await rerun.run('again please')
        assert not scratch.exists()
        assert len(requests) == 1, 'allow-always must not re-ask for the identical command'
        assert result.reply == 'cleaned again'

    def test_unknown_policy_rejected(self, terrarium):
        with pytest.raises(ValueError, match='unknown approval policy'):
            terrarium.resolve_approvals('maybe')

    async def test_resolver_gives_up_after_bounded_spins(self, terrarium):
        import asyncio

        from marcel_testing.terrarium import _ApprovalChannel

        channel = _ApprovalChannel(name='cli', policy='allow_once')
        delivered = await channel.send_approval_request({'id': 'never-registered', 'summary': 's'})
        assert delivered is True
        for _ in range(105):  # let the bounded resolver task exhaust its spins
            await asyncio.sleep(0)
        assert channel.requests[0]['id'] == 'never-registered'


class TestInstallConnector:
    """The connector seam — scenarios declare which parks exist (FEAT-260718-c232d9)."""

    def _park(self, tmp_path):
        park = tmp_path / 'clockpark'
        park.mkdir()
        (park / 'connector.yaml').write_text(
            'name: clockpark\ndescription: Clock\n'
            'server: {transport: inprocess, module: server.py}\n'
            'auth: {mode: none, per_user: false}\n'
        )
        (park / 'server.py').write_text(
            'from fastmcp import FastMCP\n'
            'def build(user_slug):\n'
            "    mcp = FastMCP('clockpark')\n"
            '    @mcp.tool\n'
            '    def now() -> str:\n'
            '        """The time."""\n'
            "        return 'noon for ' + user_slug\n"
            '    return mcp\n'
        )
        return park

    def test_installed_park_is_discovered(self, tmp_path):
        from marcel_core.connectors.loader import load_connectors

        with Terrarium(tmp_path / 'data') as t:
            assert load_connectors('alice') == []  # sealed world: no habitats by default
            t.install_connector(self._park(tmp_path))
            assert [d.name for d in load_connectors('alice')] == ['clockpark']

    def test_install_is_idempotent(self, tmp_path):
        from marcel_core.connectors.loader import load_connectors

        park = self._park(tmp_path)
        with Terrarium(tmp_path / 'data') as t:
            t.install_connector(park)
            t.install_connector(park)
            assert len(load_connectors('alice')) == 1

    def test_zoo_dir_restored_on_exit(self, tmp_path):
        from marcel_core.config import settings

        before = settings.marcel_zoo_dir
        with Terrarium(tmp_path / 'data') as t:
            t.install_connector(self._park(tmp_path))
            assert settings.marcel_zoo_dir != before
        assert settings.marcel_zoo_dir == before

    def test_requires_entered(self, tmp_path):
        t = Terrarium(tmp_path / 'data')
        with pytest.raises(RuntimeError):
            t.install_connector(self._park(tmp_path))
