"""Side threads (S-06.3, B-08): each side thread runs in its own fork of the brain.

A side thread that is not a task's (`Conversation.task_id` unset; task threads are S-04.9's and go
to their task) gets a `side` session: the runner forks the main conversation's brain
(`runner.fork`, `claude --bg --resume <brain> --fork-session`, SP7), so the fork knows the main
chat up to that moment and answers on its own channel connection. Its session id is stored as the
side conversation's `brain_session_id`; the channel gateway resolves it to the role `side` and
sends it that conversation's frames only. Nothing written in a side thread reaches the main brain,
unless the owner brings it back with "send to main" (a quote in the main conversation).

- **When a fork starts.** When a side thread is opened (`conversation.created`), and whenever the
  owner writes in one whose session is not running. Messages are addressed to the conversation,
  not the session, so they wait in the channel Outbox until a session says hello: none is lost.
- **Idle forks stop.** A side session with nothing said in its thread for `IDLE_S` (30 minutes),
  between turns and with nothing waiting for it, is stopped. The id stays on the conversation; the
  next message wakes it with `runner.resume` (same session, its own history). If it cannot be
  woken (gone, copied, refused), the brain is forked afresh, with the thread so far in its prompt.
- **At most `MAX_SIDES` run at once.** Each fork is a whole `claude` process on the NUC and the
  runner's worker cap never counts it, so the hub caps them itself. A fork that needs room stops
  the side session that has been quiet the longest (between turns, nothing waiting); when every
  one is busy, the thread waits (one plain note says so) and is tried again on the next check.
- **A failed start is said in the thread, not retried for ever.** The first failure posts a note
  with the runner's reason; the hub tries `TRIES` times in all (after 15 s, then 1 min), then
  posts a last note and stops trying until the owner writes there again.
- **A brain rollover leaves side threads alone.** A fork is its own session: when the old brain is
  stopped, its forks keep running until they go idle. Re-forks start from whichever brain is
  current. The brain keeper only ever counts sessions with the role `brain`.

A side session spends plan usage like any session; like the brain it is started through the runner
directly, never through the task registry, so the usage guard never holds it back.
"""

import asyncio
import contextlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from fastapi import FastAPI
from sqlmodel import Session, col, func, select

from marcel_hub.brain import _alive, _busy
from marcel_hub.channel.outbox import ChannelOutbox, conversation_target
from marcel_hub.conversations import ensure_main_conversation, post_message, quoted_text
from marcel_hub.models import (
    Agent,
    Conversation,
    ConversationKind,
    Message,
    MessageKind,
    MessageRole,
)
from marcel_hub.tasks.ports import RunnerUnavailable, StartFailed
from marcel_hub.ws import EventLog

log = logging.getLogger(__name__)

IDLE_S = 30 * 60.0  # a side session quiet this long is stopped
TICK_S = 10.0  # how often idle sessions and waiting threads are looked at
MAX_SIDES = 3  # side sessions running at once, hub-wide
SPAWN_GRACE_S = 60.0  # a session just started may not be in the runner's list yet
TRIES = 3
BACKOFF_S = (15.0, 60.0)
MAX_HISTORY = 30  # the thread's messages a fresh fork is given
MAX_LINE = 400
STARTING = {'role': 'side', 'state': 'working', 'status': 'busy'}  # until the runner lists it
ASKS = frozenset({MessageKind.text.value, MessageKind.quote.value})

RESUME_PROMPT = (
    'The owner wrote again in this side thread. Read the new channel messages and answer them '
    'with the reply tool.'
)


class SideRunner(Protocol):
    """What side threads need from the runner (`runner_client.RunnerClient`)."""

    async def fork(self, session_id: str, *, name: str, prompt: str) -> str: ...

    async def resume(self, session_id: str, prompt: str) -> None: ...

    async def sessions(self) -> list[dict[str, Any]]: ...

    async def stop(self, session_id: str) -> None: ...


def side_name(conversation_id: str) -> str:
    """The runner's name for a side thread's session (`ForkRequest.name`)."""
    return f'side-{conversation_id}'[:60]


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _cut(text: str) -> str:
    text = ' '.join(text.split())
    return text if len(text) <= MAX_LINE else text[: MAX_LINE - 1] + '…'


@dataclass
class _Want:
    """A side thread that needs a running session."""

    failures: int = 0
    next_try: float = 0.0
    told_full: bool = False  # the "waits for room" note is posted


class SideThreads:
    def __init__(
        self,
        engine: Any,
        runner: SideRunner | None,
        *,
        events: EventLog,
        outbox: ChannelOutbox,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.engine = engine
        self.runner = runner
        self.events = events
        self.outbox = outbox
        self.clock = clock
        self.monotonic = monotonic
        self._lock = asyncio.Lock()  # one check at a time: never two forks for one thread
        self._mutex = threading.Lock()  # `_wanted`, shared with the event observer
        self._wanted: dict[str, _Want] = {}
        self._started: dict[str, datetime] = {}  # session id → when this hub forked or woke it
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._job: asyncio.Task[None] | None = None

    # Lifecycle --------------------------------------------------------------------------------

    def start(self, tick: float | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._job = self._loop.create_task(self._run(tick or TICK_S), name='side-threads')

    async def stop(self) -> None:
        if self._job is not None:
            self._job.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._job
            self._job = None

    def poke(self) -> None:
        """Check soon. Safe from any thread."""
        if self._loop is not None and self._wake is not None:
            with contextlib.suppress(RuntimeError):  # the loop is closed: the hub is stopping
                self._loop.call_soon_threadsafe(self._wake.set)

    async def _run(self, tick: float) -> None:
        assert self._wake is not None
        while True:
            try:
                await self.check()
            except Exception:  # one bad check must not stop side threads for good
                log.exception('Could not check the side threads.')
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), tick)
            self._wake.clear()

    # What starts a fork -----------------------------------------------------------------------

    def observe(self, _user_id: str, type_: str, data: dict[str, Any]) -> None:
        """`EventLog` observer: a side thread opened, or the owner wrote in one."""
        if type_ == 'conversation.created':
            if data.get('kind') == ConversationKind.side.value and not data.get('task_id'):
                self.want(data['id'])
            return
        if type_ != 'message.created' or data.get('role') != MessageRole.user.value:
            return
        if data.get('kind') not in ASKS:
            return
        with Session(self.engine) as s:
            conv = s.get(Conversation, data['conversation_id'])
        if conv is not None and conv.kind == ConversationKind.side and conv.task_id is None:
            self.want(conv.id)

    def want(self, conversation_id: str) -> None:
        """Make sure the side thread has a running session (soon, on the keeper's loop)."""
        with self._mutex:
            self._wanted.setdefault(conversation_id, _Want())
        self.poke()

    def wanted(self) -> list[str]:
        with self._mutex:
            return list(self._wanted)

    # The check --------------------------------------------------------------------------------

    async def check(self) -> None:
        """Start or wake every side session wanted, then stop the idle ones."""
        if self.runner is None:
            return
        async with self._lock:
            try:
                listing = await self.runner.sessions()
            except RunnerUnavailable as exc:
                # Unknown is not dead: nothing is started or stopped on a guess.
                log.info('Cannot see the side sessions right now: %s', exc)
                return
            seen = {s['id']: s for s in listing if isinstance(s.get('id'), str)}
            for conversation_id in self.wanted():
                await self._ensure(conversation_id, seen)
            await self._sweep(seen)

    def _alive(self, session_id: str, seen: dict[str, dict[str, Any]]) -> bool:
        session = seen.get(session_id)
        if session is None:  # not listed: gone, unless this hub only just started it
            started = self._started.get(session_id)
            return started is not None and (self.clock() - started).total_seconds() < SPAWN_GRACE_S
        return _alive(session)

    async def _ensure(self, conversation_id: str, seen: dict[str, dict[str, Any]]) -> None:
        runner = self.runner
        assert runner is not None
        with self._mutex:
            want = self._wanted.get(conversation_id)
        if want is None or self.monotonic() < want.next_try:
            return
        with Session(self.engine) as s:
            conv = s.get(Conversation, conversation_id)
            if conv is None or conv.kind != ConversationKind.side or conv.task_id is not None:
                self._settled(conversation_id)  # a task's thread goes to its task (S-04.9)
                return
            agent = s.get(Agent, conv.agent_id)
            assert agent is not None
            brain_sid = ensure_main_conversation(s, agent).brain_session_id
            current = conv.brain_session_id
        if current is not None and self._alive(current, seen):
            self._settled(conversation_id)
            return
        if not await self._room(conversation_id, want, seen):
            return
        if current is not None:
            try:
                await runner.resume(current, RESUME_PROMPT)
            except StartFailed as exc:
                log.info('Could not wake side session %s (%s); forking the brain afresh.',
                         current[:8], exc)  # fmt: skip
            except RunnerUnavailable as exc:
                self._failed(conversation_id, want, str(exc))
                return
            else:
                self._running(conversation_id, current, seen)
                log.info('Woke side session %s for thread %s.', current[:8], conversation_id)
                return
        if brain_sid is None or not self._alive(brain_sid, seen):
            self._failed(conversation_id, want, 'Marcel’s main session is not running right now.')
            return
        try:
            sid = await runner.fork(
                brain_sid, name=side_name(conversation_id), prompt=self.prompt(conversation_id)
            )
        except (StartFailed, RunnerUnavailable) as exc:
            self._failed(conversation_id, want, str(exc))
            return
        with Session(self.engine) as s:
            conv = s.get(Conversation, conversation_id)
            assert conv is not None
            conv.brain_session_id = sid
            s.add(conv)
            s.commit()
        self._running(conversation_id, sid, seen)
        log.info('Forked the brain into side session %s for thread %s.', sid[:8], conversation_id)

    def _running(self, conversation_id: str, sid: str, seen: dict[str, dict[str, Any]]) -> None:
        self._started[sid] = self.clock()
        seen[sid] = {'id': sid, **STARTING}
        self._settled(conversation_id)

    def _settled(self, conversation_id: str) -> None:
        with self._mutex:
            self._wanted.pop(conversation_id, None)

    # Room: at most MAX_SIDES ------------------------------------------------------------------

    def _sides(self) -> dict[str, str]:
        """Session id → its side thread, for every side thread that has a session."""
        with Session(self.engine) as s:
            rows = s.exec(
                select(Conversation.brain_session_id, Conversation.id)
                .where(Conversation.kind == ConversationKind.side)
                .where(col(Conversation.task_id).is_(None))
                .where(col(Conversation.brain_session_id).is_not(None))
            ).all()
        return {sid: cid for sid, cid in rows if sid is not None}

    def _quiet(self, conversation_id: str, session: dict[str, Any]) -> bool:
        """Between turns, with no message of the owner's waiting for it: safe to stop."""
        if _busy(session):
            return False
        return not self.outbox.pending(conversation_target(conversation_id))

    def _quiet_since(self, conversation_id: str, session_id: str) -> datetime:
        with Session(self.engine) as s:
            last = s.exec(
                select(func.max(Message.created_at)).where(
                    Message.conversation_id == conversation_id
                )
            ).one()
        times = [_aware(t) for t in (last, self._started.get(session_id)) if t is not None]
        return max(times) if times else datetime.min.replace(tzinfo=UTC)

    async def _room(
        self, conversation_id: str, want: _Want, seen: dict[str, dict[str, Any]]
    ) -> bool:
        """True when one more side session may run; stops the quietest one if needed."""
        sides = self._sides()
        known = {sid for sid, s in seen.items() if s.get('role') == 'side'} | set(self._started)
        live = sorted(sid for sid in known if self._alive(sid, seen))
        while len(live) >= MAX_SIDES:
            stoppable = [
                (self._quiet_since(sides[sid], sid), sid)
                for sid in live
                if sid in sides
                and sides[sid] != conversation_id
                and self._quiet(sides[sid], seen.get(sid, STARTING))
            ]
            if not stoppable:
                if not want.told_full:
                    want.told_full = True
                    self._note(
                        conversation_id,
                        f'Marcel is busy in {len(live)} other side threads right now. This one '
                        'starts as soon as one of them is free; your messages wait here.',
                    )
                log.info('Side thread %s waits: %d side sessions run.', conversation_id, len(live))
                return False
            _, sid = min(stoppable)
            if not await self._stop(sid, 'another side thread needed the room'):
                return False
            seen[sid] = {'id': sid, 'role': 'side', 'state': 'stopped'}
            live.remove(sid)
        return True

    async def _stop(self, session_id: str, why: str) -> bool:
        assert self.runner is not None
        try:
            await self.runner.stop(session_id)
        except RunnerUnavailable as exc:
            log.warning('Could not stop side session %s (%s): %s', session_id[:8], why, exc)
            return False
        log.info('Stopped side session %s: %s.', session_id[:8], why)
        self._started.pop(session_id, None)
        return True

    # Idle sessions ----------------------------------------------------------------------------

    async def _sweep(self, seen: dict[str, dict[str, Any]]) -> None:
        """Stop each side session that has been quiet for `IDLE_S`. Its id stays on the thread,
        so the next message wakes it."""
        now = self.clock()
        for sid, conversation_id in self._sides().items():
            session = seen.get(sid)
            if session is None or not _alive(session) or not self._quiet(conversation_id, session):
                continue
            quiet = now - self._quiet_since(conversation_id, sid)
            if quiet < timedelta(seconds=IDLE_S):
                continue
            why = f'nothing was said in its thread for {int(quiet.total_seconds() // 60)} minutes'
            if await self._stop(sid, why):
                seen[sid] = {'id': sid, 'role': 'side', 'state': 'stopped'}

    # Failures ---------------------------------------------------------------------------------

    def _failed(self, conversation_id: str, want: _Want, why: str) -> None:
        want.failures += 1
        log.warning('Side thread %s did not start (%d of %d): %s',
                    conversation_id, want.failures, TRIES, why)  # fmt: skip
        why = why.rstrip('.') + '.'
        if want.failures >= TRIES:
            self._settled(conversation_id)
            self._note(
                conversation_id,
                f'Marcel could not open this side thread: {why} Nothing you wrote is lost. '
                'Write here again to try once more.',
            )
            return
        want.next_try = self.monotonic() + BACKOFF_S[want.failures - 1]
        if want.failures == 1:
            self._note(
                conversation_id,
                f'Marcel could not open this side thread yet: {why} It tries again in a moment; '
                'your messages wait here.',
            )

    def _note(self, conversation_id: str, text: str) -> None:
        """A plain-words message from the hub in the side thread (never sent to a session)."""
        with Session(self.engine) as s:
            conv = s.get(Conversation, conversation_id)
            if conv is None:
                return
            post_message(
                s, self.events, conv, role=MessageRole.system, kind=MessageKind.text,
                body={'text': text},
            )  # fmt: skip

    # The fork's first prompt ------------------------------------------------------------------

    def prompt(self, conversation_id: str) -> str:
        """What a new fork is told: it is a side thread, the message it was opened on, and (for a
        re-fork) what was said in the thread so far. Built from the hub's records, no model call.
        Owner messages still waiting in the Outbox are left out: they arrive on the channel."""
        waiting = {
            f.get('message_id') for f in self.outbox.pending(conversation_target(conversation_id))
        }
        with Session(self.engine) as s:
            conv = s.get(Conversation, conversation_id)
            assert conv is not None
            parent = s.get(Message, conv.parent_message_id) if conv.parent_message_id else None
            said = list(
                s.exec(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .where(col(Message.role) != MessageRole.system)
                    .order_by(col(Message.created_at).desc())
                    .limit(MAX_HISTORY)
                )
            )[::-1]
        parts = [
            'You are now a side thread, forked from the main conversation. Answer only here, with '
            'the reply tool. The main conversation goes on without you: nothing said here reaches '
            'it unless the owner sends it there. Do not greet or announce yourself.'
        ]
        if parent is not None:
            parts.append(
                f'The owner opened this thread on the message: "{_cut(quoted_text(parent))}"'
            )
        lines = [
            f'- {"Owner" if m.role == MessageRole.user else "You"}: {_cut(quoted_text(m))}'
            for m in said
            if m.id not in waiting
        ]
        if lines:
            parts.append('## This side thread so far\n\n' + '\n'.join(lines))
        parts.append('Wait for channel messages.')
        return '\n\n'.join(parts)


def install(app: FastAPI, runner: SideRunner | None, outbox: ChannelOutbox) -> SideThreads:
    """Put the side threads on `app.state.sides` and let them watch the app event log."""
    sides = SideThreads(app.state.engine, runner, events=app.state.events, outbox=outbox)
    app.state.events.observers.append(sides.observe)
    app.state.sides = sides
    return sides
