"""The task registry: creates tasks, moves them through the state machine, records their events.

Every state change goes through `TaskRegistry.transition`, which refuses anything not in
`contracts/task-states.md`, writes a `state` task event and publishes `task.updated`.

The queue is the set of `queued` tasks, oldest first: `queued_reason: cap` waits for a worker
slot, `usage` for the usage guard (S-10.1) to let new work start. It lives in the database, so it
survives a restart. A task's opening prompt is its first `note` event (`author: marcel`), which is
how a queued task can still be started later. The queue is drained whenever a task leaves a slot
(it finishes, fails or is stopped), and on `drain()`. While `hold_new_work()` gives a reason,
nothing new starts: a spawn is queued with reason `usage` and a drain starts nothing.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from marcel_hub.models import (
    Agent,
    Approval,
    ApprovalState,
    Artifact,
    QueuedReason,
    Task,
    TaskEvent,
    TaskEventType,
    TaskLocation,
    TaskOrigin,
    TaskState,
    new_id,
    utcnow,
)
from marcel_hub.tasks.ports import (
    CapReached,
    NullPublisher,
    Outbox,
    Publisher,
    Runner,
    RunnerUnavailable,
    StartFailed,
)
from marcel_hub.tasks.states import FINISHED, check

log = logging.getLogger(__name__)

# Transcript event types the app does not show (transcript.schema.json): not stored.
DROPPED_TRANSCRIPT = frozenset({'thinking', 'turn_end'})

# Kept on a stored `note` for the hub's own use, never sent to the app: `NoteEventData` allows
# only `author`, `text` and `handback`. `prompt` marks a task's opening prompt; `client_id` is
# the app's retry key for a steer.
HUB_ONLY_NOTE_KEYS = frozenset({'prompt', 'client_id'})


# The reason on a done task's move back to `working` when a follow-up is sent.
FOLLOW_UP_SENT = 'A follow-up was sent'


class TaskNotFound(Exception):
    def __init__(self, task_id: str) -> None:
        super().__init__(f'There is no task {task_id}.')


class TaskFinished(Exception):
    """A stopped or failed task takes no more messages (409 `task_finished`)."""


class NotArchivable(Exception):
    """Only done, failed or stopped tasks can be archived (409 `task_not_finished`)."""


def iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def _parse_at(value: str | None) -> datetime:
    if not value:
        return utcnow()
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


class TaskRegistry:
    def __init__(
        self,
        engine: Engine,
        runner: Runner,
        outbox: Outbox,
        publisher: Publisher | None = None,
    ) -> None:
        self.engine = engine
        self.runner = runner
        self.outbox = outbox
        self.publisher = publisher or NullPublisher()
        # What a worker reads before its prompt (its project's instructions and notes); set by
        # `projects.install`. The stored opening note stays the prompt alone.
        self.briefing: Callable[[Task], str] = lambda _task: ''
        # Why new work may not start now, or None when it may; set by `usage.install` (S-10.1).
        self.hold_new_work: Callable[[], str | None] = lambda: None
        self._lock = asyncio.Lock()

    # Reading ---------------------------------------------------------------------------------

    def get(self, task_id: str) -> Task:
        with Session(self.engine) as s:
            task = s.get(Task, task_id)
            if task is None:
                raise TaskNotFound(task_id)
            return task

    def owner_of(self, task_id: str) -> str | None:
        """The person whose agent the task belongs to, or None when there is no such task."""
        with Session(self.engine) as s:
            task = s.get(Task, task_id)
            agent = s.get(Agent, task.agent_id) if task is not None else None
            return agent.owner_user_id if agent is not None else None

    def agent_owner(self, agent_id: str) -> str | None:
        """The person the agent belongs to, or None when there is no such agent."""
        with Session(self.engine) as s:
            agent = s.get(Agent, agent_id)
            return agent.owner_user_id if agent is not None else None

    def list_tasks(
        self,
        agent_id: str,
        states: list[TaskState] | None = None,
        archived: bool = False,
        offset: int = 0,
        limit: int = 50,
        project_id: str | None = None,
    ) -> tuple[list[Task], int | None]:
        """One page, most recently changed first, and the offset of the next page (or None)."""
        query = select(Task).where(Task.agent_id == agent_id)
        if project_id is not None:
            query = query.where(Task.project_id == project_id)
        if states:
            query = query.where(col(Task.state).in_(states))
        archived_at = col(Task.archived_at)
        query = query.where(archived_at.is_not(None) if archived else archived_at.is_(None))
        query = query.order_by(col(Task.updated_at).desc(), col(Task.id).desc())
        with Session(self.engine) as s:
            rows = list(s.exec(query.offset(offset).limit(limit + 1)))
        more = len(rows) > limit
        return rows[:limit], (offset + limit if more else None)

    def queue(self) -> list[Task]:
        """Tasks waiting to start (for a worker slot or for the usage guard), oldest first."""
        with Session(self.engine) as s:
            return list(
                s.exec(
                    select(Task)
                    .where(Task.state == TaskState.queued)
                    .order_by(col(Task.created_at), col(Task.id))
                )
            )

    def events(self, task_id: str, after: int = 0, limit: int = 50) -> tuple[list[TaskEvent], bool]:
        self.get(task_id)
        with Session(self.engine) as s:
            rows = list(
                s.exec(
                    select(TaskEvent)
                    .where(TaskEvent.task_id == task_id)
                    .where(TaskEvent.seq > after)
                    .order_by(col(TaskEvent.seq))
                    .limit(limit + 1)
                )
            )
        return rows[:limit], len(rows) > limit

    def to_api(self, task: Task) -> dict[str, Any]:
        """The contract's `Task`: unset fields left out, plus open approvals and artifact count."""
        body: dict[str, Any] = {}
        for name, value in task.model_dump().items():
            if value is None or name == 'worktree':
                continue
            body[name] = iso(value) if isinstance(value, datetime) else value
        with Session(self.engine) as s:
            body['approval_ids'] = list(
                s.exec(
                    select(Approval.id)
                    .where(Approval.task_id == task.id)
                    .where(Approval.state == ApprovalState.open)
                )
            )
            body['artifact_count'] = s.exec(
                select(func.count()).select_from(Artifact).where(Artifact.task_id == task.id)
            ).one()
        return body

    @staticmethod
    def event_to_api(event: TaskEvent) -> dict[str, Any]:
        data = {k: v for k, v in event.data_json.items() if k not in HUB_ONLY_NOTE_KEYS}
        return {
            'task_id': event.task_id,
            'seq': event.seq,
            'type': event.type.value,
            'at': iso(event.at),
            'data': data,
        }

    # Creating --------------------------------------------------------------------------------

    async def spawn(
        self,
        agent_id: str,
        title: str,
        prompt: str,
        location: TaskLocation = TaskLocation.nuc,
        model: str = 'sonnet',
        origin: TaskOrigin = TaskOrigin.marcel,
        **fields: Any,
    ) -> Task:
        """Start a task, or queue it when the worker cap is full (or others are already waiting),
        or while the usage guard holds new work back.

        Without `project_id` in `fields` the task is filed in the agent's inbox."""
        task = Task(
            agent_id=agent_id,
            title=title,
            location=location,
            model=model,
            origin=origin,
            state=TaskState.starting,
            **fields,
        )
        async with self._lock:
            # The usage guard: the plan is shared, so NUC and cloud tasks both wait.
            held = self.hold_new_work()
            if held:
                return self.create(task, prompt, TaskState.queued, held, QueuedReason.usage)
            # The worker cap is the NUC's; cloud tasks never wait behind it.
            if location == TaskLocation.nuc and self.queue():
                self.create(task, prompt, TaskState.queued, 'Others are waiting first')
            else:
                return await self._start_new(task, prompt)
        await self.drain()  # a slot may have freed while the runner was out of reach
        return self.get(task.id)

    async def _start_new(self, task: Task, prompt: str) -> Task:
        try:
            started = await self.runner.start(task, self._briefed(task, prompt))
        except CapReached:
            return self.create(task, prompt, TaskState.queued, 'The worker cap is reached')
        except (StartFailed, RunnerUnavailable) as exc:
            self.create(task, prompt, TaskState.starting, 'Spawn accepted')
            return self._transition(task.id, TaskState.failed, f'Could not start: {exc}')
        task.session_id = started.session_id
        task.branch = started.branch or task.branch
        task.cloud_url = started.cloud_url or task.cloud_url
        return self.create(task, prompt, TaskState.starting, 'Spawn accepted')

    def create(
        self,
        task: Task,
        prompt: str,
        state: TaskState,
        reason: str,
        queued_reason: QueuedReason = QueuedReason.cap,
    ) -> Task:
        """Store a new task in `starting` or `queued`, with its opening prompt as a note."""
        check(task.id, None, state, reason)
        task.state = state
        task.queued_reason = queued_reason if state == TaskState.queued else None
        with Session(self.engine) as s:
            s.add(task)
            s.commit()
            s.refresh(task)
        self._publish_task(task.id, 'task.created')
        self._append(task.id, TaskEventType.state, {'to': state.value, 'reason': reason})
        self._append(
            task.id, TaskEventType.note, {'author': 'marcel', 'text': prompt, 'prompt': True}
        )
        return self.get(task.id)

    # Moving ----------------------------------------------------------------------------------

    async def transition(self, task_id: str, to: TaskState, reason: str, **fields: Any) -> Task:
        """Move a task. Raises `IllegalTransition` for anything not in task-states.md."""
        before = self.get(task_id).state
        task = self._transition(task_id, to, reason, **fields)
        if to in FINISHED and before != TaskState.queued:
            await self.drain()
        return task

    async def report_state(self, task_id: str, to: TaskState, reason: str, **fields: Any) -> Task:
        """A runner or channel state report. Repeats of the current state change nothing."""
        if self.get(task_id).state == to:
            return self.get(task_id)
        return await self.transition(task_id, to, reason, **fields)

    def _transition(self, task_id: str, to: TaskState, reason: str, **fields: Any) -> Task:
        with Session(self.engine) as s:
            task = s.get(Task, task_id)
            if task is None:
                raise TaskNotFound(task_id)
            before = task.state
            check(task_id, before, to, reason)
            task.state = to
            if to != TaskState.queued:
                task.queued_reason = None
            if to != TaskState.needs_you:
                task.waiting_for = None
            for name, value in fields.items():
                setattr(task, name, value)
            task.updated_at = utcnow()
            s.add(task)
            s.commit()
        log.info('Task %s: %s → %s (%s)', task_id, before.value, to.value, reason)
        self._append(
            task_id, TaskEventType.state, {'from': before.value, 'to': to.value, 'reason': reason}
        )
        self._publish_task(task_id)
        return self.get(task_id)

    async def drain(self) -> None:
        """Start queued tasks, oldest first, until the runner says the cap is full again.

        Nothing starts while the usage guard holds new work. Once the NUC's cap is full, cloud
        tasks further down the queue still start (the cap is the NUC's); a NUC task the guard had
        held now waits for a slot, so its reason becomes `cap`."""
        async with self._lock:
            if self.hold_new_work():
                return
            full = False
            for task in self.queue():
                nuc = task.location == TaskLocation.nuc
                if full and nuc:
                    self._wait_for_slot(task)
                    continue
                why = (
                    'Plan usage allows new work again'
                    if task.queued_reason == QueuedReason.usage
                    else 'A worker slot freed'
                )
                try:
                    started = await self.runner.start(
                        task, self._briefed(task, self._prompt(task.id))
                    )
                except CapReached:
                    full = True
                    self._wait_for_slot(task)
                    continue
                except RunnerUnavailable:
                    return
                except StartFailed as exc:
                    self._transition(task.id, TaskState.starting, why)
                    self._transition(task.id, TaskState.failed, f'Could not start: {exc}')
                    continue
                self._transition(
                    task.id,
                    TaskState.starting,
                    why,
                    session_id=started.session_id,
                    branch=started.branch or task.branch,
                    cloud_url=started.cloud_url or task.cloud_url,
                )

    def _wait_for_slot(self, task: Task) -> None:
        """A task the usage guard held now waits for a worker slot instead: say so."""
        if task.queued_reason != QueuedReason.usage:
            return
        with Session(self.engine) as s:
            row = s.get(Task, task.id)
            assert row is not None
            row.queued_reason = QueuedReason.cap
            row.updated_at = utcnow()
            s.add(row)
            s.commit()
        self._publish_task(task.id)

    def _briefed(self, task: Task, prompt: str) -> str:
        """The prompt with the project briefing in front. The briefing is extra context: if it
        cannot be built, the task starts without it and the log says why."""
        try:
            return self.briefing(task) + prompt
        except Exception as exc:  # any failure here must not stop the work itself
            log.warning(
                'Task %s starts without its project briefing, which could not be built: %s',
                task.id,
                exc,
            )
            return prompt

    def _prompt(self, task_id: str) -> str:
        with Session(self.engine) as s:
            event = s.exec(
                select(TaskEvent)
                .where(TaskEvent.task_id == task_id)
                .where(TaskEvent.type == TaskEventType.note)
                .where(func.json_extract(TaskEvent.data_json, '$.prompt') == 1)
                .order_by(col(TaskEvent.seq))
            ).first()
        return event.data_json['text'] if event else ''

    def _note_with_client_id(self, task_id: str, client_id: str) -> TaskEvent | None:
        with Session(self.engine) as s:
            return s.exec(
                select(TaskEvent)
                .where(TaskEvent.task_id == task_id)
                .where(TaskEvent.type == TaskEventType.note)
                .where(func.json_extract(TaskEvent.data_json, '$.client_id') == client_id)
            ).first()

    # Events ----------------------------------------------------------------------------------

    def append_transcript(self, task_id: str, event: dict[str, Any]) -> TaskEvent | None:
        """Store one normalised transcript event (transcript.schema.json) as a task event."""
        if event['type'] in DROPPED_TRANSCRIPT:
            return None
        self.get(task_id)
        at = _parse_at(event.get('at'))
        row = self._append(task_id, TaskEventType(event['type']), event['data'], at)
        with Session(self.engine) as s:
            task = s.get(Task, task_id)
            assert task is not None
            task.last_activity_at = at
            s.add(task)
            s.commit()
        return row

    def append_event(self, task_id: str, type: TaskEventType, data: dict[str, Any]) -> TaskEvent:
        """A hub-written event (`progress`, `artifact`, `note`, `permission`)."""
        self.get(task_id)
        return self._append(task_id, type, data)

    def _append(
        self,
        task_id: str,
        type: TaskEventType,
        data: dict[str, Any],
        at: datetime | None = None,
    ) -> TaskEvent:
        with Session(self.engine) as s:
            last = s.exec(select(func.max(TaskEvent.seq)).where(TaskEvent.task_id == task_id)).one()
            row = TaskEvent(
                task_id=task_id, seq=(last or 0) + 1, type=type, data_json=data, at=at or utcnow()
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        self.publisher.publish('task.event', self.event_to_api(row))
        return row

    def _publish_task(self, task_id: str, kind: str = 'task.updated') -> None:
        self.publisher.publish(kind, self.to_api(self.get(task_id)))

    # What the app can do ---------------------------------------------------------------------

    async def steer(
        self, task_id: str, text: str, author: str = 'user', client_id: str | None = None
    ) -> Task:
        """Record a note and hand it to the outbox. A repeated `client_id` changes nothing."""
        task = self.get(task_id)
        if client_id and self._note_with_client_id(task_id, client_id):
            return task
        if task.state in (TaskState.stopped, TaskState.failed):
            raise TaskFinished(
                f'This task has {task.state.value}, so it cannot take messages. '
                'Ask Marcel to retry it instead.'
            )
        note: dict[str, Any] = {'author': author, 'text': text}
        if client_id:
            note['client_id'] = client_id
        self._append(task_id, TaskEventType.note, note)
        if task.state == TaskState.done:
            # Live before the hand-over: a cloud worker's first report on the follow-up can
            # arrive while the send is still in flight, and must find its task live (S-07.1).
            await self.transition(task_id, TaskState.working, FOLLOW_UP_SENT)
        await self.outbox.to_task(
            task_id,
            {
                'type': 'steer',
                'task_id': task_id,
                'note_id': new_id('note'),
                'author': author,
                'text': text,
            },
        )
        if task.state == TaskState.needs_you:
            return await self.transition(task_id, TaskState.working, 'A steer message arrived')
        return self.get(task_id)

    async def stop(self, task_id: str, by: str = 'you') -> Task:
        task = self.get(task_id)
        if task.state in FINISHED:
            return task
        if task.state == TaskState.queued:
            return await self.transition(
                task_id, TaskState.stopped, f'Stopped by {by} before it ran'
            )
        if task.session_id:
            await self.runner.stop(task.session_id)
        return await self.transition(task_id, TaskState.stopped, f'Stopped by {by}')

    def archive(self, task_id: str) -> Task:
        with Session(self.engine) as s:
            task = s.get(Task, task_id)
            if task is None:
                raise TaskNotFound(task_id)
            if task.state not in FINISHED:
                raise NotArchivable(
                    f'This task is still {task.state.value}. '
                    'Only done, failed or stopped tasks can be archived.'
                )
            if task.archived_at is None:
                task.archived_at = utcnow()
                task.updated_at = task.archived_at
                s.add(task)
                s.commit()
        self._publish_task(task_id)
        return self.get(task_id)

    async def handback(self, task_id: str, note: str | None = None) -> Task:
        task = self.get(task_id)
        text = note or 'Handed back to Marcel.'
        self._append(
            task_id, TaskEventType.note, {'author': 'user', 'text': text, 'handback': True}
        )
        await self.outbox.to_brain(
            task.agent_id,
            {
                'type': 'system_event',
                'kind': 'handback',
                'text': f'The owner handed the task "{task.title}" back to you.',
                'task_id': task_id,
                'data': {'title': task.title, 'state': task.state.value, 'note': note or ''},
            },
        )
        return self.get(task_id)
