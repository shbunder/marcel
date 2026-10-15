"""The brain keeper (S-06.2) against a fake runner: keep-alive, backoff, rollover and its seed.

Nothing here reaches a real `claude`, a runner or the network: `FakeBrains` answers the three
runner calls the keeper makes from memory."""

import asyncio
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, select
from test_runner_client_fakes import RecordingAlerts
from test_tasks_fakes import run, validator

from marcel_hub import brain
from marcel_hub.brain import (
    ALERT,
    ALERT_AFTER,
    HELLO_WAIT_S,
    SPAWN_GRACE_S,
    BrainKeeper,
    brain_name,
    next_rollover,
)
from marcel_hub.conversations import ensure_main_conversation
from marcel_hub.memory import MemoryRepo
from marcel_hub.models import (
    Agent,
    AppEvent,
    Conversation,
    Message,
    MessageKind,
    MessageRole,
    Project,
    Task,
    TaskLocation,
    TaskOrigin,
    TaskState,
)
from marcel_hub.tasks.ports import RunnerUnavailable, StartFailed
from marcel_hub.ws import EventLog

BRUSSELS = ZoneInfo('Europe/Brussels')
APP_EVENT = validator('events.schema.json')
CWD = '/home/owner/marcel/brain'


class FakeBrains:
    """The runner, as the keeper sees it: spawn a brain, list sessions, stop one."""

    def __init__(self) -> None:
        self.by_id: dict[str, dict[str, Any]] = {}
        self.spawned: list[dict[str, Any]] = []
        self.stopped: list[str] = []
        self.fail_spawns = 0
        self.unreachable = False
        self.spawn_delay = 0.0
        self.unlisted: set[str] = set()  # spawned, but not in the runner's list yet
        # Like the real runner: the session is listed (and calls the hub) before the spawn
        # request returns, which waits for its channel to register.
        self.list_before_return = False
        # Set to a cleared `threading.Event` to hold every spawn (after it is listed) until the test
        # sets it: "while its spawn is still under way" then holds for sure, with no clock in it.
        self.release: threading.Event | None = None

    async def spawn_brain(self, *, name: str, model: str, cwd: str, prompt: str) -> str:
        self.spawned.append({'name': name, 'model': model, 'cwd': cwd, 'prompt': prompt})
        early = self.add(name=name) if self.list_before_return else None
        if self.spawn_delay:
            await asyncio.sleep(self.spawn_delay)
        while self.release is not None and not self.release.is_set():
            await asyncio.sleep(0.01)
        if self.fail_spawns:
            self.fail_spawns -= 1
            raise StartFailed('The session did not start: the channel never registered.')
        return early or self.add(name=name)

    def add(self, name: str, role: str = 'brain', **fields: Any) -> str:
        sid = str(uuid.uuid4())
        self.by_id[sid] = {
            'id': sid,
            'short_id': sid[:8],
            'name': name,
            'role': role,
            'state': 'blocked',
            'status': 'idle',
            'cwd': CWD,
            **fields,
        }
        return sid

    async def sessions(self) -> list[dict[str, Any]]:
        if self.unreachable:
            raise RunnerUnavailable('The runner is not answering.')
        return [dict(s) for sid, s in self.by_id.items() if sid not in self.unlisted]

    async def stop(self, session_id: str) -> None:
        if self.unreachable:
            raise RunnerUnavailable('The runner is not answering.')
        self.stopped.append(session_id)
        self.die(session_id, 'stopped')

    def die(self, session_id: str, state: str = 'failed') -> None:
        session = self.by_id[session_id]
        session['state'] = state
        session.pop('status', None)

    def busy(self, session_id: str, busy: bool = True) -> None:
        self.by_id[session_id].update(
            {'state': 'working', 'status': 'busy'}
            if busy
            else {'state': 'blocked', 'status': 'idle'}
        )


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.mono = 1000.0

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.mono += seconds


@pytest.fixture
def agent_id(engine: Engine) -> str:
    with Session(engine) as s:
        agent = Agent(owner_user_id='usr_owner', name='Marcel', animal='giraffe', palette='pink',
                      seed='1')  # fmt: skip
        s.add(agent)
        s.commit()
        ensure_main_conversation(s, agent)
        return agent.id


@pytest.fixture
def fake() -> FakeBrains:
    return FakeBrains()


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 10, 12, 10, 0, tzinfo=UTC))


@pytest.fixture
def alerts() -> RecordingAlerts:
    return RecordingAlerts()


def make_keeper(
    engine: Engine,
    fake: FakeBrains | None,
    clock: Clock,
    alerts: RecordingAlerts,
    memory: MemoryRepo | None = None,
) -> BrainKeeper:
    return BrainKeeper(
        engine,
        fake,
        cwd=CWD,
        events=EventLog(engine),
        alerts=alerts,
        memory=memory,
        clock=lambda: clock.now,
        monotonic=lambda: clock.mono,
        tz=BRUSSELS,
    )


@pytest.fixture
def keeper(engine: Engine, fake: FakeBrains, clock: Clock, alerts: RecordingAlerts) -> BrainKeeper:
    return make_keeper(engine, fake, clock, alerts)


def current(engine: Engine, agent_id: str) -> str | None:
    with Session(engine) as s:
        agent = s.get(Agent, agent_id)
        assert agent is not None
        return ensure_main_conversation(s, agent).brain_session_id


def rollovers(engine: Engine) -> list[dict[str, Any]]:
    with Session(engine) as s:
        rows = s.exec(select(AppEvent).where(AppEvent.type == 'brain.rollover')).all()
        frames = [
            {'id': e.id, 'type': e.type, 'at': e.at.isoformat(), 'data': e.data_json} for e in rows
        ]
    for frame in frames:
        APP_EVENT.validate(frame)
    return [f['data'] for f in frames]


def started(keeper: BrainKeeper, engine: Engine, agent_id: str) -> str:
    run(keeper.check())
    sid = current(engine, agent_id)
    assert sid is not None
    return sid


# Keep-alive ---------------------------------------------------------------------------------------


def test_the_first_check_starts_one_brain_and_stores_its_id(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str
) -> None:
    run(keeper.check())
    run(keeper.check())
    assert len(fake.spawned) == 1
    spawn = fake.spawned[0]
    assert spawn['name'] == brain_name(agent_id) and spawn['cwd'] == CWD
    assert spawn['model'] == 'opus'
    assert current(engine, agent_id) in fake.by_id
    assert rollovers(engine) == []  # a first start is not a rollover


@pytest.mark.parametrize('how', ['failed', 'stopped', 'done', 'gone'])
def test_a_dead_brain_is_replaced_and_the_rollover_is_shown(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str, clock: Clock, how: str
) -> None:
    old = started(keeper, engine, agent_id)
    clock.advance(SPAWN_GRACE_S + 1)
    if how == 'gone':
        del fake.by_id[old]
    else:
        fake.die(old, how)  # `done` with no process left (no `status`)
    run(keeper.check())
    new = current(engine, agent_id)
    assert new not in (None, old) and len(fake.spawned) == 2
    assert rollovers(engine) == [
        {
            'agent_id': agent_id,
            'reason': 'crashed',
            'kept': 'The last 0 messages of the past day and 0 open threads.',
        }
    ]
    assert 'the previous one stopped unexpectedly' in fake.spawned[1]['prompt']


@pytest.mark.parametrize(
    'fields',
    [
        {'state': 'working', 'status': 'busy'},
        {'state': 'blocked', 'status': 'waiting'},
        {'state': 'done', 'status': 'idle'},  # between turns, process alive
        {'state': 'something-new'},  # a state the hub does not know is not "dead"
    ],
)
def test_a_live_brain_is_left_alone(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str, clock: Clock, fields: Any
) -> None:
    sid = started(keeper, engine, agent_id)
    clock.advance(SPAWN_GRACE_S + 1)
    fake.by_id[sid].pop('status')
    fake.by_id[sid].update(fields)
    run(keeper.check())
    assert len(fake.spawned) == 1 and current(engine, agent_id) == sid and fake.stopped == []


def test_an_unreachable_runner_starts_nothing(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str, clock: Clock
) -> None:
    sid = started(keeper, engine, agent_id)
    clock.advance(SPAWN_GRACE_S + 1)
    fake.unreachable = True
    del fake.by_id[sid]  # whatever happened, the hub cannot see it
    run(keeper.check())
    assert len(fake.spawned) == 1 and current(engine, agent_id) == sid


def test_a_brain_the_runner_does_not_list_yet_is_not_taken_for_dead(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str, clock: Clock
) -> None:
    sid = started(keeper, engine, agent_id)
    fake.unlisted.add(sid)
    run(keeper.check())
    assert len(fake.spawned) == 1
    clock.advance(SPAWN_GRACE_S + 1)
    run(keeper.check())
    assert len(fake.spawned) == 2  # still not listed after the grace: it is gone


def test_a_burst_of_runner_events_starts_one_brain(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str
) -> None:
    fake.spawn_delay = 0.02  # a real spawn waits for the channel to register

    async def burst() -> None:
        await asyncio.gather(*(keeper.check() for _ in range(5)))

    run(burst())
    assert len(fake.spawned) == 1, 'checks must run one at a time (the keeper lock)'


def test_failed_spawns_back_off_and_raise_one_alert(
    keeper: BrainKeeper,
    fake: FakeBrains,
    engine: Engine,
    agent_id: str,
    clock: Clock,
    alerts: RecordingAlerts,
) -> None:
    fake.fail_spawns = 100
    run(keeper.check())
    run(keeper.check())
    run(keeper.check())
    assert len(fake.spawned) == 1, 'a failed spawn waits before the next try'
    for wait in brain.BACKOFF_S[: ALERT_AFTER - 1]:
        clock.advance(wait - 0.5)
        run(keeper.check())
        clock.advance(0.5)
        run(keeper.check())
    assert len(fake.spawned) == ALERT_AFTER
    assert [cause for cause, _, _ in alerts.raised] == [ALERT]
    _, title, body = alerts.raised[0]
    assert title == 'Marcel cannot start'
    assert 'the channel never registered' in body and 'Your messages are kept' in body
    for wait in brain.BACKOFF_S[ALERT_AFTER - 1 :] + (300.0, 300.0):
        clock.advance(wait)
        run(keeper.check())
    assert len(alerts.raised) == 1, 'one alert, however long it keeps failing'
    assert current(engine, agent_id) is None
    fake.fail_spawns = 0
    clock.advance(300)
    run(keeper.check())
    assert current(engine, agent_id) is not None
    assert alerts.cleared == [ALERT]


def test_without_a_runner_the_keeper_does_nothing(
    engine: Engine, clock: Clock, alerts: RecordingAlerts, agent_id: str
) -> None:
    keeper = make_keeper(engine, None, clock, alerts)
    run(keeper.check())
    keeper.poke()  # not started: nothing to wake
    assert current(engine, agent_id) is None


def test_a_hub_restart_keeps_the_running_brain(
    keeper: BrainKeeper,
    fake: FakeBrains,
    engine: Engine,
    agent_id: str,
    clock: Clock,
    alerts: RecordingAlerts,
) -> None:
    sid = started(keeper, engine, agent_id)
    clock.advance(SPAWN_GRACE_S + 1)
    again = make_keeper(engine, fake, clock, alerts)
    run(again.check())
    assert len(fake.spawned) == 1 and current(engine, agent_id) == sid and fake.stopped == []


def test_an_extra_brain_left_over_is_stopped(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str
) -> None:
    sid = started(keeper, engine, agent_id)
    extra = fake.add(name=brain_name(agent_id))  # e.g. a rollover cut short by a hub restart
    worker = fake.add(name='tsk_1', role='worker')
    side = fake.add(name='side-cnv_1', role='side')
    other = fake.add(name=brain_name('agt_other'))
    run(keeper.check())
    assert fake.stopped == [extra]
    assert current(engine, agent_id) == sid
    assert all(fake.by_id[s]['state'] == 'blocked' for s in (sid, worker, side, other))


def test_the_keeper_runs_on_its_own_and_wakes_on_a_poke(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str, clock: Clock
) -> None:
    async def scenario() -> None:
        keeper.start(tick=3600)  # only pokes wake it
        for _ in range(200):
            if fake.spawned:
                break
            await asyncio.sleep(0.005)
        sid = current(engine, agent_id)
        assert sid is not None
        clock.advance(SPAWN_GRACE_S + 1)
        fake.die(sid)
        keeper.poke()
        for _ in range(200):
            if len(fake.spawned) == 2:
                break
            await asyncio.sleep(0.005)
        await keeper.stop()
        await keeper.stop()  # twice is harmless

    run(scenario())
    assert len(fake.spawned) == 2


def test_one_bad_check_does_not_stop_the_keeper(
    keeper: BrainKeeper, fake: FakeBrains, agent_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    real = keeper.check

    async def flaky() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('boom')
        await real()

    monkeypatch.setattr(keeper, 'check', flaky)

    async def scenario() -> None:
        keeper.start(tick=0.005)
        for _ in range(200):
            if fake.spawned:
                break
            await asyncio.sleep(0.005)
        await keeper.stop()

    run(scenario())
    assert len(calls) >= 2 and fake.spawned


# Rollover ---------------------------------------------------------------------------------------


def test_a_rollover_keeps_the_old_brain_until_the_new_one_says_hello(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str
) -> None:
    old = started(keeper, engine, agent_id)
    keeper.roll_over(agent_id)
    run(keeper.check())
    assert len(fake.spawned) == 2
    new = next(sid for sid in fake.by_id if sid != old)
    # Not yet handed over: the old brain still answers, and nothing is stopped.
    assert current(engine, agent_id) == old and fake.stopped == []
    assert rollovers(engine) == []
    run(keeper.check())
    assert len(fake.spawned) == 2 and fake.stopped == [], 'the waiting one is not an extra'
    assert keeper.said_hello(str(uuid.uuid4())) is None  # someone else's hello
    with Session(engine) as s:
        agent = s.get(Agent, agent_id)
        assert agent is not None
        main_id = agent.main_conversation_id
    assert keeper.said_hello(new) == (agent_id, main_id)
    assert current(engine, agent_id) == new
    assert keeper.said_hello(new) is None  # handed over once
    run(keeper.check())
    assert fake.stopped == [old]
    assert [r['reason'] for r in rollovers(engine)] == ['manual']


def test_the_new_brain_waits_while_the_old_one_is_in_a_turn(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str
) -> None:
    old = started(keeper, engine, agent_id)
    keeper.roll_over(agent_id, 'daily')
    fake.busy(old)
    run(keeper.check())
    new = next(sid for sid in fake.by_id if sid != old)
    assert keeper.said_hello(new) is None, 'the old brain is mid-turn: never two answering'
    assert current(engine, agent_id) == old
    fake.busy(old, False)
    run(keeper.check())  # the keeper sees the old brain between turns
    assert keeper.said_hello(new) is not None
    assert current(engine, agent_id) == new


def test_a_new_brain_takes_over_at_once_when_the_old_one_died(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str, clock: Clock
) -> None:
    old = started(keeper, engine, agent_id)
    keeper.roll_over(agent_id)
    run(keeper.check())
    new = next(sid for sid in fake.by_id if sid != old)
    clock.advance(SPAWN_GRACE_S + 1)
    fake.die(old)
    run(keeper.check())
    assert len(fake.spawned) == 2, 'the waiting brain takes over; no third one'
    assert keeper.said_hello(new) is not None and current(engine, agent_id) == new


def test_a_new_brain_that_never_says_hello_is_stopped_and_the_old_one_stays(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str, clock: Clock
) -> None:
    old = started(keeper, engine, agent_id)
    keeper.roll_over(agent_id, 'daily')
    run(keeper.check())
    new = next(sid for sid in fake.by_id if sid != old)
    clock.advance(HELLO_WAIT_S + 1)
    run(keeper.check())
    assert fake.stopped == [new] and current(engine, agent_id) == old
    assert keeper.said_hello(new) is None
    clock.advance(brain.BACKOFF_S[0])
    run(keeper.check())
    assert len(fake.spawned) == 3, 'the rollover is tried again after the backoff'


def test_a_new_brain_that_dies_before_its_hello_leaves_the_old_one(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str
) -> None:
    old = started(keeper, engine, agent_id)
    keeper.roll_over(agent_id)
    run(keeper.check())
    new = next(sid for sid in fake.by_id if sid != old)
    fake.die(new)
    run(keeper.check())
    assert current(engine, agent_id) == old and fake.stopped == []
    assert keeper.said_hello(new) is None


def test_a_failed_rollover_spawn_keeps_the_old_brain(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str
) -> None:
    old = started(keeper, engine, agent_id)
    fake.fail_spawns = 1
    keeper.roll_over(agent_id)
    run(keeper.check())
    assert current(engine, agent_id) == old and len(fake.spawned) == 2


def test_a_crash_respawn_counts_as_the_fresh_start(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str, clock: Clock
) -> None:
    old = started(keeper, engine, agent_id)
    clock.advance(SPAWN_GRACE_S + 1)
    fake.die(old)
    keeper.roll_over(agent_id)
    run(keeper.check())
    run(keeper.check())
    assert len(fake.spawned) == 2  # no second, rollover brain on top of the respawn


def test_an_old_brain_that_cannot_be_stopped_yet_is_stopped_later(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str
) -> None:
    old = started(keeper, engine, agent_id)
    keeper.roll_over(agent_id)
    run(keeper.check())
    new = next(sid for sid in fake.by_id if sid != old)
    assert keeper.said_hello(new) is not None
    real_stop = fake.stop

    async def refuse(session_id: str) -> None:
        raise RunnerUnavailable('The runner is busy.')

    fake.stop = refuse  # type: ignore[method-assign]
    run(keeper.check())
    assert fake.stopped == []
    fake.stop = real_stop  # type: ignore[method-assign]
    run(keeper.check())
    assert fake.stopped == [old]


def test_the_nightly_rollover_is_at_four_in_the_owner_time_zone(
    engine: Engine, fake: FakeBrains, alerts: RecordingAlerts, agent_id: str
) -> None:
    clock = Clock(datetime(2026, 10, 12, 1, 0, tzinfo=UTC))  # 03:00 in Brussels (CEST)
    keeper = make_keeper(engine, fake, clock, alerts)
    old = started(keeper, engine, agent_id)
    clock.advance(59 * 60)  # 03:59
    run(keeper.check())
    assert len(fake.spawned) == 1
    clock.advance(60)  # 04:00
    run(keeper.check())
    assert len(fake.spawned) == 2
    new = next(sid for sid in fake.by_id if sid != old)
    assert keeper.said_hello(new) is not None
    assert [r['reason'] for r in rollovers(engine)] == ['daily']
    assert 'the daily fresh start' in fake.spawned[1]['prompt']
    clock.advance(23 * 3600)  # 03:00 the next day: not yet
    run(keeper.check())
    assert len(fake.spawned) == 2


@pytest.mark.parametrize(
    ('after', 'due'),
    [
        # An ordinary day: 04:00 CEST is 02:00 UTC.
        (datetime(2026, 10, 12, 1, 0, tzinfo=UTC), datetime(2026, 10, 12, 2, 0, tzinfo=UTC)),
        (datetime(2026, 10, 12, 2, 0, tzinfo=UTC), datetime(2026, 10, 13, 2, 0, tzinfo=UTC)),
        # Clocks go back on 25 October 2026: that night's 04:00 is CET, 03:00 UTC.
        (datetime(2026, 10, 24, 12, 0, tzinfo=UTC), datetime(2026, 10, 25, 3, 0, tzinfo=UTC)),
        # Clocks go forward on 29 March 2026: 04:00 CEST again, 02:00 UTC.
        (datetime(2026, 3, 28, 12, 0, tzinfo=UTC), datetime(2026, 3, 29, 2, 0, tzinfo=UTC)),
    ],
)
def test_the_next_rollover_follows_the_wall_clock_across_clock_changes(
    after: datetime, due: datetime
) -> None:
    assert next_rollover(after, BRUSSELS) == due


def test_the_next_rollover_without_a_zone_uses_the_local_one() -> None:
    after = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)
    due = next_rollover(after)
    assert due > after and due - after <= timedelta(days=1)
    assert due.astimezone().hour == 4 and due.astimezone().minute == 0


# The seed -----------------------------------------------------------------------------------------


def test_the_seed_is_built_from_the_hubs_own_records(
    engine: Engine,
    fake: FakeBrains,
    clock: Clock,
    alerts: RecordingAlerts,
    agent_id: str,
    tmp_path: Path,
) -> None:
    memory = MemoryRepo(tmp_path / 'memory')
    mine = memory.root / 'agents' / agent_id.lower()  # this agent's own memory (S-17.4)
    mine.mkdir(parents=True)
    (mine / 'MEMORY.md').write_text('# Notes\n\nThe kids eat at 18:00.\n')
    now = clock.now
    with Session(engine) as s:
        main = s.exec(select(Conversation).where(Conversation.agent_id == agent_id)).one()
        project = Project(agent_id=agent_id, slug='house', name='House')
        s.add(project)
        s.flush()

        def say(role: MessageRole, kind: MessageKind, body: dict[str, Any], ago: float) -> None:
            s.add(Message(conversation_id=main.id, role=role, kind=kind, body_json=body,
                          created_at=now - timedelta(hours=ago)))  # fmt: skip

        say(MessageRole.user, MessageKind.text, {'text': 'Too old to keep'}, 30)
        say(MessageRole.user, MessageKind.text, {'text': 'What is for dinner?'}, 2)
        say(MessageRole.agent, MessageKind.text, {'text': 'Pasta.\n  With  peas.'}, 1.9)
        say(
            MessageRole.agent,
            MessageKind.milestone,
            {'milestone': {'task_title': 'Fix the tests', 'event': 'done', 'summary': 'Green.'}},
            1,
        )
        say(MessageRole.agent, MessageKind.approval, {'approval_id': 'apr_1'}, 0.5)
        say(MessageRole.system, MessageKind.text, {'text': 'x' * 1000}, 0.4)
        s.add(Message(conversation_id=main.id, role=MessageRole.agent, kind=MessageKind.artifact,
                      body_json={}, artifact_id='art_1',
                      created_at=now - timedelta(hours=0.3)))  # fmt: skip
        for title, state, project_id in [
            ('Back up the NAS', TaskState.working, project.id),
            ('Plan the trip', TaskState.needs_you, None),
            ('Fix the tests', TaskState.done, None),
        ]:
            s.add(Task(agent_id=agent_id, title=title, location=TaskLocation.nuc, state=state,
                       model='sonnet', project_id=project_id,
                       origin=TaskOrigin.marcel))  # fmt: skip
        s.commit()
    keeper = make_keeper(engine, fake, clock, alerts, memory=memory)
    prompt, kept = keeper.seed(agent_id, 'daily')
    assert kept == 'Your notes, the last 6 messages of the past day and 2 open threads.'
    assert 'The kids eat at 18:00.' in prompt
    assert 'Too old to keep' not in prompt
    assert '- 10:00 Owner: What is for dinner?' in prompt  # 08:00 UTC is 10:00 in Brussels
    assert 'Marcel: Pasta. With peas.' in prompt
    assert 'Report from thread "Fix the tests": [done] Green.' in prompt
    assert 'Marcel hub: [done]' not in prompt and 'Marcel: [done]' not in prompt
    assert '[an approval card]' in prompt and '[an item arrived: art_1]' in prompt
    assert 'Marcel hub: ' + 'x' * 399 + '…' in prompt
    assert '- Back up the NAS — working, on nuc, project house (task ' in prompt
    assert '- Plan the trip — needs_you, on nuc, project inbox (task ' in prompt
    assert '- Fix the tests — done' not in prompt


def test_a_long_memory_is_cut_and_says_so(
    engine: Engine,
    fake: FakeBrains,
    clock: Clock,
    alerts: RecordingAlerts,
    agent_id: str,
    tmp_path: Path,
) -> None:
    memory = MemoryRepo(tmp_path / 'memory')
    mine = memory.root / 'agents' / agent_id.lower()  # this agent's own memory (S-17.4)
    mine.mkdir(parents=True)
    (mine / 'MEMORY.md').write_text('a' * (brain.MAX_MEMORY + 10))
    prompt, kept = make_keeper(engine, fake, clock, alerts, memory=memory).seed(agent_id, None)
    assert 'read MEMORY.md for the rest' in prompt and 'the first start' in prompt
    assert 'Nothing was said.' in prompt
    assert kept == 'Your notes, the last 0 messages of the past day and 0 open threads.'


def test_a_missing_or_unreadable_memory_leaves_the_notes_out(
    engine: Engine,
    fake: FakeBrains,
    clock: Clock,
    alerts: RecordingAlerts,
    agent_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = MemoryRepo(tmp_path / 'memory')
    memory.root.mkdir()
    keeper = make_keeper(engine, fake, clock, alerts, memory=memory)
    assert '## Your notes' not in keeper.seed(agent_id, None)[0]

    def broken(*_: Any) -> None:
        raise OSError('disk on fire')

    monkeypatch.setattr(memory, 'read_slice', broken)
    prompt, kept = keeper.seed(agent_id, 'crashed')
    assert '## Your notes' not in prompt and kept.startswith('The last 0 messages')


# A busy old brain (review fix 2) and the hub's own turn tracking (review fix 1) -------------------


def test_a_busy_old_brain_puts_the_rollover_off_then_skips_it_without_an_alert(
    keeper: BrainKeeper,
    fake: FakeBrains,
    engine: Engine,
    agent_id: str,
    clock: Clock,
    alerts: RecordingAlerts,
    caplog: pytest.LogCaptureFixture,
) -> None:
    old = started(keeper, engine, agent_id)
    fake.busy(old)  # a turn that does not end
    keeper.roll_over(agent_id, 'daily')
    for attempt in range(brain.BUSY_TRIES):
        run(keeper.check())
        assert len(fake.spawned) == 2 + attempt
        new = fake.spawned and next(s for s in fake.by_id if s not in (old, *fake.stopped))
        assert keeper.said_hello(new) is None
        clock.advance(HELLO_WAIT_S + 1)
        run(keeper.check())
        assert new in fake.stopped and current(engine, agent_id) == old
        run(keeper.check())
        assert len(fake.spawned) == 2 + attempt, 'no new brain straight away'
        clock.advance(brain.BUSY_RETRY_S)
    for _ in range(5):
        clock.advance(brain.BUSY_RETRY_S)
        run(keeper.check())
    assert len(fake.spawned) == 1 + brain.BUSY_TRIES, 'skipped after the last try'
    assert alerts.raised == [], 'a busy brain is not a failed start'
    assert 'Skipped the daily rollover' in caplog.text


def test_the_hubs_turn_tracking_holds_the_hand_over(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str
) -> None:
    old = started(keeper, engine, agent_id)
    keeper.roll_over(agent_id)
    run(keeper.check())
    new = next(sid for sid in fake.by_id if sid != old)
    asked: list[tuple[str, str]] = []
    open_turn = True

    def turn_open(agent: str, conversation: str) -> bool:
        asked.append((agent, conversation))
        return open_turn

    keeper.turn_open = turn_open
    assert keeper.said_hello(new) is None, 'the runner says idle, but a turn is open'
    assert asked and asked[0][0] == agent_id
    open_turn = False
    assert keeper.said_hello(new) is not None


def test_the_hand_over_is_sent_once_the_new_brain_holds_the_conversation(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str, clock: Clock
) -> None:
    old = started(keeper, engine, agent_id)
    keeper.roll_over(agent_id)
    seeded = clock.now
    run(keeper.check())
    new = next(sid for sid in fake.by_id if sid != old)
    handed: list[tuple[str, datetime]] = []
    keeper.welcomed(new)  # no hand-over set up: nothing happens
    keeper.hand_over = lambda conversation, since: handed.append((conversation, since))
    conversation = keeper.said_hello(new)
    assert conversation is not None and handed == []  # not before its socket is attached
    keeper.welcomed(old)  # someone else
    keeper.welcomed(new)
    keeper.welcomed(new)  # once
    assert handed == [(conversation[1], seeded)]


def test_a_failing_hand_over_is_logged(
    keeper: BrainKeeper,
    fake: FakeBrains,
    engine: Engine,
    agent_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    old = started(keeper, engine, agent_id)
    keeper.roll_over(agent_id)
    run(keeper.check())
    new = next(sid for sid in fake.by_id if sid != old)

    def broken(conversation: str, since: datetime) -> None:
        raise RuntimeError('outbox on fire')

    keeper.hand_over = broken
    assert keeper.said_hello(new) is not None
    keeper.welcomed(new)
    assert 'Could not send the new main session the latest messages.' in caplog.text
    assert current(engine, agent_id) == new
