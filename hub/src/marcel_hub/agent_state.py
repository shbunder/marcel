"""What each agent's avatar shows (B-28): the contract's `AgentState`, sent as `agent.state`.

The state is derived, never stored, in this order (`derive`):

1. `needs_you` if any of the agent's tasks is `needs_you` (an approval, a question, a login);
2. else `working` with `active_tasks` = the number of its tasks in `working` (the app shows at most
   three glowing spots; the hub sends the real number);
3. else `thinking` while a brain turn runs;
4. else `done` for `DONE_S` seconds after one of its tasks reached `done`;
5. else `idle`.

`AgentStates` watches the app event log (`EventLog.observers`) and recomputes an agent's state
when something it depends on changes. It sends `agent.state` through the same event log, so the
phone gets it like any other event, and only when the state differs from the last one sent.

A brain turn has no "turn over" signal of its own yet, so the hub reads it from the conversation:
a message the owner writes in a conversation the brain answers (the main conversation, or a side
thread that is not a task's thread) starts a turn there; the agent's next text message in that
conversation (the brain's `reply`) ends it. A turn that never ends — the brain crashed, or chose
not to answer — stops counting after `THINKING_S` seconds, so the avatar cannot think forever.

The two windows (done, thinking) end on their own: `tick` looks for windows that ran out and
recomputes those agents. The app's lifespan runs it every `TICK_S` seconds on its event loop;
tests set a fake `clock` and call `tick` themselves.

After a restart, `prime` compares each agent's state with the last `agent.state` still in the
event log and sends the new one if it changed while the hub was down.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, col, select

from marcel_hub.models import (
    Agent,
    AppEvent,
    Conversation,
    MessageKind,
    MessageRole,
    Task,
    TaskEventType,
    TaskState,
)
from marcel_hub.ws import EventLog

log = logging.getLogger(__name__)

DONE_S = 5.0  # how long `done` shows after a completion (B-28)
THINKING_S = 180.0  # a brain turn with no reply stops counting after this long
TICK_S = 1.0  # how often the app looks for windows that ran out

EVENT = 'agent.state'
ASKS = frozenset({MessageKind.text.value, MessageKind.quote.value})  # what the brain is sent


def derive(*, needs_you: int, working: int, thinking: bool, done: bool) -> dict[str, Any]:
    """The contract's `AgentState` for these inputs, by B-28's precedence."""
    if needs_you:
        return {'kind': 'needs_you'}
    if working:
        return {'kind': 'working', 'active_tasks': working}
    if thinking:
        return {'kind': 'thinking'}
    if done:
        return {'kind': 'done'}
    return {'kind': 'idle'}


IDLE = derive(needs_you=0, working=0, thinking=False, done=False)


class AgentStates:
    """Derives and sends every agent's state. `clock` is seconds (monotonic).

    All of its own bookkeeping happens under the event log's lock (re-entrant): the log calls
    `observe` holding it, and `tick` takes it before anything else, so a state is computed and
    sent in one step and two threads can never send the same change twice or out of order.
    """

    def __init__(
        self,
        events: EventLog,
        clock: Callable[[], float] = time.monotonic,
        done_s: float = DONE_S,
        thinking_s: float = THINKING_S,
    ) -> None:
        self.events = events
        self.engine = events.engine
        self.clock = clock
        self.done_s = done_s
        self.thinking_s = thinking_s
        self._thinking: dict[str, dict[str, float]] = {}  # agent → conversation → turn deadline
        self._done: dict[str, float] = {}  # agent → when its done window ends
        self._sent: dict[str, dict[str, Any]] = {}  # agent → the state the phone has
        self._task: asyncio.Task[None] | None = None

    # Reading ---------------------------------------------------------------------------------

    def current(self, agent_id: str) -> dict[str, Any]:
        """The agent's state now (for `/me`). Sends it first if it changed unseen, so the REST
        answer and the event stream never disagree."""
        return self.refresh(agent_id)

    def refresh(self, agent_id: str) -> dict[str, Any]:
        """Recompute the agent's state and send `agent.state` if it changed."""
        with self.events.lock:
            state = self._derive(agent_id)
            if state != self._last_sent(agent_id):
                with Session(self.engine) as s:
                    agent = s.get(Agent, agent_id)
                    if agent is None:
                        return state
                    self.events.publish(
                        s, agent.owner_user_id, EVENT, {'agent_id': agent_id, 'state': state}
                    )
                self._sent[agent_id] = state
            return state

    def turn_open(self, agent_id: str, conversation_id: str) -> bool:
        """True while a brain turn runs in that conversation: the owner's message reached it and
        no reply came yet (S-06.2 hands a conversation to a new brain only between turns)."""
        with self.events.lock:
            deadline = self._thinking.get(agent_id, {}).get(conversation_id)
            return deadline is not None and deadline > self.clock()

    def _derive(self, agent_id: str) -> dict[str, Any]:
        now = self.clock()
        with Session(self.engine) as s:
            counts = dict(
                s.exec(
                    select(Task.state, func.count())
                    .where(Task.agent_id == agent_id)
                    .where(col(Task.state).in_([TaskState.needs_you, TaskState.working]))
                    .group_by(col(Task.state))
                ).all()
            )
        turns = self._thinking.get(agent_id, {})
        return derive(
            needs_you=counts.get(TaskState.needs_you, 0),
            working=counts.get(TaskState.working, 0),
            thinking=any(deadline > now for deadline in turns.values()),
            done=self._done.get(agent_id, 0.0) > now,
        )

    def _last_sent(self, agent_id: str) -> dict[str, Any]:
        """What the phone was last sent: remembered, or the newest `agent.state` still in the
        event log (after a restart), or idle (what `/me` reported before any was sent)."""
        if agent_id not in self._sent:
            with Session(self.engine) as s:
                rows = s.exec(
                    select(AppEvent.data_json)
                    .where(AppEvent.type == EVENT)
                    .order_by(col(AppEvent.id).desc())
                ).all()
            found = next((r['state'] for r in rows if r.get('agent_id') == agent_id), IDLE)
            self._sent[agent_id] = found
        return self._sent[agent_id]

    # Following the event log -----------------------------------------------------------------

    def observe(self, _user_id: str, type_: str, data: dict[str, Any]) -> None:
        """`EventLog` observer: recompute the agent a change is about."""
        if type_ == EVENT:
            return
        try:
            agent_id = self._follow(type_, data)
            if agent_id is not None:
                self.refresh(agent_id)
        except Exception:  # an event must never fail because of the avatar
            log.exception('Could not follow a %s event for the avatar.', type_)

    def _follow(self, type_: str, data: dict[str, Any]) -> str | None:
        """Note what this event starts or ends; returns the agent to recompute, if any."""
        if type_ in ('task.created', 'task.updated'):
            return data['agent_id']
        if type_ == 'task.event':
            if data.get('type') != TaskEventType.state.value:
                return None
            with Session(self.engine) as s:
                task = s.get(Task, data['task_id'])
            if task is None:
                return None
            if data['data'].get('to') == TaskState.done.value:
                self._done[task.agent_id] = self.clock() + self.done_s
            return task.agent_id
        if type_ == 'message.created':
            with Session(self.engine) as s:
                conv = s.get(Conversation, data['conversation_id'])
            if conv is None or conv.task_id is not None:
                return None  # a task's thread is the worker's, not the brain's
            turns = self._thinking.setdefault(conv.agent_id, {})
            if data['role'] == MessageRole.user.value and data['kind'] in ASKS:
                turns[conv.id] = self.clock() + self.thinking_s
            elif data['role'] == MessageRole.agent.value and data['kind'] == 'text':
                turns.pop(conv.id, None)
            else:
                return None
            return conv.agent_id
        return None

    # Windows that run out --------------------------------------------------------------------

    def tick(self) -> list[str]:
        """Recompute every agent whose done or thinking window ran out. Returns their ids."""
        with self.events.lock:
            now = self.clock()
            due: set[str] = set()
            for agent_id, until in list(self._done.items()):
                if until <= now:
                    del self._done[agent_id]
                    due.add(agent_id)
            for agent_id, turns in self._thinking.items():
                for conv_id, deadline in list(turns.items()):
                    if deadline <= now:
                        del turns[conv_id]
                        due.add(agent_id)
            for agent_id in sorted(due):
                self.refresh(agent_id)
            return sorted(due)

    def prime(self) -> None:
        """At start: send every agent's state that changed while the hub was down."""
        with Session(self.engine) as s:
            ids = s.exec(select(Agent.id).order_by(col(Agent.id))).all()
        for agent_id in ids:
            self.refresh(agent_id)

    def start(self, tick: float | None = None) -> None:
        """Run `tick` every `tick` seconds (`TICK_S`), on the running event loop."""
        self._task = asyncio.get_running_loop().create_task(self._run(tick or TICK_S))

    async def _run(self, tick: float) -> None:
        while True:
            await asyncio.sleep(tick)
            try:
                self.tick()
            except Exception:  # one bad tick must not stop the avatar for good
                log.exception('Could not update the avatar states.')

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


def install(app: Any) -> AgentStates:
    """Put the agent states on `app.state.agent_states` and let them watch the app event log.
    The app's lifespan primes and starts them."""
    states = AgentStates(app.state.events)
    app.state.events.observers.append(states.observe)
    app.state.agent_states = states
    return states
