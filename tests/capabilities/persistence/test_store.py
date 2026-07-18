"""Unit tests for MarcelStepStore — protocol read side and edge branches.

The happy write-path is covered end-to-end by the Terrarium scenarios
(tests/scenarios/test_persistence_scenarios.py); these tests pin the
protocol readers and the defensive branches a scenario can't easily reach.
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    RunRecord,
    StepEvent,
    ToolEffectRecord,
)

from marcel_core.capabilities.persistence.store import (
    MarcelStepStore,
    _ledger_path,
    conversation_key,
)
from marcel_core.storage import _root


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
    return MarcelStepStore()


CONV = conversation_key('alice', 'cli')


async def _register(store: MarcelStepStore, run_id: str = 'run-1') -> None:
    await store.register_run(RunRecord(run_id=run_id, conversation_id=CONV))


class TestConversationRouting:
    async def test_non_conversation_records_are_ignored(self, store, tmp_path):
        await store.register_run(RunRecord(run_id='job-1', conversation_id=None))
        await store.append_event(StepEvent(run_id='job-1', kind='run_started', step_index=0))
        await store.record_tool_effect(ToolEffectRecord(tool_call_id='t1', tool_name='x', run_id='job-1', status='started'))
        assert await store.get_run(run_id='job-1') is None
        assert await store.list_events(run_id='job-1') == []
        assert await store.latest_snapshot(run_id='job-1') is None
        assert not list(tmp_path.rglob('runs.jsonl'))

    async def test_malformed_conversation_ids_are_ignored(self, store):
        for bad in ('', 'no-separator', ':cli', 'alice:'):
            await store.register_run(RunRecord(run_id=f'r-{bad or "empty"}', conversation_id=bad))
        assert await store.list_runs(conversation_id='alice:cli') == []


class TestProtocolReadSide:
    async def test_get_run_and_list_events_round_trip(self, store):
        await _register(store)
        await store.append_event(StepEvent(run_id='run-1', kind='run_started', step_index=0, conversation_id=CONV))
        await store.append_event(StepEvent(run_id='run-1', kind='model_request_started', step_index=1, conversation_id=CONV))

        run = await store.get_run(run_id='run-1')
        assert run is not None and run.conversation_id == CONV
        events = await store.list_events(run_id='run-1')
        assert [e.kind for e in events] == ['run_started', 'model_request_started']

    async def test_tool_effect_latest_wins_and_unresolved(self, store):
        await _register(store)
        await store.record_tool_effect(ToolEffectRecord(tool_call_id='t1', tool_name='bash', run_id='run-1', status='started'))
        await store.record_tool_effect(
            ToolEffectRecord(tool_call_id='t1', tool_name='bash', run_id='run-1', status='completed')
        )
        await store.record_tool_effect(ToolEffectRecord(tool_call_id='t2', tool_name='web', run_id='run-1', status='started'))

        effect = await store.get_tool_effect(run_id='run-1', tool_call_id='t1')
        assert effect is not None and effect.status == 'completed'
        unresolved = await store.list_unresolved_tool_effects(run_id='run-1')
        assert [e.tool_call_id for e in unresolved] == ['t2']

    async def test_list_runs_filters_by_parent(self, store):
        await _register(store, 'parent-1')
        await store.register_run(RunRecord(run_id='child-1', conversation_id=CONV, parent_run_id='parent-1'))
        children = await store.list_runs(conversation_id=CONV, parent_run_id='parent-1')
        assert [r.run_id for r in children] == ['child-1']

    async def test_malformed_ledger_line_is_skipped(self, store):
        await _register(store)
        path = _ledger_path('alice', 'cli')
        with path.open('a', encoding='utf-8') as f:
            f.write('{torn write\n')
        runs = await store.list_runs(conversation_id=CONV)
        assert len(runs) == 1


class TestSnapshotDelta:
    async def test_shrunk_snapshot_is_refused(self, store, caplog):
        await _register(store)
        store._served[CONV] = 5
        await store.save_snapshot(
            ContinuableSnapshot(run_id='run-1', step_index=1, messages=[], conversation_id=CONV)
        )
        assert store._pending.get('run-1') is None
        assert any('skipping delta' in r.message for r in caplog.records)

    async def test_run_failed_drops_buffered_delta(self, store):
        await _register(store)
        store._served[CONV] = 0
        messages = [
            ModelRequest(parts=[UserPromptPart(content='hi')]),
            ModelResponse(parts=[TextPart(content='ok')]),
        ]
        await store.save_snapshot(
            ContinuableSnapshot(run_id='run-1', step_index=1, messages=messages, conversation_id=CONV)
        )
        await store.append_event(StepEvent(run_id='run-1', kind='run_failed', step_index=1, conversation_id=CONV))
        assert 'run-1' not in store._pending

    async def test_reset_clears_turn_state(self, store):
        store._served[CONV] = 3
        store._pending['run-1'] = []
        store._run_conv['run-1'] = CONV
        store.reset()
        assert not store._served and not store._pending and not store._run_conv
