"""What the avatar shows (B-28, S-04.8): `agent.state`, derived from the agent's tasks and its
brain's turns, sent to the phone on change only.

The real app runs underneath (the task registry, the channel gateway, the event log); only the
clock is fake, and the background tick is held back (conftest), so each test decides when time
passes. Every `agent.state` event and every `/me` body is checked against the contracts.
"""

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session
from test_channel_fakes import OWNER, ChannelHub, channel_hub
from test_tasks_fakes import run, validator

from marcel_hub import agent_state
from marcel_hub.agent_state import DONE_S, THINKING_S, AgentStates, derive
from marcel_hub.conversations import post_message
from marcel_hub.models import (
    Agent,
    Conversation,
    MessageKind,
    MessageRole,
    Task,
    TaskEventType,
    TaskState,
)

STATE = validator('app-api.yaml#/components/schemas/AgentState')
ME = validator('app-api.yaml#/components/schemas/Me')

IDLE = {'kind': 'idle'}
THINKING = {'kind': 'thinking'}
DONE = {'kind': 'done'}
NEEDS_YOU = {'kind': 'needs_you'}


def working(n: int) -> dict[str, Any]:
    return {'kind': 'working', 'active_tasks': n}


# The precedence table -------------------------------------------------------------------------

# (tasks needing you, tasks working, brain turn running, a completion in the last 5 s) → state.
# Every combination of the four inputs, so each level is shown winning over every mix below it.
TABLE = [
    (0, 0, False, False, IDLE),
    (0, 0, False, True, DONE),
    (0, 0, True, False, THINKING),
    (0, 0, True, True, THINKING),
    (0, 2, False, False, working(2)),
    (0, 2, False, True, working(2)),
    (0, 2, True, False, working(2)),
    (0, 2, True, True, working(2)),
    (1, 0, False, False, NEEDS_YOU),
    (1, 0, False, True, NEEDS_YOU),
    (1, 0, True, False, NEEDS_YOU),
    (1, 0, True, True, NEEDS_YOU),
    (1, 2, False, False, NEEDS_YOU),
    (1, 2, False, True, NEEDS_YOU),
    (1, 2, True, False, NEEDS_YOU),
    (1, 2, True, True, NEEDS_YOU),
    # n is the number of working tasks, not clamped: the app shows at most 3 spots.
    (0, 1, False, False, working(1)),
    (0, 5, False, False, working(5)),
]


@pytest.mark.parametrize(('needs_you', 'n', 'thinking', 'done', 'expected'), TABLE)
def test_precedence(needs_you: int, n: int, thinking: bool, done: bool, expected: dict) -> None:
    state = derive(needs_you=needs_you, working=n, thinking=thinking, done=done)
    STATE.validate(state)
    assert state == expected


# The real app -----------------------------------------------------------------------------------


class Clock:
    """Seconds that pass only when a test says so."""

    def __init__(self) -> None:
        self.now = time.monotonic()

    def __call__(self) -> float:
        return self.now


class Avatar:
    """The app's agent states with a fake clock, and helpers to drive tasks and the brain."""

    def __init__(self, hub: ChannelHub) -> None:
        self.hub = hub
        self.clock = Clock()
        self.states: AgentStates = hub.app.state.agent_states
        self.states.clock = self.clock

    def later(self, seconds: float) -> None:
        self.clock.now += seconds
        self.states.tick()

    def sent(self) -> list[dict[str, Any]]:
        """Every `agent.state` sent so far (each checked against events.schema.json)."""
        found = []
        for frame in self.hub.events('agent.state'):
            assert frame['data']['agent_id'] == self.hub.agent_id
            found.append(frame['data']['state'])
        return found

    def move(self, task_id: str, to: TaskState, **fields: Any) -> None:
        run(self.hub.registry.transition(task_id, to, 'test', **fields))

    def say(self, text: str = 'How full is the disk?', conversation_id: str | None = None) -> None:
        conv = conversation_id or self.hub.main_id
        res = self.hub.client.post(f'/api/conversations/{conv}/messages', json={'text': text})
        assert res.status_code in (200, 201), res.text

    def me(self) -> dict[str, Any]:
        res = self.hub.client.get('/api/me')
        assert res.status_code == 200, res.text
        ME.validate(res.json())
        (agent,) = res.json()['agents']
        return agent['state']


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[ChannelHub]:
    with channel_hub(tmp_path) as found:
        yield found


@pytest.fixture
def a(hub: ChannelHub) -> Avatar:
    return Avatar(hub)


def test_an_agent_with_nothing_going_on_is_idle_and_nothing_is_sent(a: Avatar) -> None:
    assert a.me() == IDLE
    assert a.sent() == []


def test_working_counts_the_working_tasks(a: Avatar) -> None:
    one, _ = a.hub.task(TaskState.starting)
    two, _ = a.hub.task(TaskState.starting)
    a.move(one, TaskState.working)
    a.move(two, TaskState.working)
    assert a.sent() == [working(1), working(2)]
    assert a.me() == working(2)


def test_a_task_that_needs_you_wins_over_working(a: Avatar) -> None:
    one, _ = a.hub.task(TaskState.working)
    two, _ = a.hub.task(TaskState.starting)
    a.move(two, TaskState.working)
    a.move(one, TaskState.needs_you, waiting_for='Your approval')
    assert a.sent() == [working(2), NEEDS_YOU]
    a.move(one, TaskState.working)
    assert a.sent()[-1] == working(2)


def test_a_state_is_sent_on_change_only(a: Avatar) -> None:
    """Control: emit on change. Without it every task event would send a duplicate state."""
    task, _ = a.hub.task(TaskState.starting)
    a.move(task, TaskState.working)
    run(a.hub.registry.report_state(task, TaskState.working, 'again'))
    a.hub.registry.append_event(task, TaskEventType.progress, {'text': 'Reading'})
    a.me()
    a.states.tick()
    other, _ = a.hub.task(TaskState.queued)  # a queued task is not working
    a.move(other, TaskState.starting)
    assert a.sent() == [working(1)]


def test_done_shows_for_five_seconds_after_a_completion(a: Avatar) -> None:
    """Control: the done window ends on its own, with no new event to trigger it."""
    task, _ = a.hub.task(TaskState.working)
    a.move(task, TaskState.done, summary='Disk is 40 % full.')
    assert a.sent() == [DONE]
    a.later(DONE_S - 0.1)
    assert a.sent() == [DONE]
    assert a.me() == DONE
    a.later(0.2)
    assert a.sent() == [DONE, IDLE]
    assert a.me() == IDLE


def test_a_failed_or_stopped_task_is_not_a_completion(a: Avatar) -> None:
    one, _ = a.hub.task(TaskState.starting)
    two, _ = a.hub.task(TaskState.starting)
    a.move(one, TaskState.failed)
    a.move(two, TaskState.stopped)
    assert a.sent() == []
    assert a.me() == IDLE


def test_working_hides_the_done_window_and_it_still_ends(a: Avatar) -> None:
    one, _ = a.hub.task(TaskState.working)
    two, _ = a.hub.task(TaskState.working)
    a.move(one, TaskState.done)  # still one working
    assert a.sent() == [working(1)]
    a.move(two, TaskState.done)  # a fresh completion: done again
    assert a.sent() == [working(1), DONE]
    a.later(DONE_S)
    assert a.sent() == [working(1), DONE, IDLE]


def test_the_brain_thinks_from_a_message_until_it_replies(a: Avatar) -> None:
    sid = a.hub.brain()
    a.say()
    assert a.sent() == [THINKING]
    assert a.me() == THINKING
    with a.hub.connect() as ch:
        ch.hello(sid)
        ch.deliver()  # the user_message
        assert ch.call('reply', text='40 % full.')['type'] == 'call_ack'
    assert a.sent() == [THINKING, IDLE]


def test_a_tasks_milestone_or_approval_in_the_main_chat_does_not_end_the_turn(a: Avatar) -> None:
    """Only the brain's own reply ends its turn. A task posts milestones and approval cards in
    the same main conversation as agent messages; those must leave the avatar thinking."""
    task, _ = a.hub.task(TaskState.done)
    a.say()
    a.hub.gateway.milestone(task, 'done', summary='12 GB free.')
    with Session(a.hub.engine) as s:
        main = s.get(Conversation, a.hub.main_id)
        assert main is not None
        post_message(
            s,
            a.hub.app.state.events,
            main,
            role=MessageRole.agent,
            kind=MessageKind.approval,
            body={'approval_id': 'apr_01'},
            task_id=task,
        )
    kinds = [m.kind for m in a.hub.messages() if m.role == MessageRole.agent]
    assert kinds == [MessageKind.milestone, MessageKind.approval]
    assert a.sent() == [THINKING]
    assert a.me() == THINKING


def test_a_side_thread_thinks_too_and_a_task_thread_does_not(a: Avatar) -> None:
    side, _ = a.hub.side()
    task, _ = a.hub.task(TaskState.done)
    thread, _ = a.hub.side(task_id=task)
    a.say(conversation_id=thread)
    # The thread's message steers its task (S-04.9): the done task goes back to working.
    assert a.sent() == [working(1)]
    a.move(task, TaskState.done)
    a.say(conversation_id=side)
    assert a.sent() == [working(1), DONE, THINKING]


def test_a_turn_that_never_ends_stops_thinking_after_the_timeout(a: Avatar) -> None:
    """Control: the thinking timeout. A silent brain cannot keep the avatar thinking."""
    a.say()
    a.later(THINKING_S - 1)
    assert a.sent() == [THINKING]
    a.say('Are you there?')  # a new message starts a new wait
    a.later(THINKING_S - 1)
    assert a.sent() == [THINKING]
    a.later(1)
    assert a.sent() == [THINKING, IDLE]


def test_working_wins_over_thinking_and_thinking_comes_back(a: Avatar) -> None:
    a.say()
    task, _ = a.hub.task(TaskState.starting)
    a.move(task, TaskState.working)
    a.move(task, TaskState.done)
    assert a.sent() == [THINKING, working(1), THINKING]


def test_a_task_changing_state_reaches_the_phone(a: Avatar) -> None:
    task, _ = a.hub.task(TaskState.starting)
    headers = {'Authorization': f'Bearer {a.hub.token}'}
    with a.hub.client.websocket_connect('/api/ws', headers=headers) as ws:
        assert ws.receive_json()['type'] == 'hello'
        a.move(task, TaskState.working)
        frames = [ws.receive_json() for _ in range(3)]  # task.event, task.updated, agent.state
    state = [f for f in frames if f['type'] == 'agent.state']
    assert [f['data'] for f in state] == [{'agent_id': a.hub.agent_id, 'state': working(1)}]


def test_the_tick_ends_the_done_window_on_the_apps_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app's lifespan runs the tick: with a short tick, done ends with no test calling it."""
    monkeypatch.setattr(agent_state, 'TICK_S', 0.02)
    with channel_hub(tmp_path) as hub:
        states: AgentStates = hub.app.state.agent_states
        states.done_s = 0.05
        task, _ = hub.task(TaskState.working)
        run(hub.registry.transition(task, TaskState.done, 'test'))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(hub.events('agent.state')) < 2:
            time.sleep(0.02)
        assert [e['data']['state'] for e in hub.events('agent.state')] == [DONE, IDLE]


def test_after_a_restart_the_phone_gets_what_changed_while_the_hub_was_down(
    hub: ChannelHub,
) -> None:
    task, _ = hub.task(TaskState.starting)
    run(hub.registry.transition(task, TaskState.working, 'test'))
    with Session(hub.engine) as s:  # the hub stops; the task ends while it is down
        row = s.get(Task, task)
        assert row is not None
        row.state = TaskState.stopped
        s.add(row)
        s.commit()
    fresh = AgentStates(hub.app.state.events)
    fresh.prime()
    assert [e['data']['state'] for e in hub.events('agent.state')] == [working(1), IDLE]
    AgentStates(hub.app.state.events).prime()  # the phone already has idle: nothing new
    assert len(hub.events('agent.state')) == 2


def test_each_agent_has_its_own_state(a: Avatar) -> None:
    with Session(a.hub.engine) as s:
        other = Agent(owner_user_id=OWNER, name='Bob', animal='giraffe', palette='pink', seed='2')
        s.add(other)
        s.commit()
        other_id = other.id
    task, _ = a.hub.task(TaskState.starting)
    a.move(task, TaskState.working)
    assert a.states.current(other_id) == IDLE
    assert a.states.current(a.hub.agent_id) == working(1)


def test_a_broken_event_does_not_break_the_event_log(a: Avatar, caplog: Any) -> None:
    a.states.observe(OWNER, 'task.updated', {'no agent': True})
    assert 'Could not follow a task.updated event' in caplog.text
