"""Brain keep-alive and rollover (S-06.2, B-01): exactly one brain session per agent.

The brain is the `claude` session that answers the main conversation (`role: brain`, started in
the brain workspace, `~/marcel/brain` on the NUC). `BrainKeeper` keeps one running per agent:

- **Checks.** At start, after every runner event and every runner (re)connect (`RunnerWatch.pokes`),
  and every `TICK_S` seconds, the keeper lists the runner's sessions and checks each agent's brain
  (`Conversation.brain_session_id` on the main conversation). Checks run one at a time (a lock), so
  a burst of runner events never starts two brains.
- **Respawn.** A brain that is gone, `failed`, `stopped`, or `done` with no process left is replaced
  at once. The new session's id is stored on the main conversation straight away; messages sent
  while no brain ran wait in the channel Outbox (they are addressed to the conversation, not the
  session) and reach the new brain when its channel says hello. Each respawn is a `brain.rollover`
  with reason `crashed`. A runner that cannot be reached, or a session state the hub does not know,
  is never read as "dead": nothing is started on a guess.
- **Backoff.** A spawn that fails is retried after 5 s, then 15 s, 30 s, 1 min, 2 min, then every
  5 min. After `ALERT_AFTER` failures in a row the owner gets one "something broke" alert; it is
  cleared when a brain starts again.
- **Rollover.** Nightly at 04:00 in the owner's time zone (the `tz` setting), when the brain's
  context is more than `CONTEXT_FULL` full (S-06.7), or on request (`roll_over`, and the app's
  `POST /api/agents/{agent_id}/brain/rollover`), the keeper starts a new brain next to the old
  one, seeded with what the hub kept (`seed`). The old brain keeps answering until the new one
  says hello on the channel; the hello is the hand-over (`said_hello`, called by the channel
  gateway), and only once the old brain is between turns. The new id goes on the conversation,
  the old session is stopped, and a `brain.rollover` event is stored and sent to the app. A new
  brain that never says hello within `HELLO_WAIT_S` is stopped and the old one stays: there is
  never zero brains, and never two answering (the gateway refuses a `reply` from a session that
  is no longer the conversation's).
- **Extras.** Any other live brain session of the agent the runner still has (one left over from a
  hub restart in the middle of a rollover) is stopped.
- **The seed** is built by the hub from its own records — the last 24 h of the main conversation
  (messages and task milestones), the open threads and `MEMORY.md` — with no model call.
- **Context size** (S-06.7). The keeper reads the current brain's transcript from the runner
  (`tail_turn_ends`) and, at each `turn_end`, adds up its `usage` (input, cache and output tokens:
  what the session carries into its next turn). Past `CONTEXT_FULL` of the model's context window
  it asks for a `context_full` rollover — once: while one is asked for or waiting for its hello,
  and for any session that is no longer the conversation's brain, nothing more is asked. The
  window is the `brain_context_window` setting (`CONTEXT_WINDOW` by default), or `LONG_CONTEXT`
  for a model named with Claude Code's `[1m]` suffix.
- **History** (S-06.7). Every rollover is a `brain_rollover` row, written with its
  `brain.rollover` event, so the notebook can list past ones after the event log has moved on
  (`GET /api/agents/{agent_id}/brain/rollovers`).
"""

import asyncio
import contextlib
import logging
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from datetime import time as wall_time
from typing import Any, Protocol

import httpx
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlmodel import Session, col, select

from marcel_hub.auth import ApiError, current_device, require_device
from marcel_hub.auth.deps import DbSession
from marcel_hub.conversations import ensure_main_conversation
from marcel_hub.memory import MemoryRepo
from marcel_hub.models import (
    Agent,
    BrainRollover,
    Conversation,
    ConversationKind,
    Message,
    MessageKind,
    MessageRole,
    Project,
    Task,
    TaskState,
)
from marcel_hub.runner_client import (
    Alerts,
    BadEvent,
    LogAlerts,
    StreamRefused,
    check_transcript_event,
    sse,
)
from marcel_hub.tasks.ports import RunnerUnavailable, StartFailed
from marcel_hub.ws import EventLog

log = logging.getLogger(__name__)

TICK_S = 2.0  # a dead brain is noticed within this, or at once on its runner event
SPAWN_GRACE_S = 60.0  # a session the runner just started may not be in its list yet
HELLO_WAIT_S = 150.0  # the channel server retries an unknown session for 2 minutes
BACKOFF_S = (5.0, 15.0, 30.0, 60.0, 120.0, 300.0)
ALERT_AFTER = 3
BUSY_TRIES = 2  # a rollover put off this often because Marcel was busy is skipped
BUSY_RETRY_S = 300.0
ALERT = 'brain_down'
ROLLOVER_AT = wall_time(4, 0)

CONTEXT_FULL = 0.70  # past this share of its context window, the brain is rolled over
CONTEXT_WINDOW = 200_000  # tokens: the `brain_context_window` setting's default (Opus)
LONG_CONTEXT = 1_000_000  # tokens: a model named with Claude Code's `[1m]` suffix
TAIL_RETRY_S = (1.0, 30.0)  # reading a brain's transcript again: first wait, longest wait
MAX_ROLLOVERS = 50  # the most past rollovers one request lists (app-api `limit`)

SUMMARY_HOURS = 24
MAX_MESSAGES = 60
MAX_LINE = 400
MAX_MEMORY = 8000
MAX_TASKS = 30

DEAD = frozenset({'failed', 'stopped'})
OPEN_TASKS = (
    TaskState.queued,
    TaskState.starting,
    TaskState.working,
    TaskState.needs_you,
    TaskState.silent,
    TaskState.unknown,
)
REASON_WORDS = {
    'crashed': 'the previous one stopped unexpectedly',
    'daily': 'the daily fresh start',
    'manual': 'you were asked to start fresh',
    'context_full': 'the previous one ran out of room',
}


class BrainRunner(Protocol):
    """What the keeper needs from the runner (`runner_client.RunnerClient`)."""

    async def spawn_brain(self, *, name: str, model: str, cwd: str, prompt: str) -> str: ...

    async def sessions(self) -> list[dict[str, Any]]: ...

    async def stop(self, session_id: str) -> None: ...


class TailStream(Protocol):
    def aiter_lines(self) -> AsyncIterator[str]: ...

    async def aclose(self) -> None: ...


class TailRunner(Protocol):
    """What `tail_turn_ends` needs from the runner (`runner_client.RunnerClient`)."""

    async def open_stream(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> TailStream: ...


def brain_name(agent_id: str) -> str:
    """The runner's name for an agent's brain sessions (`SpawnRequest.name`)."""
    return f'brain-{agent_id}'[:60]


def next_rollover(after: datetime, tz: tzinfo | None = None) -> datetime:
    """The first 04:00 wall-clock time after `after`, in `tz` (None: the hub's local zone).

    Built from the calendar day, not by adding 24 hours, so a clock change moves nothing."""
    local = after.astimezone(tz) if tz is not None else after.astimezone()
    for days in range(3):
        wall = datetime.combine(local.date() + timedelta(days=days), ROLLOVER_AT)
        due = wall.replace(tzinfo=tz) if tz is not None else wall.astimezone()
        if due > after:
            return due.astimezone(UTC)
    raise AssertionError('04:00 comes every day')  # pragma: no cover


def context_window(model: str, default: int = CONTEXT_WINDOW) -> int:
    """The context window, in tokens, of the brain's model: `LONG_CONTEXT` for a `[1m]` model
    (e.g. `opus[1m]`), otherwise `default` (the `brain_context_window` setting)."""
    return LONG_CONTEXT if model.strip().lower().endswith('[1m]') else default


def context_tokens(usage: Any) -> int | None:
    """What the session carries into its next turn, from `TurnEnd.usage`
    (transcript.schema.json): input + cache creation + cache read + output tokens. None when the
    turn has no usage."""
    if not isinstance(usage, dict):
        return None
    keys = (
        'input_tokens',
        'cache_creation_input_tokens',
        'cache_read_input_tokens',
        'output_tokens',
    )
    total = 0
    for key in keys:
        count = usage.get(key)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return None
        total += count
    return total


async def tail_turn_ends(
    runner: TailRunner,
    session_id: str,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncIterator[dict[str, Any]]:
    """The `data` of each main-thread `turn_end` in a session's transcript: first the latest one
    already there (a hub restart picks up where the brain is), then each new one as it comes.
    Reads again with backoff when the runner is away or the stream breaks; ends when the runner
    says the session is not there (404) or the request is bad (400)."""
    path = f'/sessions/{session_id}/tail'
    offset, follow, delay = 0, False, TAIL_RETRY_S[0]
    while True:
        params = {'offset': offset, 'follow': 'true' if follow else 'false'}
        try:
            res = await runner.open_stream(path, params=params)
        except StreamRefused as exc:
            if exc.status != 503:
                log.info('Stopped reading main session %s: %s', session_id[:8], exc)
                return
            res = None
        except RunnerUnavailable:
            res = None
        if res is not None:
            latest: dict[str, Any] | None = None
            try:
                async for message in sse(res.aiter_lines()):
                    try:
                        event = check_transcript_event(message.data, session_id)
                    except BadEvent as exc:
                        log.warning('Ignored a bad event of main session %s: %s', session_id[:8],
                                    exc)  # fmt: skip
                        continue
                    offset = event['offset']
                    if event['type'] != 'turn_end' or 'subagent_id' in event:
                        continue
                    if follow:
                        yield event['data']
                    else:
                        latest = event['data']
            except httpx.TransportError as exc:
                log.info('The transcript of main session %s broke off (%s).', session_id[:8], exc)
            finally:
                await res.aclose()
            if not follow:  # caught up: the latest turn counts, then follow from here
                follow = True
                if latest is not None:
                    yield latest
                continue
            delay = TAIL_RETRY_S[0]
        await sleep(delay)
        delay = min(delay * 2, TAIL_RETRY_S[1])


def _alive(session: dict[str, Any]) -> bool:
    """False only when the runner says the process is gone. A state the hub does not know counts
    as alive: nothing is started on a guess."""
    state = session.get('state')
    if state in DEAD:
        return False
    # `status` is there only while the process lives (runner-api.yaml Session.status).
    return not (state == 'done' and 'status' not in session)


def _busy(session: dict[str, Any]) -> bool:
    return session.get('state') == 'working' or session.get('status') == 'busy'


@dataclass
class _Pending:
    session_id: str
    reason: str
    kept: str
    since: float  # monotonic, when it was started
    seeded_at: datetime  # the hub's clock when its seed was written
    refused_busy: bool = False  # its hello came while the old brain was in a turn


@dataclass
class _AgentBrain:
    pending: _Pending | None = None
    wanted: str | None = None  # a rollover asked for, not started yet
    retiring: set[str] = field(default_factory=set)
    spawned_at: dict[str, float] = field(default_factory=dict)
    failures: int = 0
    busy_tries: int = 0
    next_try: float = 0.0
    alerted: bool = False


class BrainKeeper:
    def __init__(
        self,
        engine: Any,
        runner: BrainRunner | None,
        *,
        cwd: str,
        events: EventLog | None = None,
        alerts: Alerts | None = None,
        memory: MemoryRepo | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        tz: tzinfo | None = None,
        window: int = CONTEXT_WINDOW,
    ) -> None:
        self.engine = engine
        self.runner = runner
        self.cwd = cwd
        self.events = events
        self.alerts = alerts or LogAlerts()
        self.memory = memory
        self.clock = clock
        self.monotonic = monotonic
        self.tz = tz  # the app passes the `tz` setting; None: the process's local zone
        self.window = window  # the `brain_context_window` setting
        self._lock = asyncio.Lock()  # one check at a time
        self._mutex = threading.Lock()  # the per-agent state, shared with `said_hello`
        self._agents: dict[str, _AgentBrain] = {}
        self._seen: dict[str, dict[str, Any]] = {}  # session id → the runner's last word on it
        self._next_daily = next_rollover(self.clock(), tz)
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._job: asyncio.Task[None] | None = None
        # Set by the app: the avatar's turn tracking (S-04.8), and the gateway's hand-over that
        # sends the new brain what its seed could not hold.
        self.turn_open: Callable[[str, str], bool] | None = None
        self.hand_over: Callable[[str, datetime], None] | None = None
        self._handed: dict[str, tuple[str, datetime]] = {}  # new brain → (conversation, seed)
        # Set by the app when it has a runner: each brain's `turn_end`s (`tail_turn_ends`).
        self.turn_ends: Callable[[str], AsyncIterator[dict[str, Any]]] | None = None
        self._tails: dict[str, tuple[str, asyncio.Task[None]]] = {}  # agent → (session, reader)

    # Lifecycle --------------------------------------------------------------------------------

    def start(self, tick: float | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._job = self._loop.create_task(self._run(tick or TICK_S), name='brain-keeper')

    async def stop(self) -> None:
        tails = [task for _, task in self._tails.values()]
        self._tails.clear()
        for task in tails:
            task.cancel()
        await asyncio.gather(*tails, return_exceptions=True)
        if self._job is not None:
            self._job.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._job
            self._job = None

    def poke(self) -> None:
        """Check soon (a runner event happened). Safe from any thread."""
        if self._loop is not None and self._wake is not None:
            with contextlib.suppress(RuntimeError):  # the loop is closed: the hub is stopping
                self._loop.call_soon_threadsafe(self._wake.set)

    async def _run(self, tick: float) -> None:
        assert self._wake is not None
        while True:
            try:
                await self.check()
            except Exception:  # one bad check must not stop the keeper for good
                log.exception("Could not check Marcel's main session.")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), tick)
            self._wake.clear()

    def roll_over(self, agent_id: str, reason: str = 'manual') -> bool:
        """Ask for a fresh brain for `agent_id` (reason as in `brain.rollover`). False, and
        nothing changes, when one is already asked for or waiting for its hello."""
        with self._mutex:
            st = self._state(agent_id)
            if st.pending is not None or st.wanted is not None:
                return False
            st.wanted = reason
        self.poke()
        return True

    def rolling_over(self, agent_id: str) -> bool:
        """A rollover is asked for, or its new brain is waiting to say hello."""
        with self._mutex:
            st = self._agents.get(agent_id)
            return st is not None and (st.pending is not None or st.wanted is not None)

    def turn_ended(self, session_id: str, data: dict[str, Any]) -> bool:
        """A brain session ended a turn (`turn_end` `data`). Past `CONTEXT_FULL` of its context
        window, ask for a `context_full` rollover; True when one was asked for. Only the
        conversation's current brain counts, and only once (`roll_over` refuses a second)."""
        tokens = context_tokens(data.get('usage'))
        if tokens is None:
            return False
        with Session(self.engine) as s:
            conv = s.exec(
                select(Conversation)
                .where(Conversation.brain_session_id == session_id)
                .where(Conversation.kind == ConversationKind.main)
            ).first()
            if conv is None:  # not, or no longer, an agent's brain: an old one's last turn
                return False
            agent = s.get(Agent, conv.agent_id)
            assert agent is not None
            agent_id, window = agent.id, context_window(agent.model_brain, self.window)
        if tokens < CONTEXT_FULL * window:
            return False
        if not self.roll_over(agent_id, 'context_full'):
            return False  # one is on its way already
        log.info(
            "Marcel's main session %s is %.0f%% full (%d of %d tokens); starting a fresh one.",
            session_id[:8],
            100 * tokens / window,
            tokens,
            window,
        )
        return True

    def _state(self, agent_id: str) -> _AgentBrain:
        return self._agents.setdefault(agent_id, _AgentBrain())

    # The check --------------------------------------------------------------------------------

    async def check(self) -> None:
        """Make each agent's brain right: one live session, started, replaced or handed over."""
        if self.runner is None:
            return
        async with self._lock:
            if self.clock() >= self._next_daily:
                self._next_daily = next_rollover(self.clock(), self.tz)
                with self._mutex:
                    for agent_id in self._agent_ids():
                        self._state(agent_id).wanted = 'daily'
            try:
                sessions = await self.runner.sessions()
            except RunnerUnavailable as exc:
                # Unknown is not dead: wait for the runner rather than start a second brain.
                log.info('Cannot see the brain sessions right now: %s', exc)
                return
            with self._mutex:
                self._seen = {s['id']: s for s in sessions if isinstance(s.get('id'), str)}
            for agent_id in self._agent_ids():
                await self._check_agent(agent_id)

    def _agent_ids(self) -> list[str]:
        with Session(self.engine) as s:
            return list(s.exec(select(Agent.id).order_by(col(Agent.id))))

    def _current(self, agent_id: str) -> str | None:
        with Session(self.engine) as s:
            agent = s.get(Agent, agent_id)
            assert agent is not None
            return ensure_main_conversation(s, agent).brain_session_id

    def _is_alive(self, session_id: str, st: _AgentBrain) -> bool:
        session = self._seen.get(session_id)
        if session is None:  # not listed: gone, unless the runner only just started it
            started = st.spawned_at.get(session_id)
            return started is not None and self.monotonic() - started < SPAWN_GRACE_S
        return _alive(session)

    async def _check_agent(self, agent_id: str) -> None:
        runner = self.runner
        assert runner is not None
        st = self._state(agent_id)
        # 1. Old brains handed over: stop them (retried until the runner takes it).
        for sid in sorted(st.retiring):
            if await self._stop(sid, 'it was replaced by a fresh one'):
                st.retiring.discard(sid)
        # 2. A new brain that died, or never said hello: give up on it, keep the old one.
        pending = st.pending
        if pending is not None:
            gone = not self._is_alive(pending.session_id, st)
            late = self.monotonic() - pending.since > HELLO_WAIT_S
            if late and not gone and pending.refused_busy:
                await self._put_off(agent_id, pending)
            elif gone or late:
                why = 'stopped before it said hello' if gone else 'never said hello'
                log.warning(
                    'The new main session %s %s; the old one carries on and the hub tries again.',
                    pending.session_id[:8],
                    why,
                )
                with self._mutex:
                    if st.pending is pending:
                        st.pending = None
                        st.wanted = st.wanted or pending.reason
                if not gone:
                    await self._stop(pending.session_id, 'it never said hello')
                self._failed(agent_id, f'The new session {why}.')
        # 3. No live brain: start one now (a waiting new one takes over at its hello instead).
        current = self._current(agent_id)
        if current is None or not self._is_alive(current, st):
            if st.pending is None:
                await self._respawn(agent_id, current)
        elif st.wanted is not None and st.pending is None:
            await self._start_rollover(agent_id, st.wanted)
        # 4. Any other live brain of this agent: stop it, so only one runs.
        keep = {self._current(agent_id)}
        if st.pending is not None:
            keep.add(st.pending.session_id)
        for session in list(self._seen.values()):
            sid = session['id']
            if (
                session.get('role') == 'brain'
                and session.get('name') == brain_name(agent_id)
                and sid not in keep
                and sid not in st.retiring
                and _alive(session)
            ):
                log.warning('Stopping an extra main session %s: only one may run.', sid[:8])
                await self._stop(sid, 'another main session is running')
        # 5. Read the current brain's turns, to see how full its context is.
        self._follow(agent_id, self._current(agent_id))

    def _follow(self, agent_id: str, session_id: str | None) -> None:
        """Keep one transcript reader per agent, on its current brain."""
        if self.turn_ends is None:
            return
        reading = self._tails.get(agent_id)
        if reading is not None and reading[0] == session_id and not reading[1].done():
            return
        if reading is not None:
            reading[1].cancel()
            del self._tails[agent_id]
        if session_id is None:
            return
        task = asyncio.get_running_loop().create_task(
            self._read_turns(session_id), name=f'brain-turns-{session_id[:8]}'
        )
        self._tails[agent_id] = (session_id, task)

    async def _read_turns(self, session_id: str) -> None:
        assert self.turn_ends is not None
        try:
            async for data in self.turn_ends(session_id):
                self.turn_ended(session_id, data)
        except Exception:  # the next check starts a new reader
            log.exception('Stopped reading how full main session %s is.', session_id[:8])

    async def _put_off(self, agent_id: str, pending: _Pending) -> None:
        """The new brain waited out its time because the old one stayed in a turn. That is not a
        failed start: no alert, no backoff count. Try later, and after `BUSY_TRIES` skip it."""
        st = self._state(agent_id)
        with self._mutex:
            if st.pending is not pending:
                return
            st.pending = None
            st.busy_tries += 1
            if st.busy_tries >= BUSY_TRIES:
                st.busy_tries = 0
                st.wanted = None
                log.warning(
                    'Skipped the %s rollover: Marcel stayed in a turn the whole time. The current '
                    'main session carries on.',
                    pending.reason,
                )
            else:
                st.wanted = pending.reason
                st.next_try = self.monotonic() + BUSY_RETRY_S
                log.info('Put off the %s rollover: Marcel is in a turn. Trying again in %.0f s.',
                         pending.reason, BUSY_RETRY_S)  # fmt: skip
        await self._stop(pending.session_id, 'Marcel was busy; the rollover waits')

    async def _stop(self, session_id: str, why: str) -> bool:
        assert self.runner is not None
        try:
            await self.runner.stop(session_id)
        except RunnerUnavailable as exc:
            log.warning('Could not stop main session %s (%s): %s', session_id[:8], why, exc)
            return False
        log.info('Stopped main session %s: %s.', session_id[:8], why)
        with self._mutex:  # until the next listing says so itself
            seen = self._seen.get(session_id)
            if seen is not None:
                self._seen[session_id] = {k: v for k, v in seen.items() if k != 'status'} | {
                    'state': 'stopped'
                }
        return True

    # Starting a brain -------------------------------------------------------------------------

    async def _spawn(self, agent_id: str, reason: str | None) -> tuple[str, str, datetime] | None:
        """Start a brain with its seed: (session id, what it kept, when the seed was written),
        or None (backing off)."""
        assert self.runner is not None
        st = self._state(agent_id)
        if self.monotonic() < st.next_try:
            return None
        seeded_at = self.clock()
        prompt, kept = self.seed(agent_id, reason)
        with Session(self.engine) as s:
            agent = s.get(Agent, agent_id)
            assert agent is not None
            model = agent.model_brain
        try:
            sid = await self.runner.spawn_brain(
                name=brain_name(agent_id), model=model, cwd=self.cwd, prompt=prompt
            )
        except (StartFailed, RunnerUnavailable) as exc:
            self._failed(agent_id, str(exc))
            return None
        st.spawned_at[sid] = self.monotonic()
        st.failures, st.next_try = 0, 0.0
        if st.alerted:
            st.alerted = False
            self.alerts.resolved(ALERT)
        return sid, kept, seeded_at

    def _failed(self, agent_id: str, why: str) -> None:
        st = self._state(agent_id)
        st.failures += 1
        wait = BACKOFF_S[min(st.failures, len(BACKOFF_S)) - 1]
        st.next_try = self.monotonic() + wait
        log.warning(
            "Marcel's main session did not start (%d in a row): %s Trying again in %.0f s.",
            st.failures,
            why,
            wait,
        )
        if st.failures >= ALERT_AFTER and not st.alerted:
            st.alerted = True
            self.alerts.broke(
                ALERT,
                'Marcel cannot start',
                f"Marcel's main session failed to start {st.failures} times in a row. The last "
                f'reason: {why} Your messages are kept and answered once it runs again; the hub '
                'keeps trying.',
            )

    async def _respawn(self, agent_id: str, dead: str | None) -> None:
        reason = 'crashed' if dead is not None else None
        started = await self._spawn(agent_id, reason)
        if started is None:
            return
        sid, kept, _ = started
        with self._mutex:
            self._set_brain(agent_id, sid)
            self._state(agent_id).wanted = None  # it is a fresh start already
        if dead is None:
            log.info("Started Marcel's main session %s.", sid[:8])
        else:
            log.warning("Marcel's main session %s was gone; started %s.", dead[:8], sid[:8])
            self._published(agent_id, 'crashed', kept, dead, sid)

    async def _start_rollover(self, agent_id: str, reason: str) -> None:
        started = await self._spawn(agent_id, reason)
        if started is None:
            return
        sid, kept, seeded_at = started
        with self._mutex:
            st = self._state(agent_id)
            st.pending = _Pending(sid, reason, kept, self.monotonic(), seeded_at)
            st.wanted = None
        log.info('Started main session %s (%s); it takes over at its hello.', sid[:8], reason)

    def _set_brain(self, agent_id: str, session_id: str) -> str:
        with Session(self.engine) as s:
            agent = s.get(Agent, agent_id)
            assert agent is not None
            conv = ensure_main_conversation(s, agent)
            conv.brain_session_id = session_id
            s.add(conv)
            s.commit()
            return conv.id

    def _published(self, agent_id: str, reason: str, kept: str, old: str | None, new: str) -> None:
        """Store the rollover in its history and send `brain.rollover`, in one write."""
        log.info('Brain rollover for %s (%s): %s', agent_id, reason, kept)
        with Session(self.engine) as s:
            agent = s.get(Agent, agent_id)
            assert agent is not None
            s.add(
                BrainRollover(
                    agent_id=agent_id,
                    at=self.clock(),
                    reason=reason,
                    kept=kept,
                    old_session_id=old,
                    new_session_id=new,
                )
            )
            if self.events is None:
                s.commit()
                return
            data = {'agent_id': agent_id, 'reason': reason, 'kept': kept}
            self.events.publish(s, agent.owner_user_id, 'brain.rollover', data)

    # The hand-over (the channel gateway's `Brains`) ------------------------------------------

    def said_hello(self, session_id: str) -> tuple[str, str] | None:
        with self._mutex:
            found = [
                (agent_id, st)
                for agent_id, st in self._agents.items()
                if st.pending is not None and st.pending.session_id == session_id
            ]
            if not found:
                return None
            agent_id, st = found[0]
            pending = st.pending
            assert pending is not None
            old = self._current(agent_id)
            seen = self._seen.get(old) if old is not None else None
            old_alive = seen is not None and _alive(seen)
            if old_alive and self._in_turn(agent_id, seen):
                # Refused for now; the channel server says hello again in 5 s.
                pending.refused_busy = True
                log.info('New main session %s waits for the old one to finish its turn.',
                         session_id[:8])  # fmt: skip
                return None
            conversation_id = self._set_brain(agent_id, session_id)
            st.pending = None
            st.busy_tries = 0
            if old is not None:
                st.retiring.add(old)
            self._handed[session_id] = (conversation_id, pending.seeded_at)
        self._published(agent_id, pending.reason, pending.kept, old, session_id)
        self.poke()  # stop the old one now
        return agent_id, conversation_id

    def welcomed(self, session_id: str) -> None:
        """The new brain's socket holds the conversation now: send it what its seed could not
        hold (the gateway's `hand_over`). Any other session: nothing to do."""
        with self._mutex:
            handed = self._handed.pop(session_id, None)
        if handed is None or self.hand_over is None:
            return
        try:
            self.hand_over(*handed)
        except Exception:  # the hand-over itself has happened; say what is missing
            log.exception('Could not send the new main session the latest messages.')

    def _in_turn(self, agent_id: str, seen: dict[str, Any] | None) -> bool:
        """The old brain is answering: the runner shows it working (as of its last listing), or
        the hub's own turn tracking has an owner message it has not replied to yet."""
        if seen is not None and _busy(seen):
            return True
        if self.turn_open is None:
            return False
        with Session(self.engine) as s:
            agent = s.get(Agent, agent_id)
            assert agent is not None
            main_id = ensure_main_conversation(s, agent).id
        return self.turn_open(agent_id, main_id)

    # The seed ---------------------------------------------------------------------------------

    def seed(self, agent_id: str, reason: str | None) -> tuple[str, str]:
        """The new brain's first prompt, and what it kept in plain words. Built from the hub's
        own records — no model call."""
        since = self.clock() - timedelta(hours=SUMMARY_HOURS)
        with Session(self.engine) as s:
            agent = s.get(Agent, agent_id)
            assert agent is not None
            main = ensure_main_conversation(s, agent)
            messages = list(
                s.exec(
                    select(Message)
                    .where(Message.conversation_id == main.id)
                    .where(Message.created_at >= since)
                    .order_by(col(Message.created_at).desc())
                    .limit(MAX_MESSAGES)
                )
            )[::-1]
            tasks = list(
                s.exec(
                    select(Task)
                    .where(Task.agent_id == agent_id)
                    .where(col(Task.state).in_(list(OPEN_TASKS)))
                    .where(col(Task.archived_at).is_(None))
                    .order_by(col(Task.created_at))
                    .limit(MAX_TASKS)
                )
            )
            projects = {p.id: p.slug for p in s.exec(select(Project))}
            name = agent.name
        notes = self._notes()

        why = REASON_WORDS.get(reason or '', 'the first start')
        parts = [
            f'You are {name}, starting as a fresh main session ({why}). The conversation with '
            'the family goes on as before: do not greet or announce yourself. Below is what the '
            'hub kept for you, from its own records. Read it, then wait for channel messages.'
        ]
        if notes:
            parts.append(f'## Your notes (MEMORY.md)\n\n{notes}')
        if tasks:
            lines = [
                f'- {t.title} — {t.state.value}, on {t.location.value}'
                + (f', project {projects[t.project_id]}' if t.project_id in projects else '')
                + f' (task {t.id})'
                for t in tasks
            ]
            parts.append('## Open threads\n\n' + '\n'.join(lines))
        if messages:
            lines = [self._line(m, name) for m in messages]
            parts.append('## The main chat, last 24 hours\n\n' + '\n'.join(lines))
        else:
            parts.append('## The main chat, last 24 hours\n\nNothing was said.')
        return '\n\n'.join(parts), _kept(bool(notes), len(messages), len(tasks))

    def _notes(self) -> str:
        if self.memory is None:
            return ''
        try:
            found = self.memory.read_slice('MEMORY.md', MAX_MEMORY)
        except Exception:  # the seed must never fail because of memory
            log.exception('Could not read MEMORY.md for the new main session.')
            return ''
        if found is None:
            return ''
        text, cut = found
        text = text.strip()
        return text + ('\n\n(cut short; read MEMORY.md for the rest)' if cut else '')

    def _line(self, msg: Message, name: str) -> str:
        at = msg.created_at if msg.created_at.tzinfo else msg.created_at.replace(tzinfo=UTC)
        local = at.astimezone(self.tz) if self.tz is not None else at.astimezone()
        who = {MessageRole.user: 'Owner', MessageRole.agent: name}.get(msg.role, 'Marcel hub')
        body = msg.body_json or {}
        if msg.kind == MessageKind.milestone:
            m = body.get('milestone', {})
            who = f'Report from thread "{m.get("task_title", "?")}"'
            text = f'[{m.get("event", "?")}]'
            if m.get('summary'):
                text += f' {m["summary"]}'
        elif msg.kind == MessageKind.approval:
            text = '[an approval card]'
        elif msg.kind == MessageKind.artifact:
            text = f'[an item arrived: {msg.artifact_id}]'
        else:
            text = str(body.get('text', ''))
        text = ' '.join(text.split())
        if len(text) > MAX_LINE:
            text = text[: MAX_LINE - 1] + '…'
        return f'- {local:%H:%M} {who}: {text}'


def _kept(notes: bool, messages: int, tasks: int) -> str:
    """`brain.rollover` `kept`, e.g. "Your notes, the last 40 messages and 3 open threads."."""
    said = ['your notes'] if notes else []
    said.append(f'the last {messages} message{"s" if messages != 1 else ""} of the past day')
    said.append(f'{tasks} open thread{"s" if tasks != 1 else ""}')
    text = ', '.join(said[:-1]) + ' and ' + said[-1]
    return text[0].upper() + text[1:] + '.'


# The app's routes (app-api.yaml `agents`, S-06.7) ------------------------------------------------

router = APIRouter(prefix='/api', tags=['agents'], dependencies=[Depends(require_device)])


def _owned(session: Session, request: Request, agent_id: str) -> Agent:
    agent = session.get(Agent, agent_id)
    if agent is None or agent.owner_user_id != current_device(request).user_id:
        raise ApiError(404, 'agent_not_found', 'That agent is not on this hub.')
    return agent


def rollover_json(row: BrainRollover) -> dict[str, Any]:
    """The contract's `Rollover`."""
    at = row.at if row.at.tzinfo else row.at.replace(tzinfo=UTC)
    return {
        'id': row.id,
        'agent_id': row.agent_id,
        'at': at.astimezone(UTC).isoformat().replace('+00:00', 'Z'),
        'reason': row.reason,
        'kept': row.kept,
    }


@router.post('/agents/{agent_id}/brain/rollover', status_code=202)
async def roll_over_brain(agent_id: str, request: Request, session: DbSession) -> Response:
    agent = _owned(session, request, agent_id)
    keeper: BrainKeeper | None = request.app.state.brain
    if keeper is None or keeper.runner is None:
        raise ApiError(
            503,
            'runner_unreachable',
            f'{agent.name} has no main session on this hub to refresh: the brain workspace is '
            'not set up. Nothing changed.',
        )
    busy = ApiError(
        409,
        'rollover_running',
        f'{agent.name} is already starting a fresh session. It takes over in a minute or two.',
    )
    if keeper.rolling_over(agent_id):
        raise busy
    try:
        await keeper.runner.sessions()
    except RunnerUnavailable as exc:
        raise ApiError(
            503,
            'runner_unreachable',
            f'{agent.name} cannot start a fresh session just now: {exc} Nothing changed.',
        ) from exc
    if not keeper.roll_over(agent_id, 'manual'):
        raise busy
    return Response(status_code=202)


@router.get('/agents/{agent_id}/brain/rollovers')
def list_brain_rollovers(
    agent_id: str,
    request: Request,
    session: DbSession,
    limit: int = Query(default=10, ge=1, le=MAX_ROLLOVERS),
) -> list[dict[str, Any]]:
    _owned(session, request, agent_id)
    rows = session.exec(
        select(BrainRollover)
        .where(BrainRollover.agent_id == agent_id)
        .order_by(col(BrainRollover.at).desc(), col(BrainRollover.id).desc())
        .limit(limit)
    )
    return [rollover_json(r) for r in rows]
