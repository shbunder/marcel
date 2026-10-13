"""Approval cards (B-12): opened from a worker's permission prompt, answered once.

- A `permission_request` opens one card per `request_id` and task; a repeat (the channel server
  re-sends every unanswered prompt after a reconnect) opens nothing, whatever the card's state.
- The first answer wins. Every state change is one conditional `UPDATE … WHERE state = 'open'`,
  so the app and a terminal answering at the same moment cannot both win.
- An answer from the app sends `permission_decision` to the session. Nobody else can: a session
  never answers its own prompt, and only the agent's owner sees or answers its cards.
- A prompt answered in a terminal is read from the transcript (`tool_result.denied`), once the
  runner shows the session left the prompt. A task that stops or fails expires its open cards.
- A self-change card (S-11.1, tool `self_change`, no `request_id`) is opened by `selfmod.py`, not
  by a prompt. Only the app answers it, and it outlives its task: the PR is still there when the
  worker that made it has finished. `selfmod.py` closes it when the PR merges or closes.
"""

import logging
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlmodel import Session, col, select

from marcel_hub.models import (
    Agent,
    AnsweredVia,
    Approval,
    ApprovalState,
    Task,
    TaskEvent,
    TaskEventType,
    utcnow,
)
from marcel_hub.tasks.registry import iso
from marcel_hub.ws import EventLog

log = logging.getLogger(__name__)

SELF_CHANGE_TOOL = 'self_change'  # app-api.yaml `Approval.tool` for a self-modification PR


def approval_json(row: Approval) -> dict[str, Any]:
    """The contract's `Approval`; unset fields left out."""
    body: dict[str, Any] = {
        'id': row.id,
        'task_id': row.task_id,
        'tool': row.tool,
        'summary': row.summary,
        'state': row.state.value,
        'created_at': iso(row.created_at),
    }
    for key in ('detail', 'artifact_id'):
        if getattr(row, key) is not None:
            body[key] = getattr(row, key)
    if row.answered_via is not None:
        body['answered_via'] = row.answered_via.value
    if row.answered_at is not None:
        body['answered_at'] = iso(row.answered_at)
    return body


def owner_of_task(s: Session, task_id: str) -> str | None:
    task = s.get(Task, task_id)
    agent = s.get(Agent, task.agent_id) if task else None
    return agent.owner_user_id if agent else None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=utcnow().tzinfo)


class ApprovalBook:
    def __init__(self, engine: Any, events: Callable[[], EventLog | None]) -> None:
        self.engine = engine
        self._events = events
        self._lock = threading.Lock()

    # Reading ----------------------------------------------------------------------------------

    def get(self, approval_id: str) -> Approval | None:
        with Session(self.engine) as s:
            return s.get(Approval, approval_id)

    def open_on(self, task_id: str) -> list[Approval]:
        with Session(self.engine) as s:
            return list(
                s.exec(
                    select(Approval)
                    .where(Approval.task_id == task_id)
                    .where(Approval.state == ApprovalState.open)
                    .order_by(col(Approval.created_at), col(Approval.id))
                )
            )

    def for_owner(self, user_id: str, state: ApprovalState | None = None) -> list[Approval]:
        """The owner's cards, open ones first, then newest first."""
        query = (
            select(Approval)
            .join(Task, col(Task.id) == col(Approval.task_id))
            .join(Agent, col(Agent.id) == col(Task.agent_id))
            .where(Agent.owner_user_id == user_id)
        )
        if state is not None:
            query = query.where(Approval.state == state)
        with Session(self.engine) as s:
            rows = list(s.exec(query))
        rows.sort(key=lambda a: (a.state != ApprovalState.open, -_aware(a.created_at).timestamp()))
        return rows

    # Writing ----------------------------------------------------------------------------------

    def open(self, task_id: str, request: dict[str, Any]) -> Approval | None:
        """A card for this prompt, or None when the task already holds its `request_id`."""
        with self._lock, Session(self.engine) as s:
            held = s.exec(
                select(Approval)
                .where(Approval.task_id == task_id)
                .where(Approval.request_id == request['request_id'])
            ).first()
            if held is not None:
                return None
            row = Approval(
                task_id=task_id,
                request_id=request['request_id'],
                tool=request['tool_name'],
                summary=request['description'],
                detail=request.get('input_preview'),
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        self._publish('approval.created', row)
        return row

    def open_self_change(
        self, task_id: str, artifact_id: str, summary: str, detail: str
    ) -> Approval:
        """A self-change card for a PR artifact (S-11.1). It has no `request_id`: no session
        waits on it, so nothing is sent to a session when it is answered."""
        with self._lock, Session(self.engine) as s:
            row = Approval(
                task_id=task_id,
                request_id=None,
                tool=SELF_CHANGE_TOOL,
                artifact_id=artifact_id,
                summary=summary,
                detail=detail,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        self._publish('approval.created', row)
        return row

    def resolve(
        self, approval_id: str, state: ApprovalState, via: AnsweredVia | None
    ) -> Approval | None:
        """Close an open card. None when it was no longer open: another answer won."""
        with self._lock, Session(self.engine) as s:
            result = s.exec(  # type: ignore[call-overload]
                update(Approval)
                .where(col(Approval.id) == approval_id, col(Approval.state) == ApprovalState.open)
                .values(state=state, answered_via=via, answered_at=utcnow())
            )
            s.commit()
            if result.rowcount != 1:
                return None
            row = s.get(Approval, approval_id)
            assert row is not None
        self._publish('approval.resolved', row)
        return row

    def expire_task(self, task_id: str) -> list[Approval]:
        """The task stopped or failed: its open cards can no longer be answered."""
        expired = []
        for row in self.open_on(task_id):
            if is_self_change(row):
                continue  # the PR outlives the task; selfmod closes the card with the PR
            done = self.resolve(row.id, ApprovalState.expired, None)
            if done is not None:
                expired.append(done)
        if expired:
            log.info('Task %s ended with %d open approval(s); expired them.', task_id, len(expired))
        return expired

    def resolve_from_transcript(self, task_id: str) -> tuple[list[Approval], int]:
        """Resolve open prompt cards whose tool call has a result in the transcript: `denied`
        when the result says so, `approved` otherwise. Returns them and how many stay open."""
        resolved: list[Approval] = []
        claimed: set[str] = set()
        still_open = 0
        with Session(self.engine) as s:
            events = list(
                s.exec(
                    select(TaskEvent)
                    .where(TaskEvent.task_id == task_id)
                    .where(
                        col(TaskEvent.type).in_(
                            [TaskEventType.tool_call, TaskEventType.tool_result]
                        )
                    )
                    .order_by(col(TaskEvent.seq))
                )
            )
        tools = {
            e.data_json.get('tool_use_id'): e.data_json.get('tool')
            for e in events
            if e.type == TaskEventType.tool_call
        }
        results = [e for e in events if e.type == TaskEventType.tool_result]
        for card in self.open_on(task_id):
            if card.request_id is None:
                continue  # not a prompt (a self-change card): only the app answers it
            found = next(
                (
                    r
                    for r in results
                    if r.data_json.get('tool_use_id') not in claimed
                    and tools.get(r.data_json.get('tool_use_id')) == card.tool
                    and _aware(r.at) >= _aware(card.created_at)
                ),
                None,
            )
            if found is None:
                still_open += 1
                continue
            claimed.add(found.data_json['tool_use_id'])
            denied = found.data_json.get('denied') is True
            state = ApprovalState.denied if denied else ApprovalState.approved
            row = self.resolve(card.id, state, AnsweredVia.terminal)
            if row is not None:
                log.info('Approval %s was answered in a terminal: %s.', card.id, state.value)
                resolved.append(row)
        return resolved, still_open

    def _publish(self, type_: str, row: Approval) -> None:
        events = self._events()
        if events is None:
            return
        with Session(self.engine) as s:
            user_id = owner_of_task(s, row.task_id)
            if user_id is not None:
                events.publish(s, user_id, type_, approval_json(row))


def is_self_change(row: Approval) -> bool:
    """A card `selfmod.py` opened. A worker's prompt can name any tool, even `self_change`, but
    it always has a `request_id`, so it is never one of these."""
    return row.tool == SELF_CHANGE_TOOL and row.request_id is None and row.artifact_id is not None
