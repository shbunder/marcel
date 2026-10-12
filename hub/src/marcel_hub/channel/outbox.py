"""The Outbox: hub → session frames, numbered and kept in the database until the session acks them.

A frame is addressed to a **target**, not a session: `task:<id>` for a worker (so a steer for a
task that is still `queued` waits for the session that will run it) and `conversation:<id>` for
the brain or a side thread. `seq` counts up by one per target and never restarts, because a
channel server ignores any `seq` at or below the highest it has delivered.

Nothing is held in memory per connection but a wake-up flag: a connected session's sender reads
the frames after the last one it sent straight from the database, a page at a time. A session
that stops reading cannot make the hub buffer frames for it.

**Waking the brain (S-06.5).** Whether an event is worth a brain turn is decided by the hub's
`Triage` (`marcel_hub.triage`), here: `decide` asks it and logs the decision, and every
`system_event` written for a conversation — by the gateway, the task registry (handback), the
scheduler or the digests — passes through `put`, which queues it only if the decision is a brain
turn for that conversation. Anything else is logged and not sent (`put` returns 0).
"""

import asyncio
import contextlib
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, func, update
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from marcel_hub.conversations import ensure_main_conversation
from marcel_hub.models import Agent, ChannelFrame
from marcel_hub.triage import BrainTurn, Decision, Event, Kind, RulesTriage, Triage, record

log = logging.getLogger(__name__)

PAGE = 100


def task_target(task_id: str) -> str:
    return f'task:{task_id}'


def conversation_target(conversation_id: str) -> str:
    return f'conversation:{conversation_id}'


CONVERSATION = 'conversation:'
# channel.schema.json SystemEvent.kind → the triage event it is.
SYSTEM_EVENTS = {
    'task_failed': Kind.worker_failed,
    'task_silent': Kind.worker_silent,
    'task_question': Kind.worker_question,
    'handback': Kind.handback,
    'digest_due': Kind.digest_due,
    'schedule_fired': Kind.schedule_fired,
    'rollover': Kind.rollover,
}


@dataclass(eq=False)
class Wake:
    """Wakes one connection's sender from any thread."""

    loop: asyncio.AbstractEventLoop
    event: asyncio.Event = field(default_factory=asyncio.Event)

    def set(self) -> None:
        with contextlib.suppress(RuntimeError):  # a closed loop: the connection is gone
            self.loop.call_soon_threadsafe(self.event.set)


class ChannelOutbox:
    """Implements the task registry's `Outbox` (`tasks/ports.py`), plus conversation frames."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        # One lock for numbering, storing and waking, so seq N+1 is never stored before seq N.
        self._lock = threading.Lock()
        self._wakes: dict[str, Wake] = {}
        self.triage: Triage = RulesTriage()

    # Waking the brain -------------------------------------------------------------------------

    def decide(self, event: Event) -> Decision:
        """Whether `event` is worth a brain turn: the triage decides, and the decision is logged."""
        return record(self.triage, event)

    def _system_event_wakes(self, target: str, frame: dict[str, Any]) -> bool:
        conversation_id = target.removeprefix(CONVERSATION)
        event = Event(
            SYSTEM_EVENTS[frame['kind']],
            conversation_id=conversation_id,
            task_id=frame.get('task_id'),
        )
        decision = self.decide(event)
        if isinstance(decision, BrainTurn) and decision.conversation_id in (None, conversation_id):
            return True
        log.info('Did not send the %s event to %s: no brain turn for it.', frame['kind'], target)
        return False

    # Writing ----------------------------------------------------------------------------------

    def put(self, target: str, frame: dict[str, Any]) -> int:
        """Number `frame`, store it, and wake the target's connection if it has one. A
        `system_event` for a conversation is queued only if the triage wakes the brain for it;
        otherwise nothing is stored and 0 is returned."""
        is_event = frame.get('type') == 'system_event' and target.startswith(CONVERSATION)
        if is_event and not self._system_event_wakes(target, frame):
            return 0
        with self._lock, Session(self.engine) as s:
            last = s.exec(select(func.max(ChannelFrame.seq)).where(ChannelFrame.target == target))
            seq = (last.one() or 0) + 1
            s.add(ChannelFrame(target=target, seq=seq, frame_json={**frame, 'seq': seq}))
            s.commit()
            wake = self._wakes.get(target)
        if wake is not None:
            wake.set()
        return seq

    async def to_task(self, task_id: str, frame: dict[str, Any]) -> None:
        self.put(task_target(task_id), frame)

    async def to_brain(self, agent_id: str, frame: dict[str, Any]) -> None:
        self.put(conversation_target(self.main_conversation(agent_id)), frame)

    def to_conversation(self, conversation_id: str, frame: dict[str, Any]) -> None:
        self.put(conversation_target(conversation_id), frame)

    def main_conversation(self, agent_id: str) -> str:
        with Session(self.engine) as s:
            agent = s.get(Agent, agent_id)
            if agent is None:
                raise LookupError(f'There is no agent {agent_id}.')
            return ensure_main_conversation(s, agent).id

    # Reading ----------------------------------------------------------------------------------

    def after(self, target: str, seq: int, limit: int = PAGE) -> list[dict[str, Any]]:
        """Unacked frames of `target` after `seq`, oldest first."""
        with Session(self.engine) as s:
            rows = s.exec(
                select(ChannelFrame)
                .where(ChannelFrame.target == target)
                .where(ChannelFrame.seq > seq)
                .where(col(ChannelFrame.acked).is_(False))
                .order_by(col(ChannelFrame.seq))
                .limit(limit)
            )
            return [row.frame_json for row in rows]

    def pending(self, target: str) -> list[dict[str, Any]]:
        return self.after(target, 0, limit=10_000)

    def ack(self, target: str, seq: int, through: bool = False) -> None:
        """The session delivered `seq` (or, with `through`, everything up to it). Acked rows are
        removed, except the newest of the target, which keeps the numbering."""
        with self._lock, Session(self.engine) as s:
            seqs = col(ChannelFrame.seq) <= seq if through else col(ChannelFrame.seq) == seq
            s.exec(  # type: ignore[call-overload]
                update(ChannelFrame)
                .where(col(ChannelFrame.target) == target, seqs)
                .values(acked=True)
            )
            newest = s.exec(
                select(func.max(ChannelFrame.seq)).where(ChannelFrame.target == target)
            ).one()
            s.exec(  # type: ignore[call-overload]
                delete(ChannelFrame).where(
                    col(ChannelFrame.target) == target,
                    col(ChannelFrame.acked).is_(True),
                    col(ChannelFrame.seq) < (newest or 0),
                )
            )
            s.commit()

    def clear(self, target: str) -> int:
        """Drop every unacked frame of `target` (its session is gone); keeps the numbering."""
        with self._lock, Session(self.engine) as s:
            newest = s.exec(
                select(func.max(ChannelFrame.seq)).where(ChannelFrame.target == target)
            ).one()
            dropped = s.exec(  # type: ignore[call-overload]
                update(ChannelFrame)
                .where(col(ChannelFrame.target) == target, col(ChannelFrame.acked).is_(False))
                .values(acked=True)
            ).rowcount
            s.exec(  # type: ignore[call-overload]
                delete(ChannelFrame).where(
                    col(ChannelFrame.target) == target, col(ChannelFrame.seq) < (newest or 0)
                )
            )
            s.commit()
        return dropped

    # Connections ------------------------------------------------------------------------------

    def attach(self, target: str, wake: Wake) -> Wake | None:
        """Make `wake` the target's connection; returns the one it replaces."""
        with self._lock:
            old = self._wakes.get(target)
            self._wakes[target] = wake
        wake.set()
        return old

    def detach(self, target: str, wake: Wake) -> None:
        with self._lock:
            if self._wakes.get(target) is wake:
                del self._wakes[target]

    def connected(self, target: str) -> bool:
        return target in self._wakes
