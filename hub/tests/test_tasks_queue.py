"""The worker-cap queue: tasks that hit `cap_reached` wait, and start oldest first."""

from sqlmodel import Session, select
from test_tasks_fakes import (  # noqa: F401  (fixtures)
    AGENT,
    FakeRunner,
    outbox_fixture,
    publisher_fixture,
    registry_fixture,
    run,
    runner_fixture,
)

from marcel_hub.models import TaskEvent, TaskLocation, TaskState
from marcel_hub.tasks.registry import TaskRegistry


def spawn(registry: TaskRegistry, title: str) -> str:
    return run(registry.spawn(AGENT, title, prompt=f'Prompt for {title}')).id


def test_cap_of_one_with_three_spawns_then_stop_starts_the_next(
    registry: TaskRegistry, runner: FakeRunner
) -> None:
    runner.cap = 1
    first, second, third = (spawn(registry, t) for t in ('one', 'two', 'three'))
    run(registry.report_state(first, TaskState.working, 'The runner reports working'))

    states = [registry.get(t).state for t in (first, second, third)]
    assert states == [TaskState.working, TaskState.queued, TaskState.queued]
    assert registry.get(second).queued_reason == 'cap'
    assert [t.id for t in registry.queue()] == [second, third]

    run(registry.stop(first))

    assert registry.get(first).state == TaskState.stopped
    assert registry.get(second).state == TaskState.starting
    assert registry.get(second).session_id == f'ses-{second}'
    assert registry.get(second).queued_reason is None
    assert registry.get(third).state == TaskState.queued
    # The queued task starts with the prompt it was spawned with.
    assert runner.started[-1] == (second, 'Prompt for two')


def test_stopping_a_queued_task_removes_it_from_the_queue(
    registry: TaskRegistry, runner: FakeRunner
) -> None:
    runner.cap = 1
    first, second, third = (spawn(registry, t) for t in ('one', 'two', 'three'))

    run(registry.stop(second))

    assert registry.get(second).state == TaskState.stopped
    assert [t.id for t in registry.queue()] == [third]
    assert runner.stopped == []  # it never had a session
    run(registry.stop(first))
    assert registry.get(third).state == TaskState.starting


def test_a_new_spawn_waits_behind_the_queue(registry: TaskRegistry, runner: FakeRunner) -> None:
    runner.cap = 1
    spawn(registry, 'one')
    second = spawn(registry, 'two')
    runner.cap = 2  # a slot frees without anyone telling the registry
    third = spawn(registry, 'three')

    # The oldest waiting task got the free slot, not the newcomer.
    assert registry.get(second).state == TaskState.starting
    assert registry.get(third).state == TaskState.queued


def test_finishing_a_task_starts_the_next(registry: TaskRegistry, runner: FakeRunner) -> None:
    runner.cap = 1
    first, second = spawn(registry, 'one'), spawn(registry, 'two')
    runner.running.clear()  # the runner no longer counts the finished session

    run(registry.report_state(first, TaskState.done, 'The worker reports done'))

    assert registry.get(second).state == TaskState.starting


def test_the_queue_waits_while_the_runner_is_down(
    registry: TaskRegistry, runner: FakeRunner
) -> None:
    runner.cap = 1
    first, second = spawn(registry, 'one'), spawn(registry, 'two')
    runner.down = True
    runner.running.clear()

    run(registry.report_state(first, TaskState.failed, 'The worker reports failed'))

    assert registry.get(second).state == TaskState.queued
    runner.down = False
    run(registry.drain())
    assert registry.get(second).state == TaskState.starting


def test_a_queued_task_that_cannot_start_fails_and_the_next_one_tries(
    registry: TaskRegistry, runner: FakeRunner
) -> None:
    runner.cap = 1
    first, second, third = (spawn(registry, t) for t in ('one', 'two', 'three'))
    runner.fail_with = 'The repository could not be cloned.'

    run(registry.stop(first))

    assert registry.get(second).state == TaskState.failed
    assert registry.get(third).state == TaskState.failed
    events = registry.events(second)[0]
    assert [e.data_json['to'] for e in events if e.type == 'state'] == [
        'queued',
        'starting',
        'failed',
    ]
    assert 'could not be cloned' in events[-1].data_json['reason']


def test_the_queue_survives_a_new_registry(registry: TaskRegistry, runner: FakeRunner) -> None:
    runner.cap = 1
    first, second = spawn(registry, 'one'), spawn(registry, 'two')
    restarted = TaskRegistry(registry.engine, runner, registry.outbox)

    run(restarted.stop(first))

    assert restarted.get(second).state == TaskState.starting
    assert runner.started[-1] == (second, 'Prompt for two')


def test_a_cloud_task_does_not_wait_behind_the_nuc_queue(
    registry: TaskRegistry, runner: FakeRunner
) -> None:
    runner.cap = 1
    spawn(registry, 'one')
    second = spawn(registry, 'two')
    runner.cap = 2  # room for one more session, and the cloud task asks first
    cloud = run(registry.spawn(AGENT, 'cloud', prompt='x', location=TaskLocation.cloud))
    assert cloud.state == TaskState.starting
    assert registry.get(second).state == TaskState.queued


def test_a_queued_task_starts_with_its_prompt_not_a_later_note_from_marcel(
    registry: TaskRegistry, runner: FakeRunner
) -> None:
    runner.cap = 1
    first, second = spawn(registry, 'one'), spawn(registry, 'two')
    run(registry.steer(second, 'Also check the logs.', author='marcel'))
    with Session(registry.engine) as s:  # make the later note the first one stored
        for event in s.exec(select(TaskEvent).where(TaskEvent.task_id == second)):
            if event.data_json.get('prompt'):
                s.delete(event)
                s.add(TaskEvent(task_id=second, seq=99, type=event.type,
                                data_json=event.data_json, at=event.at))  # fmt: skip
        s.commit()

    run(registry.stop(first))

    assert runner.started[-1] == (second, 'Prompt for two')
