"""Persistence scenarios — segment integrity across turns (FEAT-260718-ed6d63)."""

from __future__ import annotations

import json
import pathlib

from odile import call_tool, reply


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
        from tests.scenarios import probe_hooks

        probe_hooks.reset()
        probe_hooks.HANDLERS['note'] = lambda text, user_slug: 'noted'
        terrarium.install_connector(pathlib.Path(__file__).resolve().parent / 'probe')

        terrarium.user('alice')
        s1 = terrarium.scenario(
            call_tool('load_capability', id='probe'),
            call_tool('note'),
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


class TestSealingCoexistsWithTheStore:
    """Scenario: idle sealing and the step store share one layout safely.

    The idle summarizer (outer loop) seals segments and writes rolling
    summaries against the same files MarcelStepStore reads and appends —
    one layout, two readers. Sealing mid-conversation must not corrupt
    either: the next turn's context is the summary prefix plus the fresh
    segment, and the store keeps appending to the fresh segment.
    """

    async def test_seal_then_resume_and_append(self, terrarium):
        from datetime import datetime, timezone

        from marcel_core.capabilities.persistence import persistence_store
        from marcel_core.memory.conversation import (
            SegmentSummary,
            read_active_segment,
            save_summary,
            seal_active_segment,
        )

        terrarium.user('alice')
        s1 = terrarium.scenario(reply('About the garden: noted!'), user='alice', channel='cli')
        await s1.run('remember the garden plan')

        # Outer loop seals the segment and records a rolling summary —
        # storage-level, exactly what summarize_active_segment persists
        # (the LLM half is out of scope in a sealed world).
        sealed_id, _meta = seal_active_segment('alice', 'cli')
        now = datetime.now(tz=timezone.utc)
        save_summary(
            'alice',
            'cli',
            SegmentSummary(
                segment_id=sealed_id,
                created_at=now,
                trigger='idle',
                message_count=2,
                time_span_from=now,
                time_span_to=now,
                summary='Alice shared a garden plan.',
            ),
        )

        context = await persistence_store().load_context('alice', 'cli')
        rendered = str(context)
        assert 'Previous conversation summary' in rendered
        assert 'garden plan' in rendered.lower()
        assert 'About the garden: noted!' not in rendered, 'sealed content is summarized, not replayed'

        s2 = terrarium.scenario(reply('Fresh segment reply'), user='alice', channel='cli')
        result = await s2.run('and now?')
        assert result.reply == 'Fresh segment reply'
        fresh = read_active_segment('alice', 'cli')
        assert [(m.role, m.text) for m in fresh] == [
            ('user', 'and now?'),
            ('assistant', 'Fresh segment reply'),
        ]


class TestToolHistoryPersistsExactlyOnce:
    """Regression: a turn-1 tool entry must not be re-appended by turn 2.

    ``_extract_tool_history`` walks the extraction message list; fed
    ``all_messages()`` it re-extracted the converted history prefix every
    turn, so each turn re-appended every historical tool entry to the
    segment — compounding growth masked only by rotation and sealing
    (STORY-260718-bfb1ac). ``new_messages()`` is the correct delta.
    """

    async def test_no_reappend_on_later_turns(self, terrarium):
        from tests.scenarios import probe_hooks

        probe_hooks.reset()
        probe_hooks.HANDLERS['ping'] = lambda text, user_slug: 'probe-result'
        terrarium.install_connector(pathlib.Path(__file__).resolve().parent / 'probe')

        terrarium.user('alice')
        s1 = terrarium.scenario(
            call_tool('load_capability', id='probe'),
            call_tool('ping'),
            reply('turn one done'),
            user='alice',
            channel='cli',
        )
        await s1.run('use the probe')

        from marcel_core.memory.conversation import read_active_segment

        after_one = read_active_segment('alice', 'cli')
        tools_after_one = len([m for m in after_one if m.role == 'tool'])
        calls_after_one = len([m for m in after_one if m.role == 'assistant' and m.tool_calls])
        assert tools_after_one >= 1  # the probe (plus load_capability bookkeeping)

        s2 = terrarium.scenario(reply('turn two done'), user='alice', channel='cli')
        await s2.run('just chat')

        msgs = read_active_segment('alice', 'cli')
        tool_entries = [m for m in msgs if m.role == 'tool']
        assistant_tool_calls = [m for m in msgs if m.role == 'assistant' and m.tool_calls]
        # The invariant: turn two must not re-append turn one's tool history.
        assert len(tool_entries) == tools_after_one, (
            f'expected {tools_after_one} tool entries after 2 turns, found {len(tool_entries)}'
        )
        assert len(assistant_tool_calls) == calls_after_one
