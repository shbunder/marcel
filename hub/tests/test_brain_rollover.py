"""Rollover by context size, on request, and its history (S-06.7).

- A brain turn past 70 % of its context window rolls it over once (`BrainKeeper.turn_ended`),
  read from the brain's transcript (`tail_turn_ends`).
- `POST /api/agents/{id}/brain/rollover` asks for one (202, 409 while one runs, 503 without a
  runner); `GET /api/agents/{id}/brain/rollovers` lists past ones from their own table.
- A `reply` refused with `replaced` reaches the new brain once.

Nothing here reaches a real `claude`, a runner or the network."""

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, select
from test_brain import BRUSSELS, CWD, Clock, FakeBrains, current, rollovers, started
from test_brain_app import answering, brain_hub, keeper_of, wait
from test_brain_app import current as current_in
from test_channel_fakes import ChannelHub
from test_runner_client_fakes import RecordingAlerts
from test_tasks_fakes import run, validator

from marcel_hub import brain
from marcel_hub.app import Settings, SettingsError, create_app, load_settings
from marcel_hub.brain import (
    CONTEXT_WINDOW,
    LONG_CONTEXT,
    BrainKeeper,
    context_tokens,
    context_window,
    tail_turn_ends,
)
from marcel_hub.channel.gateway import Peer
from marcel_hub.channel.outbox import conversation_target
from marcel_hub.conversations import ensure_main_conversation
from marcel_hub.models import Agent, AppEvent, BrainRollover
from marcel_hub.runner_client import StreamRefused
from marcel_hub.tasks.ports import RunnerUnavailable
from marcel_hub.ws import EventLog

ROLLOVER = validator('app-api.yaml#/components/schemas/Rollover')
OLD = '0f6c1a2e-0000-4000-8000-000000000001'


def usage(tokens: int) -> dict[str, Any]:
    """A `turn_end` whose context is `tokens` (mostly cache reads, as a long session's is)."""
    return {
        'duration_ms': 4200,
        'usage': {
            'input_tokens': 2,
            'cache_creation_input_tokens': 700,
            'cache_read_input_tokens': tokens - 1000,
            'output_tokens': 298,
        },
    }


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


def make_keeper(engine: Engine, fake: FakeBrains, clock: Clock, **kw: Any) -> BrainKeeper:
    return BrainKeeper(
        engine,
        fake,
        cwd=CWD,
        events=EventLog(engine),
        alerts=RecordingAlerts(),
        clock=lambda: clock.now,
        monotonic=lambda: clock.mono,
        tz=BRUSSELS,
        **kw,
    )


@pytest.fixture
def keeper(engine: Engine, fake: FakeBrains, clock: Clock) -> BrainKeeper:
    return make_keeper(engine, fake, clock)


def history(engine: Engine) -> list[BrainRollover]:
    with Session(engine) as s:
        return list(s.exec(select(BrainRollover)))


# By context size --------------------------------------------------------------------------------


def test_a_turn_past_70_percent_rolls_over_once(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str
) -> None:
    old = started(keeper, engine, agent_id)
    assert keeper.turn_ended(old, usage(139_999)) is False, '69.99 % is not full'
    run(keeper.check())
    assert len(fake.spawned) == 1
    assert keeper.turn_ended(old, usage(140_001)) is True
    run(keeper.check())
    assert len(fake.spawned) == 2
    assert 'the previous one ran out of room' in fake.spawned[1]['prompt']
    new = next(sid for sid in fake.by_id if sid != old)
    # The old brain answers more turns while the new one starts: no second rollover is asked.
    assert keeper.turn_ended(old, usage(150_000)) is False
    assert keeper.said_hello(new) is not None
    run(keeper.check())
    # After the hand-over, a late turn of the old brain counts for nothing either.
    assert keeper.turn_ended(old, usage(190_000)) is False
    run(keeper.check())
    run(keeper.check())
    assert len(fake.spawned) == 2, 'rolled over more than once'
    assert current(engine, agent_id) == new
    assert [r['reason'] for r in rollovers(engine)] == ['context_full']


def test_the_context_window_follows_the_model_and_the_setting(
    engine: Engine, fake: FakeBrains, clock: Clock, agent_id: str
) -> None:
    assert context_window('opus') == CONTEXT_WINDOW == 200_000
    assert context_window('claude-opus-4-6[1m]') == LONG_CONTEXT
    assert context_window('sonnet', 500_000) == 500_000
    keeper = make_keeper(engine, fake, clock, window=1_000_000)
    sid = started(keeper, engine, agent_id)
    assert keeper.turn_ended(sid, usage(600_000)) is False
    assert keeper.turn_ended(sid, usage(700_000)) is True
    with Session(engine) as s:
        agent = s.get(Agent, agent_id)
        assert agent is not None
        agent.model_brain = 'opus[1m]'
        s.add(agent)
        s.commit()
    small = make_keeper(engine, fake, clock)  # 200 000 by default, but the model has 1M
    assert small.turn_ended(sid, usage(300_000)) is False


def test_a_turn_without_usage_or_from_another_session_changes_nothing(
    keeper: BrainKeeper, engine: Engine, agent_id: str
) -> None:
    sid = started(keeper, engine, agent_id)
    assert keeper.turn_ended(sid, {'duration_ms': 10}) is False
    assert keeper.turn_ended('a-worker-session', usage(199_000)) is False
    assert context_tokens(None) is None
    assert context_tokens({'input_tokens': 1}) is None
    assert context_tokens({**usage(5000)['usage'], 'output_tokens': -1}) is None
    assert context_tokens({**usage(5000)['usage'], 'output_tokens': True}) is None
    assert context_tokens(usage(5000)['usage']) == 5000
    assert not keeper.rolling_over(agent_id)


def test_the_keeper_reads_the_current_brains_turns(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str
) -> None:
    read: list[str] = []

    async def turns(session_id: str) -> AsyncIterator[dict[str, Any]]:
        read.append(session_id)
        yield usage(10_000)
        yield usage(160_000)

    keeper.turn_ends = turns

    async def scenario() -> None:
        await keeper.check()
        for _ in range(5):
            await asyncio.sleep(0)
        await keeper.check()  # the reader is still the current brain's: not started twice
        await keeper.stop()

    run(scenario())
    sid = current(engine, agent_id)
    assert read == [sid]
    assert keeper.rolling_over(agent_id)


def test_a_reader_that_breaks_is_logged_and_started_again(
    keeper: BrainKeeper, engine: Engine, agent_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    tries: list[str] = []

    async def turns(session_id: str) -> AsyncIterator[dict[str, Any]]:
        tries.append(session_id)
        raise RuntimeError('the stream fell over')
        yield {}  # pragma: no cover

    keeper.turn_ends = turns

    async def scenario() -> None:
        await keeper.check()
        await asyncio.sleep(0)
        await keeper.check()
        await asyncio.sleep(0)
        keeper._follow(agent_id, None)  # no brain: no reader
        assert keeper._tails == {}

    run(scenario())
    assert len(tries) == 2
    assert 'Stopped reading how full main session' in caplog.text


# Reading the brain's transcript -----------------------------------------------------------------


def line(offset: int, type_: str, data: dict[str, Any], **extra: Any) -> list[str]:
    event = {
        'session_id': OLD,
        'offset': offset,
        'at': '2026-10-12T10:00:00Z',
        'type': type_,
        'data': data,
        **extra,
    }
    return [f'id: {offset}', 'event: transcript', f'data: {json.dumps(event)}', '']


class Stream:
    def __init__(self, lines: list[str], breaks: bool = False) -> None:
        self.lines = lines
        self.breaks = breaks
        self.closed = False

    async def aiter_lines(self) -> AsyncIterator[str]:
        for item in self.lines:
            yield item
        if self.breaks:
            raise httpx.ReadError('connection reset')

    async def aclose(self) -> None:
        self.closed = True


class TailFake:
    """The runner's `/sessions/{id}/tail`, as a script of answers."""

    def __init__(self, answers: list[Stream | Exception]) -> None:
        self.answers = answers
        self.asked: list[dict[str, Any]] = []

    async def open_stream(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Stream:
        assert path == f'/sessions/{OLD}/tail'
        self.asked.append(dict(params or {}))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_the_tail_starts_from_the_latest_turn_then_follows_the_new_ones() -> None:
    caught_up = Stream(
        line(100, 'turn_end', usage(20_000))
        + line(200, 'text', {'role': 'assistant', 'text': 'hi'})
        + line(300, 'turn_end', usage(90_000))
        + line(300, 'turn_end', usage(1), subagent_id='a1b2')  # a subagent's: never counts
        + ['data: {not json', '']
    )
    following = Stream(line(400, 'turn_end', usage(120_000)), breaks=True)
    fake = TailFake(
        [
            caught_up,
            following,
            StreamRefused(503, 'The runner could not ask claude just now.'),
            RunnerUnavailable('The runner is not answering.'),
            Stream(line(500, 'turn_end', usage(130_000))),
            StreamRefused(404, 'No such session.'),
        ]
    )
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    async def read() -> list[int]:
        found = [context_tokens(d['usage']) async for d in tail_turn_ends(fake, OLD, sleep=sleep)]
        return [t for t in found if t is not None]

    assert run(read()) == [90_000, 120_000, 130_000]
    assert fake.asked == [
        {'offset': 0, 'follow': 'false'},
        {'offset': 300, 'follow': 'true'},
        {'offset': 400, 'follow': 'true'},
        {'offset': 400, 'follow': 'true'},
        {'offset': 400, 'follow': 'true'},
        {'offset': 500, 'follow': 'true'},
    ]
    assert caught_up.closed and following.closed
    assert waits == [1.0, 2.0, 4.0, 1.0]  # a stream that gave events starts a new backoff


def test_the_tail_of_an_empty_transcript_just_follows() -> None:
    fake = TailFake([Stream([]), StreamRefused(400, 'Bad request.')])

    async def read() -> list[dict[str, Any]]:
        return [d async for d in tail_turn_ends(fake, OLD)]

    assert run(read()) == []
    assert fake.asked == [{'offset': 0, 'follow': 'false'}, {'offset': 0, 'follow': 'true'}]


# History ------------------------------------------------------------------------------------------


def test_every_rollover_is_kept_with_its_sessions(
    keeper: BrainKeeper, fake: FakeBrains, engine: Engine, agent_id: str, clock: Clock
) -> None:
    first = started(keeper, engine, agent_id)
    assert history(engine) == [], 'the first start is not a rollover'
    fake.die(first)
    second = started(keeper, engine, agent_id)
    clock.advance(60)
    assert keeper.roll_over(agent_id, 'manual')
    assert not keeper.roll_over(agent_id, 'daily'), 'one at a time'
    run(keeper.check())
    third = next(sid for sid in fake.by_id if sid not in (first, second))
    assert keeper.said_hello(third) is not None
    rows = sorted(history(engine), key=lambda r: r.at)
    assert [(r.reason, r.old_session_id, r.new_session_id) for r in rows] == [
        ('crashed', first, second),
        ('manual', second, third),
    ]
    assert rows[1].kept == rollovers(engine)[1]['kept']
    keeper.events = None  # history is kept even without the app's event log
    fake.die(third)
    run(keeper.check())
    assert len(history(engine)) == 3


# The app's routes -------------------------------------------------------------------------------


@pytest.fixture
def hub(tmp_path: Path, fake: FakeBrains, monkeypatch: pytest.MonkeyPatch) -> Iterator[ChannelHub]:
    monkeypatch.setattr(brain, 'TICK_S', 0.05)
    with brain_hub(tmp_path, fake) as hub:
        yield hub


def test_a_manual_rollover_is_202_then_409_while_it_runs(hub: ChannelHub, fake: FakeBrains) -> None:
    old = wait(lambda: current_in(hub))
    url = f'/api/agents/{hub.agent_id}/brain/rollover'
    assert hub.client.post(url).status_code == 202
    again = hub.client.post(url)
    assert again.status_code == 409
    assert again.json()['code'] == 'rollover_running'
    assert 'already starting a fresh session' in again.json()['message']
    wait(lambda: len(fake.spawned) == 2)
    assert hub.client.post(url).status_code == 409, 'waiting for its hello'
    new = next(sid for sid in fake.by_id if sid != old)
    with hub.connect() as ch:
        assert ch.hello(new)['type'] == 'welcome'
        wait(lambda: answering(hub) == new)
    assert [e['data']['reason'] for e in hub.events('brain.rollover')] == ['manual']
    assert hub.client.post(url).status_code == 202, 'a new one once that is done'


def test_a_manual_rollover_needs_a_runner_and_an_agent_of_yours(
    hub: ChannelHub, fake: FakeBrains
) -> None:
    wait(lambda: current_in(hub))
    fake.unreachable = True
    res = hub.client.post(f'/api/agents/{hub.agent_id}/brain/rollover')
    assert res.status_code == 503 and res.json()['code'] == 'runner_unreachable'
    assert 'Nothing changed' in res.json()['message']
    assert not keeper_of(hub).rolling_over(hub.agent_id)
    for path in ('rollover', 'rollovers'):
        method = hub.client.post if path == 'rollover' else hub.client.get
        res = method(f'/api/agents/agt_nobody/brain/{path}')
        assert res.status_code == 404 and res.json()['code'] == 'agent_not_found'
        other = hub.phone('usr_someone_else')
        res = method(
            f'/api/agents/{hub.agent_id}/brain/{path}',
            headers={'Authorization': f'Bearer {other}'},
        )
        assert res.status_code == 404


def test_without_a_brain_workspace_a_manual_rollover_is_503(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db'))
    with TestClient(app) as client:
        hub = ChannelHub(client, tmp_path)
        res = client.post(f'/api/agents/{hub.agent_id}/brain/rollover')
    assert res.status_code == 503 and res.json()['code'] == 'runner_unreachable'
    assert 'not set up' in res.json()['message']


def test_past_rollovers_are_listed_newest_first_after_2000_later_events(
    hub: ChannelHub, fake: FakeBrains
) -> None:
    url = f'/api/agents/{hub.agent_id}/brain/rollovers'
    assert hub.client.get(url).json() == []
    first = wait(lambda: current_in(hub))
    fake.die(first)
    second = wait(lambda: (sid := current_in(hub)) != first and sid)
    fake.die(second)
    wait(lambda: current_in(hub) not in (first, second))
    filler = EventLog(hub.engine)  # the app's log, without its observers
    with Session(hub.engine) as s:
        owner = s.get(Agent, hub.agent_id)
        assert owner is not None
        for i in range(2100):  # more than the event log keeps
            filler.publish(s, owner.owner_user_id, 'filler', {'i': i})
        left = s.exec(select(AppEvent).where(AppEvent.type == 'brain.rollover')).all()
    assert left == [], 'the event log has moved on'
    listed = hub.client.get(url).json()
    assert [r['reason'] for r in listed] == ['crashed', 'crashed']
    for item in listed:
        ROLLOVER.validate(item)
        assert item['agent_id'] == hub.agent_id and item['at'].endswith('Z')
    assert listed[0]['at'] >= listed[1]['at']
    assert listed[0]['id'] > listed[1]['id'], 'newest first'
    assert [r['id'] for r in hub.client.get(url, params={'limit': 1}).json()] == [listed[0]['id']]
    assert hub.client.get(url, params={'limit': 51}).status_code == 400
    assert hub.client.get(url, params={'limit': 0}).status_code == 400


def test_the_context_window_is_a_setting(tmp_path: Path) -> None:
    base = {'MARCEL_CONFIG': str(tmp_path / 'none.toml')}
    assert load_settings(base).brain_context_window == 200_000
    env = {**base, 'MARCEL_BRAIN_CONTEXT_WINDOW': '1000000'}
    assert load_settings(env).brain_context_window == 1_000_000
    for bad in ('lots', '500'):
        with pytest.raises(SettingsError, match='brain_context_window'):
            load_settings({**base, 'MARCEL_BRAIN_CONTEXT_WINDOW': bad})
    app = create_app(
        Settings(
            data_dir=tmp_path,
            db_path=tmp_path / 'marcel.db',
            brain_cwd=CWD,
            runner_sock=tmp_path / 'runner.sock',
            brain_context_window=300_000,
        )
    )
    keeper: BrainKeeper = app.state.brain
    assert keeper.window == 300_000
    assert keeper.turn_ends is not None, 'with a runner the keeper reads the brain transcript'


# No lost answer ---------------------------------------------------------------------------------


def test_a_replaced_reply_reaches_the_new_brain_once(hub: ChannelHub, fake: FakeBrains) -> None:
    old = wait(lambda: current_in(hub))
    assert keeper_of(hub).roll_over(hub.agent_id)
    wait(lambda: len(fake.spawned) == 2)
    new = next(sid for sid in fake.by_id if sid != old)
    with hub.connect() as ch:
        assert ch.hello(new)['type'] == 'welcome'
        late = {
            'type': 'reply',
            'call_id': 'c_late',
            'text': 'The pasta needs 12 minutes.',
            'in_reply_to': 'msg_01',
        }
        peer = Peer(old, 'brain', hub.agent_id, conversation_id=hub.main_id)
        for _ in range(2):  # a re-sent copy is refused again, but passed on once
            ack = asyncio.run(hub.gateway.handle(peer, late))
            assert ack[0]['error']['code'] == 'replaced'
        queued = hub.gateway.outbox.pending(conversation_target(hub.main_id))
        assert [f['kind'] for f in queued] == ['rollover'], 'the unsent reply, once'
        frame = ch.deliver()
        assert frame['type'] == 'system_event' and frame['kind'] == 'rollover'
        assert frame['data'] == {
            'unsent_reply': 'The pasta needs 12 minutes.',
            'in_reply_to': 'msg_01',
        }
        reply = ch.call('reply', call_id='c_new', text='About 12 minutes.')
        assert reply == {'type': 'call_ack', 'call_id': 'c_new'}, 'nothing else was queued'
