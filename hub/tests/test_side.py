"""Side threads (S-06.3) against a fake runner: fork on open, idle stop, resume or re-fork, the
cap on running forks, failures said in the thread, and a brain rollover that leaves forks alone.

Nothing here reaches a real `claude`, a runner or the network: `FakeSides` answers the runner calls
from memory."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select
from test_brain import Clock, FakeBrains, make_keeper
from test_runner_client_fakes import RecordingAlerts
from test_tasks_fakes import run

from marcel_hub import side
from marcel_hub.channel.outbox import ChannelOutbox, conversation_target
from marcel_hub.conversations import ensure_main_conversation, post_message
from marcel_hub.models import (
    Agent,
    Conversation,
    ConversationKind,
    Message,
    MessageKind,
    MessageRole,
)
from marcel_hub.runner_client import RunnerClient
from marcel_hub.side import IDLE_S, MAX_SIDES, TRIES, SideThreads, side_name
from marcel_hub.tasks.ports import RunnerUnavailable, StartFailed
from marcel_hub.ws import EventLog


class FakeSides(FakeBrains):
    """The runner, as side threads see it: the brain keeper's calls plus fork and resume."""

    def __init__(self) -> None:
        super().__init__()
        self.forked: list[dict[str, Any]] = []
        self.resumed: list[str] = []
        self.fail_forks = 0
        self.fork_unavailable = False
        self.gone: set[str] = set()  # sessions `resume` cannot find any more

    async def fork(self, session_id: str, *, name: str, prompt: str) -> str:
        self.forked.append({'from': session_id, 'name': name, 'prompt': prompt})
        if self.fork_unavailable:
            raise RunnerUnavailable('The runner is not answering.')
        if self.fail_forks:
            self.fail_forks -= 1
            raise StartFailed('The fork did not start: the channel never registered.')
        return self.add(name=name, role='side')

    async def resume(self, session_id: str, prompt: str) -> None:
        self.resumed.append(session_id)
        if session_id in self.gone or session_id not in self.by_id:
            raise StartFailed('That session is not on this machine.')
        self.by_id[session_id].update(state='working', status='busy')


class World:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.fake = FakeSides()
        self.clock = Clock(datetime.now(UTC))
        self.events = EventLog(engine)
        self.outbox = ChannelOutbox(engine)
        self.sides = SideThreads(
            engine,
            self.fake,
            events=self.events,
            outbox=self.outbox,
            clock=lambda: self.clock.now,
            monotonic=lambda: self.clock.mono,
        )
        self.events.observers.append(self.sides.observe)
        with Session(engine) as s:
            agent = Agent(owner_user_id='usr_owner', name='Marcel', animal='giraffe',
                          palette='pink', seed='1')  # fmt: skip
            s.add(agent)
            s.commit()
            self.agent_id = agent.id
            self.main_id = ensure_main_conversation(s, agent).id
        self.brain = self.set_brain(self.fake.add(name=f'brain-{self.agent_id}'))

    def set_brain(self, sid: str) -> str:
        with Session(self.engine) as s:
            conv = s.get(Conversation, self.main_id)
            assert conv is not None
            conv.brain_session_id = sid
            s.add(conv)
            s.commit()
        return sid

    def post(self, conversation_id: str, text: str, role: MessageRole = MessageRole.user) -> str:
        with Session(self.engine) as s:
            conv = s.get(Conversation, conversation_id)
            assert conv is not None
            msg = post_message(
                s, self.events, conv, role=role, kind=MessageKind.text, body={'text': text}
            )
            msg.created_at = self.clock.now  # on the test's clock, like the hub's own records
            s.add(msg)
            s.commit()
            return msg.id

    def open(self, text: str = 'What should we cook tonight?', task_id: str | None = None) -> str:
        """Open a side thread on a new main message, as `POST /messages/{id}/thread` does."""
        parent = self.post(self.main_id, text)
        with Session(self.engine) as s:
            conv = Conversation(
                agent_id=self.agent_id,
                kind=ConversationKind.side,
                parent_message_id=parent,
                task_id=task_id,
            )
            s.add(conv)
            s.commit()
            data = {'id': conv.id, 'agent_id': self.agent_id, 'kind': 'side'}
            if task_id:
                data['task_id'] = task_id
            self.sides.observe('usr_owner', 'conversation.created', data)
            return conv.id

    def session_of(self, conversation_id: str) -> str | None:
        with Session(self.engine) as s:
            conv = s.get(Conversation, conversation_id)
            assert conv is not None
            return conv.brain_session_id

    def check(self) -> None:
        run(self.sides.check())

    def opened(self, text: str = 'What should we cook tonight?') -> tuple[str, str]:
        cid = self.open(text)
        self.check()
        sid = self.session_of(cid)
        assert sid is not None
        self.fake.busy(sid, False)  # its first turn is over
        return cid, sid

    def notes(self, conversation_id: str) -> list[str]:
        with Session(self.engine) as s:
            rows = s.exec(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .where(Message.role == MessageRole.system)
                .order_by(col(Message.created_at))
            )
            return [m.body_json['text'] for m in rows]

    def deliver_all(self, conversation_id: str) -> None:
        """The side session took every frame waiting for it."""
        target = conversation_target(conversation_id)
        for frame in self.outbox.pending(target):
            self.outbox.ack(target, frame['seq'])


@pytest.fixture
def w(engine: Engine) -> World:
    return World(engine)


# Opening a thread forks the brain -----------------------------------------------------------------


def test_opening_a_side_thread_forks_the_brain_and_stores_the_fork_on_the_thread(w: World) -> None:
    cid = w.open('What should we cook tonight?')
    assert w.sides.wanted() == [cid]
    w.check()
    [fork] = w.fake.forked
    assert fork['from'] == w.brain, 'a side thread is a fork of the main brain'
    assert fork['name'] == side_name(cid) == f'side-{cid}'
    assert 'What should we cook tonight?' in fork['prompt']
    assert 'side thread' in fork['prompt']
    sid = w.session_of(cid)
    assert sid is not None and sid != w.brain
    assert w.fake.by_id[sid]['role'] == 'side'
    assert w.session_of(w.main_id) == w.brain, 'the fork never replaces the main brain'
    assert w.sides.wanted() == []
    w.check()
    assert len(w.fake.forked) == 1, 'a running fork is not forked again'


def test_a_task_thread_is_never_forked(w: World) -> None:
    cid = w.open('Check disk usage started', task_id='tsk_01')
    assert w.sides.wanted() == []
    w.post(cid, 'Use df, not du')
    w.sides.want(cid)  # even when asked directly
    w.check()
    assert w.fake.forked == [] and w.sides.wanted() == []


def test_messages_in_the_main_conversation_and_hub_notes_start_nothing(w: World) -> None:
    w.post(w.main_id, 'Hello')
    cid = w.open()
    w.check()
    w.post(cid, 'A note', role=MessageRole.system)
    w.sides.observe('usr_owner', 'message.created', {'role': 'user', 'kind': 'approval'})
    assert w.sides.wanted() == []


def test_a_message_in_a_thread_whose_fork_runs_starts_nothing(w: World) -> None:
    cid, _ = w.opened()
    w.post(cid, 'And for dessert?')
    w.check()
    assert len(w.fake.forked) == 1 and w.fake.resumed == []


# Idle sessions --------------------------------------------------------------------------------


def test_an_idle_side_session_is_stopped_after_thirty_minutes_and_keeps_its_id(w: World) -> None:
    cid, sid = w.opened()
    w.clock.advance(IDLE_S - 60)
    w.check()
    assert w.fake.stopped == [], 'stopped before 30 minutes'
    w.clock.advance(61)
    w.check()
    assert w.fake.stopped == [sid]
    assert w.session_of(cid) == sid, 'the id stays, so the next message can resume it'
    assert w.fake.by_id[w.brain]['state'] != 'stopped', 'the main brain is never stopped'


def test_a_busy_or_waited_for_side_session_is_never_stopped_as_idle(w: World) -> None:
    cid, sid = w.opened()
    w.clock.advance(IDLE_S + 60)
    w.fake.busy(sid)
    w.check()
    assert w.fake.stopped == [], 'stopped in the middle of a turn'
    w.fake.busy(sid, False)
    w.outbox.to_conversation(cid, {'type': 'user_message', 'message_id': 'msg_x', 'text': 'Hi'})
    w.check()
    assert w.fake.stopped == [], 'stopped with a message of the owner still waiting for it'
    w.deliver_all(cid)
    w.check()
    assert w.fake.stopped == [sid]


def test_the_next_message_resumes_the_stopped_session(w: World) -> None:
    cid, sid = w.opened()
    w.clock.advance(IDLE_S + 1)
    w.check()
    w.post(cid, 'Back again: what about Sunday?')
    w.check()
    assert w.fake.resumed == [sid]
    assert len(w.fake.forked) == 1, 'resumed, not forked again'
    assert w.session_of(cid) == sid


def test_a_session_that_cannot_be_resumed_is_forked_afresh_with_the_thread_so_far(
    w: World,
) -> None:
    cid, sid = w.opened('What should we cook tonight?')
    w.post(cid, 'Something with leeks')
    w.post(cid, 'Leek and potato soup.', role=MessageRole.agent)
    w.clock.advance(IDLE_S + 1)
    w.check()
    w.fake.gone.add(sid)
    w.post(cid, 'And a dessert?')
    w.outbox.to_conversation(
        cid, {'type': 'user_message', 'message_id': w.post(cid, 'Still to answer'), 'text': 'x'}
    )
    w.check()
    assert w.fake.resumed == [sid]
    second = w.fake.forked[1]
    assert second['from'] == w.brain
    assert '- Owner: Something with leeks' in second['prompt']
    assert '- You: Leek and potato soup.' in second['prompt']
    assert 'Still to answer' not in second['prompt'], 'a waiting message would arrive twice'
    new = w.session_of(cid)
    assert new is not None and new != sid


# Failures -------------------------------------------------------------------------------------


def test_a_failed_fork_is_said_in_the_thread_and_tried_a_few_times_only(w: World) -> None:
    w.fake.fail_forks = 99
    cid = w.open()
    w.check()
    [note] = w.notes(cid)
    assert note.startswith('Marcel could not open this side thread yet: The fork did not start')
    w.check()
    assert len(w.fake.forked) == 1, 'tried again at once: no backoff'
    for _ in range(TRIES):
        w.clock.advance(301)
        w.check()
    assert len(w.fake.forked) == TRIES, 'kept trying: a crash loop'
    assert len(w.notes(cid)) == 2
    assert 'Write here again to try once more' in w.notes(cid)[-1]
    assert w.sides.wanted() == []
    # The owner writes again: one more round.
    w.fake.fail_forks = 0
    w.post(cid, 'Try again please')
    w.check()
    assert w.session_of(cid) is not None


def test_a_brain_that_is_not_running_is_said_in_the_thread(w: World) -> None:
    w.fake.die(w.brain)
    cid = w.open()
    w.check()
    assert w.fake.forked == []
    assert 'main session is not running' in w.notes(cid)[0]


def test_a_runner_that_does_not_answer_counts_as_a_failure(w: World) -> None:
    w.fake.fork_unavailable = True
    cid = w.open()
    w.check()
    assert 'The runner is not answering.' in w.notes(cid)[0]


def test_a_runner_that_cannot_list_sessions_starts_and_stops_nothing(w: World) -> None:
    cid, _ = w.opened()
    w.fake.unreachable = True
    w.clock.advance(IDLE_S + 1)
    w.open()
    w.check()
    assert w.fake.stopped == [] and len(w.fake.forked) == 1
    assert cid not in w.sides.wanted()


def test_no_runner_does_nothing(engine: Engine) -> None:
    w = World(engine)
    w.sides.runner = None
    w.open()
    w.check()
    assert w.fake.forked == []


# The cap --------------------------------------------------------------------------------------


def test_at_most_max_sides_run_and_the_quietest_one_makes_room(w: World) -> None:
    opened = []
    for i in range(MAX_SIDES):
        opened.append(w.opened(f'Thread {i}'))
        w.clock.advance(60)
    # The oldest thread spoke last; the second one has been quiet the longest.
    w.post(opened[0][0], 'Still here')
    w.clock.advance(60)
    cid = w.open('One too many')
    w.check()
    assert w.fake.stopped == [opened[1][1]], 'not the quietest side session'
    assert w.session_of(cid) is not None
    live = [s for s in w.fake.by_id.values() if s['role'] == 'side' and s['state'] != 'stopped']
    assert len(live) == MAX_SIDES


def test_when_every_side_session_is_busy_the_new_one_waits_and_says_so_once(w: World) -> None:
    opened = [w.opened(f'Thread {i}') for i in range(MAX_SIDES)]
    for _, sid in opened:
        w.fake.busy(sid)
    cid = w.open('One too many')
    w.check()
    w.check()
    assert len(w.fake.forked) == MAX_SIDES and w.fake.stopped == []
    [note] = w.notes(cid)
    assert f'busy in {MAX_SIDES} other side threads' in note
    w.fake.busy(opened[2][1], False)
    w.check()
    assert w.fake.stopped == [opened[2][1]]
    assert w.session_of(cid) is not None


def test_a_fork_not_listed_yet_still_counts_toward_the_cap(w: World) -> None:
    for i in range(MAX_SIDES):
        w.opened(f'Thread {i}')
    w.fake.unlisted = {sid for sid, s in w.fake.by_id.items() if s['role'] == 'side'}
    w.open('One too many')
    w.check()
    assert len(w.fake.forked) == MAX_SIDES, 'went past the cap'


def test_a_side_session_the_runner_cannot_stop_keeps_the_new_one_waiting(w: World) -> None:
    for i in range(MAX_SIDES):
        w.opened(f'Thread {i}')

    async def refuse(_sid: str) -> None:
        raise RunnerUnavailable('The runner is not answering.')

    w.fake.stop = refuse  # type: ignore[method-assign]
    cid = w.open('One too many')
    w.check()
    assert len(w.fake.forked) == MAX_SIDES
    assert cid in w.sides.wanted()
    w.clock.advance(IDLE_S + 1)
    w.check()  # the idle sweep cannot stop them either; nothing breaks


# A brain rollover -----------------------------------------------------------------------------


def test_a_rollover_leaves_open_side_threads_running_and_re_forks_use_the_new_brain(
    w: World, engine: Engine
) -> None:
    cid, sid = w.opened()
    keeper = make_keeper(engine, w.fake, w.clock, RecordingAlerts())
    run(keeper.check())
    assert w.fake.spawned == [] and w.fake.stopped == [], 'the keeper took the fork for a brain'
    # The old brain is replaced (as at a rollover's hand-over).
    run(w.fake.stop(w.brain))
    new = w.set_brain(w.fake.add(name=f'brain-{w.agent_id}'))
    run(keeper.check())
    assert w.fake.by_id[sid]['state'] != 'stopped', 'the fork stopped with its brain'
    assert w.session_of(cid) == sid
    # Later the fork is gone for good: the next one is forked from the new brain.
    w.fake.die(sid)
    w.fake.gone.add(sid)
    w.post(cid, 'Hello again')
    w.check()
    assert w.fake.forked[-1]['from'] == new


# The runner client --------------------------------------------------------------------------------


def runner(status: int, body: dict[str, Any], seen: list[httpx.Request]) -> RunnerClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    return RunnerClient('/x.sock', transport=httpx.MockTransport(handler))


def test_the_runner_client_forks_with_the_contract_body() -> None:
    seen: list[httpx.Request] = []
    spawned = {'session_id': 'f-1', 'short_id': 'f', 'name': 'side-cnv_1', 'cwd': '/b',
               'channel_registered': True}  # fmt: skip
    sid = run(runner(201, spawned, seen).fork('b-1', name='side-cnv_1', prompt='Go'))
    assert sid == 'f-1'
    assert seen[0].method == 'POST' and seen[0].url.path == '/sessions/b-1/fork'
    assert json.loads(seen[0].content) == {'name': 'side-cnv_1', 'prompt': 'Go'}


@pytest.mark.parametrize(
    ('status', 'code', 'error'),
    [
        (409, 'not_forkable', StartFailed),
        (422, 'channel_timeout', StartFailed),
        (404, 'not_found', StartFailed),
        (503, 'claude_timeout', RunnerUnavailable),
    ],
)
def test_the_runner_client_says_why_a_fork_failed(
    status: int, code: str, error: type[Exception]
) -> None:
    client = runner(status, {'code': code, 'message': f'It said {code}.'}, [])
    with pytest.raises(error, match=f'It said {code}'):
        run(client.fork('b-1', name='side-x', prompt='Go'))


def test_the_runner_client_resumes_and_takes_a_running_session_as_it_is() -> None:
    seen: list[httpx.Request] = []
    run(runner(200, {'id': 's-1'}, seen).resume('s-1', 'Wake up'))
    assert seen[0].url.path == '/sessions/s-1/resume'
    run(runner(409, {'code': 'running', 'message': 'Still running.'}, []).resume('s-1', 'x'))
    with pytest.raises(StartFailed, match='copy'):
        run(runner(409, {'code': 'resume_copied', 'message': 'Claude started a copy.'}, [])
            .resume('s-1', 'x'))  # fmt: skip
    with pytest.raises(RunnerUnavailable):
        run(runner(503, {'code': 'claude_timeout', 'message': 'Too slow.'}, []).resume('s', 'x'))


def test_the_loop_forks_in_the_background_and_stops_cleanly(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(side, 'TICK_S', 0.01)

    async def go() -> None:
        w.sides.start()
        cid = w.open()
        for _ in range(100):
            if w.session_of(cid):
                break
            await asyncio.sleep(0.01)
        await w.sides.stop()
        await w.sides.stop()  # twice is fine

    run(go())
    assert len(w.fake.forked) == 1
