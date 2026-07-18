"""Persistence scenarios — segment integrity across turns (FEAT-260718-ed6d63)."""

from __future__ import annotations

import json

from odile import call_tool, reply

from marcel_core.toolkit import marcel_tool


class TestStepStoreLedgerAndResume:
    """Scenario: turns are runs — ledgered, resumable, human-readable.

    The StepPersistence capability writes through MarcelStepStore: a
    ``runs.jsonl`` ledger appears in the conversation directory (run
    lifecycle + tool effects — the crash-visibility record), the store
    enumerates the conversation's runs, and ``continue_run`` reconstructs
    a provider-valid history containing the earlier exchange.
    """

    async def test_two_turns_ledger_and_continue(self, terrarium):
        from pydantic_ai_harness.step_persistence import continue_run

        from marcel_core.capabilities.persistence import persistence_store
        from marcel_core.capabilities.persistence.store import _ledger_path

        @marcel_tool('probe.note')
        async def note(params: dict, user_slug: str) -> str:
            return 'noted'

        terrarium.user('alice')
        s1 = terrarium.scenario(
            call_tool('toolkit', id='probe.note', params={}),
            reply('First reply'),
            user='alice',
            channel='cli',
        )
        await s1.run('turn one')
        s2 = terrarium.scenario(reply('Second reply'), user='alice', channel='cli')
        await s2.run('turn two')

        # Ledger: human-readable JSONL with run lifecycle + tool effects.
        ledger_lines = [json.loads(line) for line in _ledger_path('alice', 'cli').read_text().splitlines()]
        kinds = [line.get('kind') for line in ledger_lines if line.get('record') == 'event']
        assert kinds.count('run_started') == 2
        assert kinds.count('run_completed') == 2
        effect_statuses = [line.get('status') for line in ledger_lines if line.get('record') == 'tool_effect']
        assert 'started' in effect_statuses and 'completed' in effect_statuses

        # Store protocol: runs enumerable per conversation; latest resumable.
        store = persistence_store()
        runs = await store.list_runs(conversation_id='alice:cli')
        assert len(runs) == 2
        history = await continue_run(store, run_id=runs[-1].run_id)
        rendered = str(history)
        assert 'turn one' in rendered and 'First reply' in rendered
        assert not await store.list_unresolved_tool_effects(run_id=runs[0].run_id)


class TestToolHistoryPersistsExactlyOnce:
    """Regression: a turn-1 tool entry must not be re-appended by turn 2.

    ``_extract_tool_history`` walks the extraction message list; fed
    ``all_messages()`` it re-extracted the converted history prefix every
    turn, so each turn re-appended every historical tool entry to the
    segment — compounding growth masked only by rotation and sealing
    (STORY-260718-bfb1ac). ``new_messages()`` is the correct delta.
    """

    async def test_no_reappend_on_later_turns(self, terrarium):
        @marcel_tool('probe.echo')
        async def echo(params: dict, user_slug: str) -> str:
            return 'probe-result'

        terrarium.user('alice')
        s1 = terrarium.scenario(
            call_tool('toolkit', id='probe.echo', params={}),
            reply('turn one done'),
            user='alice',
            channel='cli',
        )
        await s1.run('use the probe')

        s2 = terrarium.scenario(reply('turn two done'), user='alice', channel='cli')
        await s2.run('just chat')

        from marcel_core.memory.conversation import read_active_segment

        msgs = read_active_segment('alice', 'cli')
        tool_entries = [m for m in msgs if m.role == 'tool']
        assistant_tool_calls = [m for m in msgs if m.role == 'assistant' and m.tool_calls]
        assert len(tool_entries) == 1, f'expected 1 tool entry after 2 turns, found {len(tool_entries)}'
        assert len(assistant_tool_calls) == 1
