"""The usage guard (S-10.1): holding new work near the plan's limit, one push per crossing, the
drain after the reset, fail-open on stale readings, and limit hits. Nothing here reaches a runner,
Claude or the network: readings come from `FakeReader`, pushes go to `FakePushes`."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select
from test_tasks_fakes import (  # noqa: F401  (fixtures)
    AGENT,
    FakeRunner,
    RecordingPublisher,
    add_agent,
    outbox_fixture,
    publisher_fixture,
    registry_fixture,
    run,
    runner_fixture,
    validator,
)

from marcel_hub.models import QueuedReason, Task, TaskLocation, TaskState, UsageSnapshot
from marcel_hub.notify.message import PushNotification
from marcel_hub.runner_client import map_session
from marcel_hub.tasks.ports import MemoryOutbox, Started
from marcel_hub.tasks.registry import TaskRegistry
from marcel_hub.usage import LIMIT_REASON, NoReading, UsageGuard

NOW = datetime(2026, 10, 12, 11, 0, tzinfo=UTC)
RESET = NOW + timedelta(hours=3)  # 14:00 UTC


def at(t: datetime) -> str:
    return t.isoformat().replace('+00:00', 'Z')


def reading(
    five: float = 20.0,
    weekly: float = 30.0,
    stale: bool = False,
    resets: datetime = RESET,
    weekly_resets: datetime = NOW + timedelta(days=3),
) -> dict[str, Any]:
    return {
        'five_hour': {'used_pct': five, 'resets_at': at(resets)},
        'weekly': {'used_pct': weekly, 'resets_at': at(weekly_resets)},
        'source': 'cli_usage',
        'taken_at': at(NOW),
        'stale': stale,
    }


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


class FakeReader:
    def __init__(self) -> None:
        self.next: dict[str, Any] | None = None

    async def usage(self) -> dict[str, Any] | None:
        return self.next


class FakePushes:
    def __init__(self) -> None:
        self.sent: list[PushNotification] = []

    def push(self, n: PushNotification, user_id: str | None) -> bool:
        self.sent.append(n)
        return True


class World:
    def __init__(self, registry: TaskRegistry, runner: FakeRunner, tmp_path: Path) -> None:
        self.registry, self.runner = registry, runner
        self.clock = Clock()
        self.reader = FakeReader()
        self.pushes = FakePushes()
        self.events = RecordingPublisher()
        self.state = tmp_path / 'usage-guard.json'
        self.guard = self.new_guard()

    def new_guard(self) -> UsageGuard:
        guard = UsageGuard(
            self.reader,
            pusher=self.pushes,
            publisher=self.events,
            state_path=self.state,
            clock=self.clock,
            tz=UTC,
        )
        guard.registry = self.registry
        self.registry.hold_new_work = guard.holding
        # The guard watches the registry's events for limit hits, as in the app.
        self.registry.publisher = guard.task_publisher(self.registry.publisher)
        return guard

    def poll(self, value: dict[str, Any] | None) -> None:
        self.reader.next = value
        run(self.guard.tick())

    def spawn(self, title: str, location: TaskLocation = TaskLocation.nuc) -> str:
        return run(
            self.registry.spawn(AGENT, title, prompt=f'Prompt for {title}', location=location)
        ).id


@pytest.fixture
def world(registry: TaskRegistry, runner: FakeRunner, tmp_path: Path) -> World:
    return World(registry, runner, tmp_path)


# Done when: above the threshold the spawn is queued, not started --------------------------------


def test_above_the_threshold_a_spawn_is_queued_not_started(world: World) -> None:
    """The usage guard is a control: with its check in `TaskRegistry.spawn` removed, the task
    starts and this test fails."""
    world.poll(reading(five=91))

    task = world.registry.get(world.spawn('one'))

    assert world.runner.started == []
    assert task.state == TaskState.queued
    assert task.queued_reason == QueuedReason.usage
    cloud = world.registry.get(world.spawn('two', TaskLocation.cloud))  # the plan is shared
    assert (cloud.state, cloud.queued_reason) == (TaskState.queued, QueuedReason.usage)
    assert world.runner.started == []


def test_the_weekly_window_holds_from_95_percent(world: World) -> None:
    world.poll(reading(weekly=94.9))
    assert world.guard.holding() is None
    world.poll(reading(weekly=95))
    assert world.guard.holding() == (
        'Weekly plan usage is at 95 %. New tasks wait until Thu 11:00.'
    )


def test_below_the_threshold_spawns_start(world: World) -> None:
    world.poll(reading(five=89.9, weekly=94.9))
    assert world.registry.get(world.spawn('one')).state == TaskState.starting


def test_running_work_continues_while_the_guard_holds(world: World) -> None:
    running = world.spawn('one')
    run(world.registry.report_state(running, TaskState.working, 'The runner reports working'))
    world.poll(reading(five=95))
    run(world.registry.steer(running, 'Carry on.'))
    assert world.registry.get(running).state == TaskState.working
    assert world.runner.stopped == []


# Done when: the reset drains the queue in FIFO order --------------------------------------------


def test_the_reset_drains_the_queue_oldest_first(world: World) -> None:
    world.poll(reading(five=92))
    ids = [world.spawn(t) for t in ('one', 'two', 'three')]
    world.clock.advance(minutes=10)
    world.poll(reading(five=96))  # still over: nothing starts
    assert world.runner.started == []

    world.clock.now = RESET  # the window resets; the reading is still the old one
    world.poll(reading(five=96))

    assert [tid for tid, _ in world.runner.started] == ids
    assert all(world.registry.get(t).state == TaskState.starting for t in ids)
    states = [e for e in world.registry.events(ids[0])[0] if e.type == 'state']
    assert states[-1].data_json['reason'] == 'Plan usage allows new work again'


def test_the_drain_after_a_reset_respects_the_worker_cap(world: World) -> None:
    world.runner.cap = 1
    nuc_start = world.runner.start

    async def start(task: Task, prompt: str) -> Started:
        if task.location == TaskLocation.cloud:  # the cap is the NUC's
            return Started(session_id=f'cloud-{task.id}')
        return await nuc_start(task, prompt)

    world.runner.start = start  # type: ignore[method-assign]
    world.poll(reading(five=92))
    first, second, third = (world.spawn(t) for t in ('one', 'two', 'three'))
    cloud = world.spawn('cloud', TaskLocation.cloud)
    world.clock.now = RESET + timedelta(seconds=1)

    world.poll(reading(five=3, resets=RESET + timedelta(hours=5)))

    assert world.registry.get(first).state == TaskState.starting
    for tid in (second, third):
        task = world.registry.get(tid)
        # Now it waits for a worker slot, not for the plan.
        assert (task.state, task.queued_reason) == (TaskState.queued, QueuedReason.cap)
    assert world.registry.get(cloud).state == TaskState.starting  # the cap is the NUC's
    world.runner.running.clear()
    run(world.registry.report_state(first, TaskState.done, 'The worker reports done'))
    assert world.registry.get(second).state == TaskState.starting
    assert world.registry.get(third).state == TaskState.queued


def test_a_fresh_low_reading_lets_go_before_the_reset(world: World) -> None:
    world.poll(reading(five=92))
    queued = world.spawn('one')
    world.clock.advance(minutes=5)
    world.poll(reading(five=10, resets=RESET + timedelta(hours=1)))  # an early reset
    assert world.registry.get(queued).state == TaskState.starting


def test_a_hold_past_its_reset_holds_nothing_before_the_next_poll(world: World) -> None:
    world.poll(reading(five=92))
    world.clock.now = RESET
    assert world.guard.holding() is None
    assert world.registry.get(world.spawn('one')).state == TaskState.starting


# Done when: a stale reading never blocks forever ------------------------------------------------


def test_stale_readings_fail_open_after_30_minutes_with_a_log(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    world.poll(reading(five=92, resets=NOW + timedelta(hours=4)))
    queued = world.spawn('one')
    world.clock.advance(minutes=29)
    world.poll(reading(five=92, stale=True))
    assert world.guard.holding() is not None
    world.clock.advance(minutes=1)

    with caplog.at_level(logging.WARNING, logger='marcel_hub.usage'):
        world.poll(None)  # the runner has no reading at all

    assert world.guard.holding() is None
    assert world.registry.get(queued).state == TaskState.starting
    assert 'no fresh usage reading for 30 minutes' in caplog.text
    assert len(world.pushes.sent) == 1  # the owner is not pushed about it
    world.clock.advance(minutes=5)
    world.poll(reading(five=99, stale=True))  # a stale reading never starts a hold
    assert world.guard.holding() is None


def test_a_stale_reading_over_the_limit_does_not_hold(world: World) -> None:
    world.poll(reading(five=99, stale=True))
    assert world.guard.holding() is None
    assert world.pushes.sent == []


def test_a_window_that_already_reset_does_not_hold(world: World) -> None:
    world.poll(reading(five=99, resets=NOW - timedelta(minutes=1)))
    assert world.guard.holding() is None


# One push per crossing --------------------------------------------------------------------------


def test_one_push_per_crossing_with_the_reset_time(world: World) -> None:
    for _ in range(3):
        world.poll(reading(five=91))
        world.clock.advance(minutes=1)
    world.spawn('one')
    world.spawn('two')
    world.poll(reading(five=97))

    assert len(world.pushes.sent) == 1
    push = world.pushes.sent[0]
    assert push.title == 'Marcel is pausing new work'
    assert push.body.startswith(
        'Marcel is pausing new work until 14:00: the subscription is at 91 %.'
    )
    assert (push.category, push.level, push.deep_link) == ('note', 'normal', 'marcel://status')

    world.clock.now = RESET  # the next window crosses again: one more push
    world.poll(reading(five=5, resets=RESET + timedelta(hours=5)))
    world.poll(reading(five=93, resets=RESET + timedelta(hours=5)))
    assert len(world.pushes.sent) == 2
    assert 'until 19:00' in world.pushes.sent[1].body


def test_a_hold_that_starts_again_for_the_same_reset_does_not_push_again(world: World) -> None:
    world.poll(reading(five=92))
    world.clock.advance(minutes=31)
    world.poll(None)  # fail-open
    world.clock.advance(minutes=1)
    world.poll(reading(five=93))  # the same window, over again
    assert world.guard.holding() is not None
    assert len(world.pushes.sent) == 1


# Limit hits (SP4) -------------------------------------------------------------------------------


def transcript_hit(world: World, task_id: str) -> None:
    event = {
        'type': 'error',
        'at': at(world.clock.now),
        'data': {
            'message': "You've hit your session limit · resets 2:29pm (UTC)",
            'api_error_status': 429,
            'usage_limit': True,
        },
    }
    world.registry.append_transcript(task_id, event)


def test_a_limit_hit_in_a_transcript_holds_even_with_a_low_stale_reading(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    world.poll(reading(five=12, stale=True))
    running = world.spawn('one')

    with caplog.at_level(logging.WARNING, logger='marcel_hub.usage'):
        transcript_hit(world, running)
        transcript_hit(world, running)

    assert world.guard.holding() == (
        "The plan's usage limit is reached. New tasks wait until 14:00."
    )
    assert len(world.pushes.sent) == 1
    assert "the subscription's limit is reached" in world.pushes.sent[0].body
    assert caplog.text.count('First usage-limit transcript row seen') == 1
    assert "You've hit your session limit" in caplog.text
    queued = world.registry.get(world.spawn('two'))
    assert queued.queued_reason == QueuedReason.usage
    world.poll(reading(five=12))  # a fresh low reading does not undo a hit
    assert world.guard.holding() is not None
    world.clock.now = RESET
    world.poll(reading(five=12))
    assert world.registry.get(queued.id).state == TaskState.starting


def test_a_session_blocked_on_the_limit_holds_once_until_the_task_moves_on(world: World) -> None:
    world.poll(None)
    running = world.spawn('one')
    session = {'id': 's', 'state': 'blocked', 'needs': 'usage limit reached — check plan'}
    report = map_session(session)
    assert report.reason == LIMIT_REASON  # what the runner watcher reports for a session hit

    run(world.registry.report_state(running, report.state, report.reason, **report.fields))
    # Without a reading the reset is unknown: it holds for 30 minutes.
    assert world.guard.holding() == (
        "The plan's usage limit is reached. Marcel could not read when it resets; new tasks "
        'wait until 11:30.'
    )
    world.clock.advance(minutes=31)
    world.poll(None)
    assert world.guard.holding() is None
    # The runner reconnects and reports the same blocked session again: not a new hit.
    run(world.registry.report_state(running, TaskState.unknown, 'The runner is unreachable'))
    run(world.registry.report_state(running, report.state, report.reason))
    assert world.guard.holding() is None
    # Once the task moved on, a new hit counts again.
    run(world.registry.report_state(running, TaskState.working, 'The runner reports working'))
    run(world.registry.report_state(running, report.state, report.reason))
    assert world.guard.holding() is not None


def test_the_first_raw_session_row_is_logged_once(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    session = {'id': 's', 'state': 'blocked', 'needs': 'rate limited'}
    with caplog.at_level(logging.WARNING, logger='marcel_hub.usage'):
        run(world.guard.on_session('tsk_1', session))
        run(world.guard.on_session('tsk_1', session))
        run(world.guard.on_session('tsk_1', {'id': 's', 'state': 'working'}))
    assert caplog.text.count('First usage-limit session seen') == 1
    assert "'needs': 'rate limited'" in caplog.text


def test_a_hit_during_a_hold_extends_it_without_a_push(world: World) -> None:
    world.poll(reading(five=92))
    running = world.spawn('zero')  # queued, but give it a transcript anyway
    world.clock.advance(minutes=29)
    transcript_hit(world, running)
    world.clock.advance(minutes=29)
    world.poll(None)  # 58 minutes without a fresh reading, but the hit was 29 minutes ago
    assert world.guard.holding() is not None
    assert len(world.pushes.sent) == 1


def test_repeated_limit_hits_with_an_unknown_reset_push_once(world: World) -> None:
    world.poll(None)  # no reading: every hit holds for 30 minutes
    task = world.spawn('one')
    for _ in range(10):
        transcript_hit(world, task)
        assert world.guard.holding() is not None
        world.clock.advance(minutes=31)
        world.poll(None)  # fail-open, then the next hit holds again
        assert world.guard.holding() is None
    assert len(world.pushes.sent) == 1
    world.clock.advance(hours=1)  # five hours after that push: news again
    transcript_hit(world, task)
    assert len(world.pushes.sent) == 2


def test_a_fresh_reading_ends_the_quiet_after_an_unknown_reset(world: World) -> None:
    task = world.spawn('one')
    transcript_hit(world, task)
    world.clock.advance(minutes=31)
    world.poll(reading(five=50, resets=world.clock.now - timedelta(minutes=1)))
    world.poll(None)
    transcript_hit(world, task)
    assert len(world.pushes.sent) == 2


# Restart ----------------------------------------------------------------------------------------


def test_a_restart_keeps_the_hold_and_the_queue_and_does_not_push_again(
    world: World, engine: Engine, runner: FakeRunner
) -> None:
    world.poll(reading(five=92))
    queued = world.spawn('one')

    registry = TaskRegistry(engine, runner, MemoryOutbox(), RecordingPublisher())
    world.registry = registry
    world.guard = world.new_guard()
    run(registry.drain())  # the app drains at start
    assert registry.get(queued).state == TaskState.queued
    world.poll(reading(five=93))
    assert len(world.pushes.sent) == 1

    world.clock.now = RESET
    world.poll(reading(five=1, resets=RESET + timedelta(hours=5)))
    assert registry.get(queued).state == TaskState.starting


def test_a_broken_state_file_starts_without_a_hold(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    world.state.write_text('{"hold": {"cause": "reading"}}')
    with caplog.at_level(logging.WARNING, logger='marcel_hub.usage'):
        guard = world.new_guard()
    assert guard.hold is None
    assert 'starts without a hold' in caplog.text


@pytest.mark.parametrize('content', ['[]', '"x"', '3'])
def test_a_state_file_that_is_not_an_object_starts_clean(
    world: World, content: str, caplog: pytest.LogCaptureFixture
) -> None:
    world.state.write_text(content)
    with caplog.at_level(logging.WARNING, logger='marcel_hub.usage'):
        guard = world.new_guard()
    assert guard.hold is None and guard.by_task == {}
    assert 'does not hold a JSON object' in caplog.text


def test_an_unreadable_state_file_starts_clean(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    world.state.mkdir()  # reading a folder raises an OSError
    with caplog.at_level(logging.WARNING, logger='marcel_hub.usage'):
        guard = world.new_guard()
    assert guard.hold is None
    assert 'starts without a hold' in caplog.text


def test_a_half_read_state_file_keeps_nothing(world: World) -> None:
    world.state.write_text('{"notified_until": "x", "previous": [1]}')
    guard = world.new_guard()
    assert guard.notified_until is None and guard.previous is None


# What the app sees ------------------------------------------------------------------------------


def test_current_matches_the_contract_and_is_published_on_change(world: World) -> None:
    with pytest.raises(NoReading):
        world.guard.current()
    world.poll(reading(five=91))
    world.spawn('one')
    body = world.guard.current()
    validator('app-api.yaml#/components/schemas/Usage').validate(body)
    assert body['guard'] == {
        'active': True,
        'queued': 1,
        'reason': 'Plan usage is at 91 %. New tasks wait until 14:00.',
    }
    world.poll(reading(five=91))
    updates = [d for t, d in world.events.sent if t == 'usage.updated']
    assert len(updates) == 2  # the hold, then the queued count; the repeat sent nothing
    assert updates[-1] == world.guard.current()


def test_a_reading_goes_stale_after_15_minutes_without_a_fresh_one(world: World) -> None:
    world.poll(reading())
    assert world.guard.current()['stale'] is False
    world.clock.advance(minutes=16)
    world.poll(None)
    assert world.guard.current()['stale'] is True


def test_a_reading_that_does_not_match_the_contract_is_ignored(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger='marcel_hub.usage'):
        world.poll({'five_hour': {'used_pct': 'lots'}, 'stale': False})
    assert world.guard.reading is None
    assert 'does not match the contract' in caplog.text


# Records ----------------------------------------------------------------------------------------


def test_fresh_readings_are_kept_as_snapshots_when_they_change(world: World) -> None:
    world.poll(reading(five=10))
    world.poll(reading(five=10))
    world.poll(reading(five=12))
    world.poll(reading(five=40, stale=True))
    with Session(world.registry.engine) as s:
        rows = s.exec(select(UsageSnapshot).order_by(col(UsageSnapshot.used_pct))).all()
    assert [(r.window.value, r.used_pct) for r in rows] == [
        ('five_hour', 10.0),
        ('five_hour', 12.0),
        ('weekly', 30.0),
    ]


def test_usage_is_attributed_to_the_tasks_that_were_running(world: World) -> None:
    world.poll(reading(five=10))
    a, b = world.spawn('a'), world.spawn('b')
    world.poll(reading(five=14))
    run(world.registry.stop(b))
    world.poll(reading(five=17))
    world.poll(reading(five=1, resets=RESET + timedelta(hours=5)))  # a new window
    run(world.registry.stop(a))
    world.poll(reading(five=3, resets=RESET + timedelta(hours=5)))
    assert world.guard.usage_by_task() == {a: 5.0, b: 2.0, 'other': 2.0}
    restarted = world.new_guard()
    assert restarted.usage_by_task() == world.guard.usage_by_task()


# Polling ----------------------------------------------------------------------------------------


def test_it_polls_every_5_minutes_and_every_minute_from_70_percent(world: World) -> None:
    assert world.guard.next_wait() == 300
    world.poll(reading(five=69))
    assert world.guard.next_wait() == 300
    world.poll(reading(weekly=70))
    assert world.guard.next_wait() == 60
    world.poll(reading(five=91, resets=NOW + timedelta(seconds=20)))
    assert world.guard.next_wait() == 20  # it wakes at the reset


def test_the_loop_keeps_polling_after_an_error(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    waits: list[float] = []

    class Boom(Exception):
        pass

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 2:
            raise Boom

    calls = 0

    async def usage() -> dict[str, Any] | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('broken')
        return reading()

    world.guard.sleep = sleep
    world.reader.usage = usage  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR, logger='marcel_hub.usage'), pytest.raises(Boom):
        run(world.guard.run())
    assert calls == 2 and waits == [300, 300]
    assert 'could not check plan usage' in caplog.text


def test_start_and_stop(world: World) -> None:
    async def go() -> None:
        world.guard.start()
        await world.guard.stop()
        await world.guard.stop()

    run(go())


def test_without_a_registry_or_a_pusher_the_guard_still_holds(tmp_path: Path) -> None:
    clock = Clock()
    reader = FakeReader()
    guard = UsageGuard(reader, clock=clock, tz=UTC)
    reader.next = reading(five=95)
    run(guard.tick())
    assert guard.holding() is not None
    assert guard.current()['guard']['queued'] == 0
    clock.now = RESET
    run(guard.tick())
    assert guard.holding() is None


def test_an_unreadable_task_event_is_passed_on_and_logged(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    add_agent(engine, 'agt_other')
    inner = RecordingPublisher()
    hits = UsageGuard().task_publisher(inner)
    with caplog.at_level(logging.ERROR, logger='marcel_hub.usage'):
        hits.publish('task.event', {'type': 'state'})
    assert inner.sent == [('task.event', {'type': 'state'})]
    assert 'could not read a task event' in caplog.text
