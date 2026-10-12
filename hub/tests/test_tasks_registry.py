"""Spawning, runner and transcript events, and what the app is told."""

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session
from test_tasks_fakes import (  # noqa: F401  (fixtures)
    AGENT,
    FakeRunner,
    RecordingPublisher,
    outbox_fixture,
    publisher_fixture,
    put_task,
    registry_fixture,
    run,
    runner_fixture,
    validator,
)

from marcel_hub.models import Approval, Artifact, ArtifactKind, TaskEventType, TaskState
from marcel_hub.tasks.registry import TaskNotFound, TaskRegistry

TASK_EVENT = validator('app-api.yaml#/components/schemas/TaskEvent')
TASK_UPDATED = validator('events.schema.json#/$defs/TaskUpdated')
TASK_EVENT_APPENDED = validator('events.schema.json#/$defs/TaskEventAppended')


def test_spawn_starts_a_session_and_records_the_prompt(
    registry: TaskRegistry, runner: FakeRunner
) -> None:
    task = run(registry.spawn(AGENT, 'Check disk usage', prompt='Report the five largest.'))
    assert task.state == TaskState.starting
    assert task.session_id == f'ses-{task.id}'
    state, note = registry.events(task.id)[0]
    assert state.data_json == {'to': 'starting', 'reason': 'Spawn accepted'}
    assert note.data_json == {'author': 'marcel', 'text': 'Report the five largest.'}
    for event in (state, note):
        TASK_EVENT.validate(registry.event_to_api(event))


@pytest.mark.parametrize('down', [True, False])
def test_a_spawn_that_cannot_start_fails_with_the_reason(
    registry: TaskRegistry, runner: FakeRunner, down: bool
) -> None:
    runner.down = down
    runner.fail_with = None if down else 'The session exited at once.'
    task = run(registry.spawn(AGENT, 'Broken', prompt='x'))
    assert task.state == TaskState.failed
    reasons = [e.data_json['reason'] for e in registry.events(task.id)[0] if e.type == 'state']
    assert reasons[0] == 'Spawn accepted'
    assert ('not answering' if down else 'exited at once') in reasons[1]


def test_a_failed_task_can_be_retried(registry: TaskRegistry, engine: Engine) -> None:
    task_id = put_task(engine, TaskState.failed)
    task = run(registry.transition(task_id, TaskState.starting, 'Marcel retries'))
    assert task.state == TaskState.starting


def test_repeated_runner_reports_write_one_event(registry: TaskRegistry, engine: Engine) -> None:
    task_id = put_task(engine, TaskState.starting)
    for _ in range(3):
        run(registry.report_state(task_id, TaskState.working, 'The runner reports working'))
    assert len(registry.events(task_id)[0]) == 1


def test_needs_you_keeps_what_it_waits_for_until_it_moves_on(
    registry: TaskRegistry, engine: Engine
) -> None:
    task_id = put_task(engine, TaskState.working)
    run(registry.report_state(task_id, TaskState.needs_you, 'blocked', waiting_for='A login.'))
    assert registry.get(task_id).waiting_for == 'A login.'
    run(registry.report_state(task_id, TaskState.working, 'answered'))
    assert registry.get(task_id).waiting_for is None


def test_transcript_events_are_stored_in_order(registry: TaskRegistry, engine: Engine) -> None:
    task_id = put_task(engine, TaskState.working)
    base = {'session_id': 'ses-1', 'offset': 10, 'at': '2026-10-04T18:06:39Z'}
    stored = [
        registry.append_transcript(task_id, {**base, **event})
        for event in (
            {'type': 'text', 'data': {'role': 'assistant', 'text': 'Looking at the volumes.'}},
            {'type': 'thinking', 'data': {'text': 'hmm'}},
            {
                'type': 'tool_call',
                'data': {
                    'tool_use_id': 'toolu_1',
                    'tool': 'Bash',
                    'summary': 'docker system df -v',
                },
            },  # fmt: skip
            {'type': 'turn_end', 'data': {}},
        )
    ]
    assert stored[1] is None and stored[3] is None
    events = registry.events(task_id)[0]
    assert [(e.seq, e.type) for e in events] == [
        (1, TaskEventType.text),
        (2, TaskEventType.tool_call),
    ]
    for event in events:
        TASK_EVENT.validate(registry.event_to_api(event))
    assert registry.to_api(registry.get(task_id))['last_activity_at'] == '2026-10-04T18:06:39Z'


def test_hub_events_are_stored(registry: TaskRegistry, engine: Engine) -> None:
    task_id = put_task(engine, TaskState.working)
    event = registry.append_event(task_id, TaskEventType.progress, {'text': 'Halfway.'})
    TASK_EVENT.validate(registry.event_to_api(event))
    with pytest.raises(TaskNotFound):
        registry.append_event('tsk_missing', TaskEventType.progress, {'text': 'x'})
    with pytest.raises(TaskNotFound):
        registry.append_transcript('tsk_missing', {'type': 'text', 'data': {'text': 'x'}})


def test_the_app_hears_about_every_change(
    registry: TaskRegistry, publisher: RecordingPublisher, engine: Engine
) -> None:
    task_id = put_task(engine, TaskState.starting)
    run(registry.transition(task_id, TaskState.working, 'The runner reports working'))
    kinds = [kind for kind, _ in publisher.sent]
    assert kinds == ['task.event', 'task.updated']
    (_, event), (_, task) = publisher.sent
    TASK_EVENT_APPENDED.validate({'id': 1, 'type': 'task.event', 'at': event['at'], 'data': event})
    TASK_UPDATED.validate({'id': 2, 'type': 'task.updated', 'at': task['updated_at'], 'data': task})
    assert task['state'] == 'working'


def test_the_task_counts_open_approvals_and_artifacts(
    registry: TaskRegistry, engine: Engine
) -> None:
    task_id = put_task(engine, TaskState.needs_you)
    with Session(engine) as s:
        s.add(Approval(id='apr_1', task_id=task_id, tool='Bash', summary='Run it'))
        s.add(Approval(id='apr_2', task_id=task_id, tool='Bash', summary='x', state='approved'))
        s.add(Artifact(task_id=task_id, kind=ArtifactKind.pr, title='Fix'))
        s.commit()
    body = registry.to_api(registry.get(task_id))
    assert (body['approval_ids'], body['artifact_count']) == (['apr_1'], 1)


def test_unknown_tasks_are_not_found(registry: TaskRegistry) -> None:
    with pytest.raises(TaskNotFound, match='tsk_missing'):
        run(registry.transition('tsk_missing', TaskState.working, 'x'))
    with pytest.raises(TaskNotFound):
        registry.archive('tsk_missing')


def test_a_new_task_is_announced_once(
    registry: TaskRegistry, publisher: RecordingPublisher
) -> None:
    task = run(registry.spawn(AGENT, 'Check disk usage', prompt='Go.'))
    kinds = [kind for kind, _ in publisher.sent]
    assert kinds == ['task.created', 'task.event', 'task.event']
    data = publisher.sent[0][1]
    validator('events.schema.json#/$defs/TaskCreated').validate(
        {'id': 1, 'type': 'task.created', 'at': data['created_at'], 'data': data}
    )
    assert data['id'] == task.id
