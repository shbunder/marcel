"""The scheduler with a frozen clock (S-09.1): fire, pause, a missed fire reported once, and the
deadline alert.

Controls, each with a test here that goes red without it: missed fires are not made up; all the
fires one look missed make one message and one push; a paused schedule does not fire; a fire moves
`next_run_at` on before it hands the work on (never fired twice); a deadline says "did not run"
once; a worker spawn goes through the task registry, so the usage guard and the cap queue it; a
built-in schedule cannot be deleted; stopping lets a running look finish.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select
from test_tasks_fakes import (  # noqa: F401  (fixtures)
    AGENT,
    FakeRunner,
    outbox_fixture,
    publisher_fixture,
    registry_fixture,
    run,
    runner_fixture,
)

from marcel_hub.channel.outbox import ChannelOutbox
from marcel_hub.models import (
    Message,
    MessageRole,
    QueuedReason,
    Schedule,
    Task,
    TaskOrigin,
    TaskState,
)
from marcel_hub.notify.message import PushNotification
from marcel_hub.schedule import CronError, ScheduleBuiltin, ScheduleNotFound, Scheduler
from marcel_hub.schedule.scheduler import GRACE, STATE_FILE
from marcel_hub.tasks.registry import TaskRegistry

# Monday 12 October 2026, 07:59 in Brussels (UTC+2).
START = datetime(2026, 10, 12, 5, 59, tzinfo=UTC)
DAILY = {'name': 'Garden check', 'cron': '0 8 * * *', 'tz': 'Europe/Brussels',
         'location': 'nuc', 'prompt': 'Check the garden notes; rm -rf / is just text.'}  # fmt: skip


class Clock:
    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def go(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class Pushes:
    def __init__(self) -> None:
        self.sent: list[tuple[PushNotification, str | None]] = []

    def push(self, n: PushNotification, user_id: str | None) -> bool:
        self.sent.append((n, user_id))
        return True


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def pushes() -> Pushes:
    return Pushes()


@pytest.fixture
def frames(engine: Engine) -> ChannelOutbox:
    return ChannelOutbox(engine)


def make(
    engine: Engine,
    registry: TaskRegistry,
    frames: ChannelOutbox,
    clock: Clock,
    pushes: Pushes,
    state: Path | None = None,
) -> Scheduler:
    return Scheduler(engine, registry, frames, pusher=pushes, clock=clock, state_path=state)


@pytest.fixture
def sched(
    engine: Engine,
    registry: TaskRegistry,
    frames: ChannelOutbox,
    clock: Clock,
    pushes: Pushes,
    tmp_path: Path,
) -> Scheduler:
    return make(engine, registry, frames, clock, pushes, tmp_path / STATE_FILE)


def tasks(engine: Engine) -> list[Task]:
    with Session(engine) as s:
        return list(s.exec(select(Task).order_by(col(Task.created_at))))


def said(engine: Engine) -> list[str]:
    with Session(engine) as s:
        rows = s.exec(select(Message).where(Message.role == MessageRole.system))
        return [m.body_json['text'] for m in rows]


def row(engine: Engine, schedule_id: str) -> Schedule:
    with Session(engine) as s:
        found = s.get(Schedule, schedule_id)
        assert found is not None
        return found


def next_run(engine: Engine, schedule_id: str) -> datetime | None:
    at = row(engine, schedule_id).next_run_at
    return at.replace(tzinfo=UTC) if at and at.tzinfo is None else at


# Firing -------------------------------------------------------------------------------------


def test_a_worker_schedule_fires_at_its_time_and_moves_on(
    sched: Scheduler, clock: Clock, engine: Engine, runner: FakeRunner
) -> None:
    made = sched.create(AGENT, DAILY)
    assert made['next_run_at'] == '2026-10-12T06:00:00Z'  # 08:00 Brussels, stored in UTC

    run(sched.tick())
    assert tasks(engine) == []  # 07:59: not yet

    clock.go(minutes=1, seconds=5)
    run(sched.tick())
    (task,) = tasks(engine)
    assert task.origin == TaskOrigin.schedule and task.schedule_id == made['id']
    assert task.title == 'Garden check' and task.state == TaskState.starting
    # The owner's prompt reaches the worker as text, unchanged.
    assert runner.started[0][1] == DAILY['prompt']
    after = sched.list(AGENT)[0]
    assert after['next_run_at'] == '2026-10-13T06:00:00Z'
    assert after['last_run_at'] == '2026-10-12T06:00:05Z'

    run(sched.tick())  # the same minute again: nothing more
    assert len(tasks(engine)) == 1


def test_a_brain_schedule_sends_a_system_event_to_the_brain(
    sched: Scheduler, clock: Clock, engine: Engine, frames: ChannelOutbox
) -> None:
    made = sched.create(AGENT, {**DAILY, 'location': 'brain'})
    clock.go(minutes=1)
    run(sched.tick())
    assert tasks(engine) == []
    target = f'conversation:{frames.main_conversation(AGENT)}'
    (frame,) = frames.pending(target)
    assert frame['type'] == 'system_event' and frame['kind'] == 'schedule_fired'
    assert frame['data'] == {
        'schedule_id': made['id'],
        'name': 'Garden check',
        'prompt': DAILY['prompt'],
        'fired_at': '2026-10-12T06:00:00Z',
    }


def test_a_fire_a_little_late_still_runs(sched: Scheduler, clock: Clock, engine: Engine) -> None:
    sched.create(AGENT, DAILY)
    clock.go(minutes=1)
    clock.now += GRACE
    run(sched.tick())
    assert len(tasks(engine)) == 1 and said(engine) == []


def test_a_fire_is_moved_on_before_the_work_is_handed_on(
    sched: Scheduler, clock: Clock, engine: Engine, registry: TaskRegistry
) -> None:
    made = sched.create(AGENT, DAILY)
    seen: list[Any] = []

    async def spawn(*args: Any, **kw: Any) -> Task:
        seen.append(next_run(engine, made['id']))
        raise RuntimeError('the hub fell over mid-spawn')

    registry.spawn = spawn  # type: ignore[method-assign]
    clock.go(minutes=1)
    run(sched.tick())
    assert seen == [datetime(2026, 10, 13, 6, 0, tzinfo=UTC)]  # already moved on when the spawn ran
    run(sched.tick())
    assert len(seen) == 1  # never fired twice


# The usage guard and the cap ----------------------------------------------------------------


def test_a_fire_while_the_usage_guard_holds_is_queued_not_dropped(
    sched: Scheduler, clock: Clock, engine: Engine, registry: TaskRegistry, runner: FakeRunner
) -> None:
    registry.hold_new_work = lambda: 'Plan usage is at 91 %.'
    sched.create(AGENT, DAILY)
    clock.go(minutes=1)
    run(sched.tick())
    (task,) = tasks(engine)
    assert task.state == TaskState.queued and task.queued_reason == QueuedReason.usage
    assert runner.started == []


def test_a_fire_with_the_cap_full_is_queued_and_starts_when_a_slot_frees(
    sched: Scheduler, clock: Clock, engine: Engine, registry: TaskRegistry, runner: FakeRunner
) -> None:
    runner.cap = 0
    sched.create(AGENT, DAILY)
    clock.go(minutes=1)
    run(sched.tick())
    (task,) = tasks(engine)
    assert task.state == TaskState.queued and task.queued_reason == QueuedReason.cap
    runner.cap = 1
    run(registry.drain())
    assert registry.get(task.id).state == TaskState.starting


# Pause, resume, delete ----------------------------------------------------------------------


def test_a_paused_schedule_does_not_fire_and_resuming_reports_nothing(
    sched: Scheduler, clock: Clock, engine: Engine, pushes: Pushes
) -> None:
    made = sched.create(AGENT, DAILY)
    paused = sched.update(made['id'], {'paused': True})
    assert paused['paused'] is True and 'next_run_at' not in paused
    clock.go(days=3)
    run(sched.tick())
    assert tasks(engine) == []

    resumed = sched.update(made['id'], {'paused': False})
    assert resumed['next_run_at'] == '2026-10-15T06:00:00Z'  # the next one after now
    run(sched.tick())
    assert said(engine) == [] and pushes.sent == []  # the paused days were not "missed"


def test_a_paused_row_with_a_due_time_still_does_not_fire(
    sched: Scheduler, clock: Clock, engine: Engine
) -> None:
    made = sched.create(AGENT, DAILY)
    with Session(engine) as s:  # paused, but next_run_at left in place
        r = s.get(Schedule, made['id'])
        assert r is not None
        r.paused = True
        s.add(r)
        s.commit()
    clock.go(minutes=1)
    run(sched.tick())
    assert tasks(engine) == []


def test_changing_the_time_moves_the_next_run(sched: Scheduler) -> None:
    made = sched.create(AGENT, DAILY)
    changed = sched.update(made['id'], {'cron': '30 9 * * *', 'name': 'Later check'})
    assert changed['next_run_at'] == '2026-10-12T07:30:00Z' and changed['name'] == 'Later check'
    with pytest.raises(CronError):
        sched.update(made['id'], {'tz': 'Mars/Olympus'})
    with pytest.raises(ScheduleNotFound):
        sched.update('sch_nope', {'paused': True})
    with pytest.raises(ValueError, match='cannot be changed'):
        sched.update(made['id'], {'builtin': False})


def test_delete_removes_it_and_a_builtin_cannot_be_deleted(sched: Scheduler) -> None:
    made = sched.create(AGENT, DAILY)
    builtin = sched.create(AGENT, {**DAILY, 'name': 'Morning digest'}, builtin=True)
    sched.delete(AGENT, made['id'])
    with pytest.raises(ScheduleNotFound):
        sched.delete(AGENT, made['id'])
    with pytest.raises(ScheduleNotFound):
        sched.delete('agt_other', builtin['id'])
    with pytest.raises(ScheduleBuiltin, match='paused in the app, not deleted'):
        sched.delete(AGENT, builtin['id'])
    assert [s['id'] for s in sched.list(AGENT)] == [builtin['id']]
    assert sched.update(builtin['id'], {'paused': True})['paused'] is True


def test_the_list_is_by_next_run_with_paused_last(sched: Scheduler) -> None:
    daily = sched.create(AGENT, DAILY)
    hourly = sched.create(AGENT, {**DAILY, 'cron': '0 * * * *', 'name': 'Hourly'})
    weekly = sched.create(AGENT, {**DAILY, 'cron': '0 8 * * 1', 'name': 'Weekly'})
    sched.update(hourly['id'], {'paused': True})
    assert [s['id'] for s in sched.list(AGENT)] == [daily['id'], weekly['id'], hourly['id']]


# Missed fires -------------------------------------------------------------------------------


def test_missed_fires_are_not_made_up_and_are_reported_once(
    sched: Scheduler, clock: Clock, engine: Engine, pushes: Pushes
) -> None:
    daily = sched.create(AGENT, DAILY)
    sched.create(AGENT, {**DAILY, 'cron': '0 8 * * 1', 'name': 'Weekly report'})
    clock.go(days=2, hours=3)  # the hub was off: three garden checks and one report went by

    run(sched.tick())

    assert tasks(engine) == []  # nothing made up
    (text,) = said(engine)
    assert text.startswith('Marcel was off when 2 scheduled jobs were due')
    assert '“Garden check” (3 times)' in text and '“Weekly report”' in text
    ((push, user),) = pushes.sent
    assert push.category == 'note' and push.deep_link.startswith('marcel://message/')
    assert user == 'usr_1'
    assert next_run(engine, daily['id']) == datetime(2026, 10, 15, 6, 0, tzinfo=UTC)

    run(sched.tick())
    assert len(said(engine)) == 1 and len(pushes.sent) == 1  # once


def test_a_restart_after_downtime_reports_once_and_the_store_survives(
    engine: Engine,
    registry: TaskRegistry,
    frames: ChannelOutbox,
    clock: Clock,
    pushes: Pushes,
    tmp_path: Path,
) -> None:
    first = make(engine, registry, frames, clock, pushes, tmp_path / STATE_FILE)
    made = first.create(AGENT, DAILY)
    clock.go(days=1)  # down for a day

    second = make(engine, registry, frames, clock, pushes, tmp_path / STATE_FILE)
    assert [s['id'] for s in second.list(AGENT)] == [made['id']]
    run(second.tick())
    (text,) = said(engine)
    assert text.startswith('Marcel was off when a scheduled job was due, so it did not run')

    third = make(engine, registry, frames, clock, pushes, tmp_path / STATE_FILE)
    run(third.tick())
    assert len(said(engine)) == 1 and len(pushes.sent) == 1


# Deadlines ----------------------------------------------------------------------------------


def test_a_run_that_never_starts_says_did_not_run_once(
    sched: Scheduler, clock: Clock, engine: Engine, runner: FakeRunner, pushes: Pushes
) -> None:
    runner.cap = 0
    sched.create(AGENT, {**DAILY, 'deadline_minutes': 30})
    clock.go(minutes=1)
    run(sched.tick())
    clock.go(minutes=29)
    run(sched.tick())
    assert said(engine) == []  # 08:29: still in time

    clock.go(minutes=1)
    run(sched.tick())
    (text,) = said(engine)
    assert text == ('“Garden check” did not run. It was due at 08:00 and had not started 30 '
                    'minutes later: it is waiting for a free worker on the NUC.')  # fmt: skip
    ((push, _),) = pushes.sent
    assert push.title == 'A scheduled job did not run' and push.collapse_key.startswith('late:')

    clock.go(hours=1)
    run(sched.tick())
    assert len(said(engine)) == 1 and len(pushes.sent) == 1


def test_the_deadline_names_the_usage_guard(
    sched: Scheduler, clock: Clock, engine: Engine, registry: TaskRegistry
) -> None:
    registry.hold_new_work = lambda: 'Plan usage is at 91 %.'
    sched.create(AGENT, {**DAILY, 'deadline_minutes': 10})
    clock.go(minutes=1)
    run(sched.tick())
    clock.go(minutes=10)
    run(sched.tick())
    (text,) = said(engine)
    assert text.endswith('it is waiting because the plan’s usage is near its limit.')


def test_a_run_that_started_in_time_says_nothing(
    sched: Scheduler, clock: Clock, engine: Engine, pushes: Pushes
) -> None:
    sched.create(AGENT, {**DAILY, 'deadline_minutes': 30})
    clock.go(minutes=1)
    run(sched.tick())
    assert sched.watches == {}  # seen starting on the same look
    clock.go(hours=1)
    run(sched.tick())
    assert said(engine) == [] and pushes.sent == []


def test_a_brain_event_not_taken_says_did_not_run_and_a_taken_one_does_not(
    sched: Scheduler, clock: Clock, engine: Engine, frames: ChannelOutbox
) -> None:
    taken = sched.create(AGENT, {**DAILY, 'location': 'brain', 'deadline_minutes': 5,
                                 'name': 'Taken'})  # fmt: skip
    sched.create(AGENT, {**DAILY, 'location': 'brain', 'deadline_minutes': 5, 'name': 'Lost'})
    clock.go(minutes=1)
    run(sched.tick())
    target = f'conversation:{frames.main_conversation(AGENT)}'
    for frame in frames.pending(target):
        if frame['data']['schedule_id'] == taken['id']:
            frames.ack(target, frame['seq'])
    clock.go(minutes=5)
    run(sched.tick())
    (text,) = said(engine)
    assert text.startswith('“Lost” did not run') and 'brain has not picked it up' in text


def test_a_fire_that_failed_to_start_says_so_at_the_deadline(
    sched: Scheduler, clock: Clock, engine: Engine, registry: TaskRegistry
) -> None:
    async def broken(*args: Any, **kw: Any) -> Task:
        raise RuntimeError('boom')

    registry.spawn = broken  # type: ignore[method-assign]
    made = sched.create(AGENT, {**DAILY, 'deadline_minutes': 5})
    clock.go(minutes=1)
    run(sched.tick())
    assert next_run(engine, made['id']) == datetime(2026, 10, 13, 6, 0, tzinfo=UTC)
    clock.go(minutes=5)
    run(sched.tick())
    (text,) = said(engine)
    assert 'Marcel could not start it' in text


def test_a_deadline_watch_survives_a_restart(
    engine: Engine,
    registry: TaskRegistry,
    frames: ChannelOutbox,
    clock: Clock,
    pushes: Pushes,
    runner: FakeRunner,
    tmp_path: Path,
) -> None:
    runner.cap = 0
    first = make(engine, registry, frames, clock, pushes, tmp_path / STATE_FILE)
    first.create(AGENT, {**DAILY, 'deadline_minutes': 30})
    clock.go(minutes=1)
    run(first.tick())
    clock.go(minutes=40)  # the hub restarts after the deadline
    second = make(engine, registry, frames, clock, pushes, tmp_path / STATE_FILE)
    run(second.tick())
    assert len(said(engine)) == 1
    third = make(engine, registry, frames, clock, pushes, tmp_path / STATE_FILE)
    run(third.tick())
    assert len(said(engine)) == 1


def test_deleting_a_watched_schedule_drops_its_watch(
    sched: Scheduler, clock: Clock, engine: Engine, runner: FakeRunner
) -> None:
    runner.cap = 0
    made = sched.create(AGENT, {**DAILY, 'deadline_minutes': 5})
    clock.go(minutes=1)
    run(sched.tick())
    sched.delete(AGENT, made['id'])
    clock.go(minutes=10)
    run(sched.tick())
    assert said(engine) == []


def test_an_unreadable_state_file_starts_without_watches(
    engine: Engine,
    registry: TaskRegistry,
    frames: ChannelOutbox,
    clock: Clock,
    pushes: Pushes,
    tmp_path: Path,
) -> None:
    for content in ('not json', json.dumps({'watches': {'sch_1': {'fired_at': 'soon'}}})):
        (tmp_path / STATE_FILE).write_text(content)
        assert make(engine, registry, frames, clock, pushes, tmp_path / STATE_FILE).watches == {}


# Odd rows and the loop ----------------------------------------------------------------------


def test_a_stored_cron_that_cannot_be_read_pauses_its_schedule(
    sched: Scheduler, clock: Clock, engine: Engine
) -> None:
    made = sched.create(AGENT, DAILY)
    with Session(engine) as s:
        r = s.get(Schedule, made['id'])
        assert r is not None
        r.cron = 'every day'
        s.add(r)
        s.commit()
    clock.go(minutes=1)
    run(sched.tick())
    assert tasks(engine) == [] and row(engine, made['id']).paused is True


def test_next_wait_is_the_next_fire_within_bounds(sched: Scheduler, clock: Clock) -> None:
    assert sched.next_wait() == 30.0  # nothing scheduled
    sched.create(AGENT, DAILY)
    assert sched.next_wait() == 30.0  # 60 s away: look again in 30
    clock.go(seconds=50)
    assert sched.next_wait() == 10.0
    clock.go(seconds=20)
    assert sched.next_wait() == 1.0


def test_without_a_pusher_the_message_is_still_posted(
    engine: Engine, registry: TaskRegistry, frames: ChannelOutbox, clock: Clock
) -> None:
    sched = Scheduler(engine, registry, frames, clock=clock)
    sched.create(AGENT, DAILY)
    clock.go(days=1)
    run(sched.tick())
    assert len(said(engine)) == 1


def test_stopping_lets_a_running_look_finish(
    sched: Scheduler, clock: Clock, engine: Engine, runner: FakeRunner
) -> None:
    sched.create(AGENT, DAILY)
    clock.go(minutes=1)

    async def scenario() -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        real_start = runner.start

        async def slow_start(task: Task, prompt: str) -> Any:
            entered.set()
            await release.wait()
            return await real_start(task, prompt)

        runner.start = slow_start  # type: ignore[method-assign]
        sched.start()
        await entered.wait()
        stopping = asyncio.create_task(sched.stop())
        await asyncio.sleep(0.05)
        assert not stopping.done()  # it waits for the spawn in flight
        release.set()
        await stopping
        await sched.stop()  # a second stop is fine

    run(scenario())
    (task,) = tasks(engine)
    assert task.state == TaskState.starting


def test_the_loop_keeps_going_after_a_look_fails(sched: Scheduler) -> None:
    calls: list[int] = []
    again = asyncio.Event

    async def scenario() -> None:
        looked_again = again()

        async def tick() -> None:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('a bad look')
            looked_again.set()

        sched.tick = tick  # type: ignore[method-assign]
        sched.next_wait = lambda: 0.01  # type: ignore[method-assign]
        sched.start()
        await asyncio.wait_for(looked_again.wait(), timeout=2)
        await sched.stop()

    run(scenario())
    assert len(calls) >= 2
