"""Task threads (S-04.9, B-08, B-11): a side thread opened on a task's milestone or approval card
belongs to that task (`Conversation.task_id`).

- **Posting there steers the task.** `POST /api/conversations/{id}/messages` on a task's thread
  goes through the task registry's steer path, like `POST /api/tasks/{id}/messages`: a `note`
  task event and a `steer` frame in the task's Outbox. It never starts a brain turn: the channel
  gateway and the avatar both leave a task's thread alone. A `stopped` or `failed` task answers
  409 `task_finished` and nothing is stored; a `done` task takes it as a follow-up.
- **The task's later milestones and approval cards are mirrored into its thread.** They are still
  posted in the main conversation; each copy in a thread carries `client_id` `mirror_<original
  message id>`, so one original makes exactly one copy per thread. Copies are made once, when the
  original is posted; a WebSocket replay or reconnect only re-sends the stored events.
"""

import logging
from typing import Any

from fastapi import FastAPI
from sqlmodel import Session, select

from marcel_hub.auth import ApiError
from marcel_hub.conversations import post_message
from marcel_hub.models import (
    Conversation,
    ConversationKind,
    Message,
    MessageKind,
    MessageRole,
)
from marcel_hub.tasks.registry import TaskFinished, TaskNotFound, TaskRegistry
from marcel_hub.ws import EventLog

log = logging.getLogger(__name__)

# What a task posts in the main conversation that its thread shows too.
MIRRORED = frozenset({MessageKind.milestone.value, MessageKind.approval.value})
MIRROR_PREFIX = 'mirror_'


class TaskThreads:
    def __init__(self, engine: Any, events: EventLog, registry: TaskRegistry) -> None:
        self.engine = engine
        self.events = events
        self.registry = registry

    async def send(
        self,
        session: Session,
        thread: Conversation,
        text: str,
        *,
        client_id: str | None = None,
        project_id: str | None = None,
        artifact_id: str | None = None,
        mentions: list[dict[str, Any]] | None = None,
    ) -> Message:
        """Steer the thread's task with `text`, then store it as your message in the thread.

        The steer comes first, so a finished task stores nothing. A retry with the same
        `client_id` neither steers again nor stores a second message."""
        task_id = thread.task_id
        assert task_id is not None, 'only a task thread is sent here'
        try:
            await self.registry.steer(task_id, text, client_id=client_id)
        except TaskFinished as exc:
            raise ApiError(409, 'task_finished', str(exc)) from exc
        except TaskNotFound as exc:
            raise ApiError(
                404, 'task_not_found', 'The task this thread belongs to is not on this hub.'
            ) from exc
        return post_message(
            session,
            self.events,
            thread,
            role=MessageRole.user,
            kind=MessageKind.text,
            body={'text': text},
            task_id=task_id,
            project_id=project_id,
            client_id=client_id,
            artifact_id=artifact_id,
            mentions=mentions,
        )

    # Mirroring --------------------------------------------------------------------------------

    def observe(self, _user_id: str, type_: str, data: dict[str, Any]) -> None:
        """`EventLog` observer: copy a task's new milestone or approval card into its threads."""
        if type_ != 'message.created' or data.get('kind') not in MIRRORED:
            return
        if not data.get('task_id'):
            return
        try:
            self.mirror(data['id'])
        except Exception:  # the original is stored and shown; a missing copy must not undo it
            log.exception('Could not copy message %s into its task thread.', data.get('id'))

    def mirror(self, message_id: str) -> list[Message]:
        """Copy a message from the main conversation into every thread of its task. Returns the
        copies (an existing copy is returned, never made twice)."""
        with Session(self.engine) as s:
            original = s.get(Message, message_id)
            if original is None or original.task_id is None:
                return []
            source = s.get(Conversation, original.conversation_id)
            # Only originals from the main conversation: a copy in a thread is never copied again.
            if source is None or source.kind != ConversationKind.main:
                return []
            threads = s.exec(
                select(Conversation)
                .where(Conversation.task_id == original.task_id)
                .where(Conversation.kind == ConversationKind.side)
            ).all()
            made = [
                post_message(
                    s,
                    self.events,
                    thread,
                    role=original.role,
                    kind=original.kind,
                    body=dict(original.body_json),
                    task_id=original.task_id,
                    project_id=original.project_id,
                    artifact_id=original.artifact_id,
                    client_id=MIRROR_PREFIX + original.id,
                )
                for thread in threads
            ]
            for copy in made:  # a later copy's commit expired the earlier ones
                s.refresh(copy)
            return made


def install(app: FastAPI) -> TaskThreads:
    """Put the task threads on `app.state.task_threads` and let them watch the app event log.
    Call after `tasks.install`."""
    threads = TaskThreads(app.state.engine, app.state.events, app.state.tasks)
    app.state.events.observers.append(threads.observe)
    app.state.task_threads = threads
    return threads
