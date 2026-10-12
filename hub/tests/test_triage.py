"""When the brain is woken (S-06.5): the wake table, the decision log, and the hub following it."""

import json
import logging
import re
from collections.abc import Iterator
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from test_channel_fakes import ChannelHub, channel_hub

from marcel_hub import triage
from marcel_hub.channel.outbox import conversation_target, task_target
from marcel_hub.models import TaskState
from marcel_hub.runner_client import map_session
from marcel_hub.triage import BrainTurn, Direct, Event, Kind, RulesTriage, Store

ARCHITECTURE = Path(__file__).resolve().parents[2] / 'plan' / '02-architecture.md'


# The table ----------------------------------------------------------------------------------------

# Each row of "When the brain is woken" in plan/02-architecture.md § Token economy, word for word,
# with the events it covers and the decision the policy must make for each.
ROWS: list[tuple[str, list[tuple[Event, type, str | None]]]] = [
    (
        '| User message in main chat | yes | brain |',
        [(Event(Kind.main_message, 'a', 'main', text='Book the dentist.'), BrainTurn, 'main')],
    ),
    (
        '| User message in a side thread | yes | that side fork |',
        [(Event(Kind.side_message, 'a', 'side_1', text='And on Friday?'), BrainTurn, 'side_1')],
    ),
    (
        '| Steer typed in a thread view, or "reply in thread" on a milestone card | **no** | hub '
        'forwards it straight to that task (explicit routing chosen in the UI) |',
        [(Event(Kind.thread_message, 'a', 'thr', 'tsk_1', 'Use the NAS.'), Direct, 'task:tsk_1')],
    ),
    (
        '| Approve / deny tap | **no** | hub → channel |',
        [(Event(Kind.approval_answer, task_id='tsk_1'), Direct, 'task:tsk_1')],
    ),
    (
        '| Worker progress | **no** | hub stores it; quest view and Quests tab only |',
        [(Event(Kind.worker_progress, 'a', task_id='tsk_1'), Store, None)],
    ),
    (
        "| Worker done | **no** | hub posts a milestone from the worker's own summary |",
        [(Event(Kind.worker_done, 'a', 'main', 'tsk_1'), Store, None)],
    ),
    (
        '| Artifact published or updated; project board rebuilt; task moved to another project '
        '| **no** | hub |',
        [(Event(Kind.hub_update, task_id='tsk_1'), Store, None)],
    ),
    (
        "| Worker failed, silent, or asked a question the user can't answer with a button | yes "
        '| brain decides: retry, re-route, or ask |',
        [
            (Event(Kind.worker_failed, 'a', 'main', 'tsk_1'), BrainTurn, 'main'),
            (Event(Kind.worker_silent, 'a', 'main', 'tsk_1'), BrainTurn, 'main'),
            (Event(Kind.worker_question, 'a', 'main', 'tsk_1'), BrainTurn, 'main'),
        ],
    ),
    (
        "| Digest due | yes | brain writes it from the hub's facts bundle |",
        [(Event(Kind.digest_due, conversation_id='main'), BrainTurn, 'main')],
    ),
    (
        '| "status?"-style message | yes in v1 | brain via `tasks_list`, with a cached system '
        'prompt |',
        [
            (Event(Kind.main_message, 'a', 'main', text=said), BrainTurn, 'main')
            for said in ('status?', "How's it going?", 'Any news', 'Is it done yet?')
        ],
    ),
]


def plan_rows() -> list[str]:
    text = ARCHITECTURE.read_text()
    table = text[text.index('**When the brain is woken (S-06.5):**') :].split('\n\n')[1]
    return [line for line in table.splitlines()[2:] if line.startswith('|')]


def test_the_test_quotes_every_row_of_the_plans_table() -> None:
    assert [row for row, _ in ROWS] == plan_rows()


@pytest.mark.parametrize(('row', 'cases'), ROWS, ids=[f'row{i + 1}' for i in range(len(ROWS))])
def test_each_row_of_the_wake_table(row: str, cases: list[tuple[Event, type, str | None]]) -> None:
    for event, decision_type, target in cases:
        decision = triage.wake_policy(event)
        assert isinstance(decision, decision_type), (row, event, decision)
        if isinstance(decision, BrainTurn):
            assert decision.conversation_id == target
        elif isinstance(decision, Direct):
            assert decision.target == target
        assert decision.reason and decision.rule


def test_the_status_row_is_counted_on_its_own_and_ordinary_messages_are_not() -> None:
    def rule(text: str) -> str:
        return triage.wake_policy(Event(Kind.main_message, conversation_id='m', text=text)).rule

    for said in ('status?', 'Status', "what's the status", 'where are we?', 'any updates'):
        assert rule(said) == 'status_question', said
    for said in ('Book the dentist.', 'statuses of my PRs and then merge the green ones please.'):
        assert rule(said) == 'main_message', said
    assert rule('status? ' + 'x' * 80) == 'main_message'  # a long message is a real request


@pytest.mark.parametrize('kind', [Kind.handback, Kind.schedule_fired, Kind.rollover])
def test_events_addressed_to_the_brain_wake_it(kind: Kind) -> None:
    decision = triage.wake_policy(Event(kind, conversation_id='main'))
    assert isinstance(decision, BrainTurn) and decision.rule == kind.value


def test_the_policys_task_target_is_the_outboxs() -> None:
    assert triage.task_target('tsk_1') == task_target('tsk_1')


# The decision log ---------------------------------------------------------------------------------


def decisions(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    """The decision log as S-16.2 reads it: the JSON after the last ` | ` of each triage line."""
    return [
        json.loads(r.getMessage().rsplit(' | ', 1)[1])
        for r in caplog.records
        if r.name == 'marcel_hub.triage'
    ]


def test_every_decision_is_logged_in_plain_words_and_as_json(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger='marcel_hub.triage')
    triage.record(RulesTriage(), Event(Kind.worker_done, 'agt_1', 'cnv_1', 'tsk_1'))
    [record] = [r for r in caplog.records if r.name == 'marcel_hub.triage']
    assert record.getMessage().startswith('Worker done is stored, no brain turn: A worker finished')
    [line] = decisions(caplog)
    assert datetime.fromisoformat(line.pop('at')).tzinfo is not None
    assert [line] == [
        {
            'event': 'worker_done',
            'decision': 'store',
            'brain_turn': False,
            'target': None,
            'reason': "A worker finished: the hub posts a milestone from the worker's own summary.",
            'rule': 'worker_done',
            'agent_id': 'agt_1',
            'conversation_id': 'cnv_1',
            'task_id': 'tsk_1',
            'by': 'rules',
        }
    ]


# The hub follows the policy -----------------------------------------------------------------------


@pytest.fixture
def hub(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> Iterator[ChannelHub]:
    caplog.set_level(logging.INFO, logger='marcel_hub.triage')
    with channel_hub(tmp_path) as hub:
        yield hub


def system_events(hub: ChannelHub) -> list[dict[str, Any]]:
    pending = hub.gateway.outbox.pending(conversation_target(hub.main_id))
    return [f for f in pending if f['type'] == 'system_event']


def assert_worker_done_wakes_no_brain(hub: ChannelHub, caplog: pytest.LogCaptureFixture) -> None:
    task_id, sid = hub.task()
    hub.brain()
    with hub.connect() as ch:
        ch.hello(sid)
        assert ch.call('report', kind='done', summary='Five volumes listed.')['type'] == 'call_ack'
    [done] = [d for d in decisions(caplog) if d['event'] == 'worker_done']
    assert done['brain_turn'] is False, 'a finished worker must not wake the brain'
    assert done['task_id'] == task_id
    assert system_events(hub) == []
    [milestone] = [m for m in hub.messages() if m.kind.value == 'milestone']
    assert milestone.body_json['milestone']['summary'] == 'Five volumes listed.'


def test_worker_done_does_not_wake_the_brain(
    hub: ChannelHub, caplog: pytest.LogCaptureFixture
) -> None:
    assert_worker_done_wakes_no_brain(hub, caplog)


def test_the_done_test_fails_when_the_policy_always_wakes_the_brain(
    hub: ChannelHub, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def always_wake(event: Event) -> BrainTurn:
        return BrainTurn(event.conversation_id, 'Always.', 'always')

    monkeypatch.setattr(triage, 'wake_policy', always_wake)
    with pytest.raises(AssertionError, match='must not wake the brain'):
        assert_worker_done_wakes_no_brain(hub, caplog)


def test_a_failed_worker_wakes_the_brain(hub: ChannelHub, caplog: pytest.LogCaptureFixture) -> None:
    task_id, sid = hub.task(title='Back up the photos')
    brain = hub.brain()
    with hub.connect() as ch:
        ch.hello(sid)
        ch.call('report', kind='failed', summary='The NAS is offline.')
    with hub.connect() as ch:
        ch.hello(brain)
        event = ch.deliver()  # checked against channel.schema.json
    assert event['kind'] == 'task_failed' and event['task_id'] == task_id
    assert event['text'].startswith('The task "Back up the photos" failed: The worker reports')
    assert event['data']['state'] == 'failed'
    failed = [d for d in decisions(caplog) if d['event'] == 'worker_failed']
    assert [d['brain_turn'] for d in failed] == [True]
    # The progress kinds are decided too, and never wake the brain.
    with hub.connect() as ch:
        other, sid2 = hub.task()
        ch.hello(sid2)
        ch.call('report', 'c_s', kind='started', text='Looking.')
        ch.call('report', 'c_p', kind='progress', text='Half way.')
    progress = [d for d in decisions(caplog) if d['event'] == 'worker_progress']
    assert len(progress) == 2 and not any(d['brain_turn'] for d in progress)
    assert {d['task_id'] for d in progress} == {other}


def test_a_silent_worker_and_a_question_wake_the_brain(hub: ChannelHub) -> None:
    silent, _ = hub.task()
    asking, _ = hub.task()
    approval, _ = hub.task()
    portal: Any = hub.client.portal
    portal.call(hub.registry.transition, silent, TaskState.silent, 'No activity for 20 minutes')
    question = map_session({'state': 'blocked', 'needs': 'Which NAS share?'})
    move = partial(hub.registry.transition, asking, question.state, question.reason)
    portal.call(partial(move, **question.fields))
    prompt = map_session({'state': 'blocked', 'waiting_for': 'permission prompt'})
    move = partial(hub.registry.transition, approval, prompt.state, prompt.reason)
    portal.call(partial(move, **prompt.fields))
    got = {(e['kind'], e['task_id']) for e in system_events(hub)}
    assert got == {('task_silent', silent), ('task_question', asking)}  # a button answers a prompt
    [ask] = [e for e in system_events(hub) if e['kind'] == 'task_question']
    assert 'Which NAS share?' in ask['text']


def test_a_status_question_is_logged_as_one(
    hub: ChannelHub, caplog: pytest.LogCaptureFixture
) -> None:
    hub.brain()
    for text in ('status?', 'Book the dentist for Friday.'):
        res = hub.client.post(f'/api/conversations/{hub.main_id}/messages', json={'text': text})
        assert res.status_code == 201
    rules = [d['rule'] for d in decisions(caplog) if d['event'] == 'main_message']
    assert rules == ['status_question', 'main_message']
    frames = hub.gateway.outbox.pending(conversation_target(hub.main_id))
    assert [f['text'] for f in frames] == ['status?', 'Book the dentist for Friday.']


def test_a_thread_message_and_an_approval_go_straight_to_the_task(
    hub: ChannelHub, caplog: pytest.LogCaptureFixture
) -> None:
    from test_channel_fakes import permission

    task_id, sid = hub.task()
    thread_id, _ = hub.side(task_id=task_id)
    hub.brain()
    hub.client.post(f'/api/conversations/{thread_id}/messages', json={'text': 'Use the NAS.'})
    with hub.connect() as ch:
        ch.hello(sid)
        ch.send(**permission())
        ch.until_pong()
    [card] = hub.approvals()
    assert hub.answer(card['id'], 'approve').status_code == 200
    direct = [d for d in decisions(caplog) if d['decision'] == 'direct']
    assert [(d['event'], d['target']) for d in direct] == [
        ('thread_message', f'task:{task_id}'),
        ('approval_answer', f'task:{task_id}'),
    ]
    assert system_events(hub) == []
    assert hub.gateway.outbox.pending(conversation_target(hub.main_id)) == []


def test_an_artifact_change_is_decided_and_never_wakes_the_brain(
    hub: ChannelHub, caplog: pytest.LogCaptureFixture
) -> None:
    hub.gateway.on_app_event('usr_owner', 'artifact.updated', {'id': 'art_1', 'task_id': 'tsk_1'})
    [update] = [d for d in decisions(caplog) if d['event'] == 'hub_update']
    assert update['decision'] == 'store' and update['task_id'] == 'tsk_1'


def test_a_system_event_the_triage_does_not_wake_for_is_not_queued(
    hub: ChannelHub, caplog: pytest.LogCaptureFixture
) -> None:
    class Never:
        name = 'never'

        def decide(self, event: Event) -> Store:
            return Store('Not worth it.', 'never')

    target = conversation_target(hub.main_id)
    frame = {'type': 'system_event', 'kind': 'digest_due', 'text': 'The digest is due.'}
    hub.gateway.outbox.triage = Never()
    assert hub.gateway.outbox.put(target, frame) == 0
    assert hub.gateway.outbox.pending(target) == []
    assert [d['by'] for d in decisions(caplog)] == ['never']
    hub.gateway.outbox.triage = RulesTriage()
    assert hub.gateway.outbox.put(target, frame) == 1
    [logged] = [d for d in decisions(caplog) if d['by'] == 'rules']
    assert logged['event'] == 'digest_due' and logged['brain_turn'] is True


def test_a_handback_is_decided_by_the_triage(
    hub: ChannelHub, caplog: pytest.LogCaptureFixture
) -> None:
    task_id, _ = hub.task(TaskState.done)
    hub.brain()
    res = hub.client.post(f'/api/tasks/{task_id}/handback', json={'note': 'Try the NAS.'})
    assert res.status_code == 202, res.text
    [handback] = [d for d in decisions(caplog) if d['event'] == 'handback']
    assert handback['brain_turn'] is True and handback['task_id'] == task_id
    assert re.search(r'Handback wakes the brain', caplog.text)


def test_decisions_are_kept_in_the_hubs_data_folder(hub: ChannelHub, tmp_path: Path) -> None:
    hub.brain()
    hub.client.post(f'/api/conversations/{hub.main_id}/messages', json={'text': 'status?'})
    lines = (tmp_path / 'triage.log').read_text().splitlines()
    kept = [json.loads(line.rsplit(' | ', 1)[1]) for line in lines]
    assert [(d['event'], d['rule']) for d in kept][-1] == ('main_message', 'status_question')
    assert lines[-1].startswith('Main message wakes the brain: A "status?" question')


def test_a_new_hub_replaces_the_last_ones_log_file(tmp_path: Path) -> None:
    triage.keep_log(tmp_path / 'one' / 'triage.log')
    triage.keep_log(tmp_path / 'two' / 'triage.log')
    triage.record(RulesTriage(), Event(Kind.worker_progress, task_id='tsk_1'))
    assert (tmp_path / 'one' / 'triage.log').read_text() == ''
    assert 'worker_progress' in (tmp_path / 'two' / 'triage.log').read_text()
