"""Main and side conversations and their messages (B-01, B-02, B-08).

Each agent has one main conversation. "Reply in thread" opens a side conversation on a message;
"send to main" brings a side-thread result back as a quote. Every new message and side thread is
an app event (`message.created`, `conversation.created`) for every device of the agent's owner.

Later stories (brain replies, milestones, approvals) store messages with `post_message`.
"""

from typing import Any, Self

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func
from sqlmodel import Session, col, select

from marcel_hub.auth import ApiError, require_device
from marcel_hub.auth.deps import DbSession
from marcel_hub.mentions import Mention, resolve
from marcel_hub.models import (
    Agent,
    Artifact,
    Conversation,
    ConversationKind,
    Message,
    MessageKind,
    MessageRole,
    Project,
    Task,
)
from marcel_hub.ws import EventLog

router = APIRouter(prefix='/api', tags=['conversations'], dependencies=[Depends(require_device)])

CURSOR_PREFIX = 'c_'
TITLE_LENGTH = 80
# Message kinds about a task: a thread opened on one belongs to that task (Conversation.task_id).
TASK_KINDS = {MessageKind.milestone, MessageKind.approval, MessageKind.artifact}


class NewMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=20000)
    client_id: str | None = Field(default=None, max_length=200)
    # Sent from a project room (B-32): stored on the message, which stays in this conversation.
    project_id: str | None = None
    # "Ask about this" on an item (B-21): stored on the message and passed to the brain.
    artifact_id: str | None = None
    # B-42: the quests, projects and items picked in the composer; each must be the agent's own.
    mentions: list[Mention] = Field(default_factory=list, max_length=10)


class SendToMain(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message_id: str | None = None
    text: str | None = Field(default=None, min_length=1, max_length=20000)

    @model_validator(mode='after')
    def _one_of(self) -> Self:
        if (self.message_id is None) == (self.text is None):
            raise ValueError('send either `message_id` or `text`, not both')
        return self


# ---- shapes -----------------------------------------------------------------------------------


def message_json(msg: Message, thread: dict[str, Any] | None = None) -> dict[str, Any]:
    """The contract's `Message`. `text`, `milestone`, `approval_id` and `quoted_message_id`
    live in `body_json`."""
    body: dict[str, Any] = {
        'id': msg.id,
        'conversation_id': msg.conversation_id,
        'role': msg.role.value,
        'kind': msg.kind.value,
    }
    for key in ('text', 'milestone', 'approval_id', 'quoted_message_id'):
        if key in msg.body_json:
            body[key] = msg.body_json[key]
    if msg.mentions_json:
        body['mentions'] = msg.mentions_json
    if msg.task_id is not None:
        body['task_id'] = msg.task_id
    if msg.project_id is not None:
        body['project_id'] = msg.project_id
    if msg.artifact_id is not None:
        body['artifact_id'] = msg.artifact_id
    if msg.client_id is not None:
        body['client_id'] = msg.client_id
    if msg.reply_to_message_id is not None:
        body['reply_to_message_id'] = msg.reply_to_message_id
    if msg.agent_id is not None:
        body['agent_id'] = msg.agent_id
    if thread is not None:
        body['thread'] = thread
    body['created_at'] = msg.created_at.isoformat()
    return body


def conversation_json(conv: Conversation, stats: tuple[int, Any] | None = None) -> dict[str, Any]:
    """The contract's `Conversation`. `stats` is (message count, newest message time)."""
    body: dict[str, Any] = {'id': conv.id, 'agent_id': conv.agent_id, 'kind': conv.kind.value}
    for key in ('parent_message_id', 'task_id', 'title'):
        if getattr(conv, key) is not None:
            body[key] = getattr(conv, key)
    if conv.kind == ConversationKind.side:
        body['reply_count'] = stats[0] if stats else 0
    if stats and stats[1] is not None:
        body['last_message_at'] = stats[1].isoformat()
    body['created_at'] = conv.created_at.isoformat()
    return body


def conversation_stats(session: Session, ids: list[str]) -> dict[str, tuple[int, Any]]:
    rows = session.exec(
        select(Message.conversation_id, func.count(), func.max(Message.created_at))
        .where(col(Message.conversation_id).in_(ids))
        .group_by(col(Message.conversation_id))
    ).all()
    return {cid: (count, newest) for cid, count, newest in rows}


def threads_of(session: Session, message_ids: list[str]) -> dict[str, dict[str, Any]]:
    """`Message.thread` for each message that has a side thread."""
    sides = session.exec(
        select(Conversation).where(col(Conversation.parent_message_id).in_(message_ids))
    ).all()
    stats = conversation_stats(session, [s.id for s in sides])
    return {
        s.parent_message_id: {'conversation_id': s.id, 'reply_count': stats.get(s.id, (0,))[0]}
        for s in sides
        if s.parent_message_id is not None
    }


# ---- store ------------------------------------------------------------------------------------


def ensure_main_conversation(session: Session, agent: Agent) -> Conversation:
    """The agent's main conversation, made and linked on first use."""
    if agent.main_conversation_id is not None:
        main = session.get(Conversation, agent.main_conversation_id)
        if main is not None:
            return main
    main = Conversation(agent_id=agent.id, kind=ConversationKind.main)
    session.add(main)
    session.flush()
    agent.main_conversation_id = main.id
    session.add(agent)
    session.commit()
    session.refresh(main)
    return main


def owner_of(session: Session, conv: Conversation) -> str:
    agent = session.get(Agent, conv.agent_id)
    assert agent is not None  # a foreign key
    return agent.owner_user_id


def answerable(session: Session, conversation_id: str, message_id: str) -> str | None:
    """`message_id` when it is a user message in this conversation, the only kind a reply may
    quote; None otherwise."""
    msg = session.get(Message, message_id)
    if msg is None or msg.conversation_id != conversation_id or msg.role != MessageRole.user:
        return None
    return msg.id


def post_message(
    session: Session,
    events: EventLog,
    conv: Conversation,
    *,
    role: MessageRole,
    kind: MessageKind,
    body: dict[str, Any],
    task_id: str | None = None,
    project_id: str | None = None,
    client_id: str | None = None,
    artifact_id: str | None = None,
    reply_to_message_id: str | None = None,
    mentions: list[dict[str, Any]] | None = None,
) -> Message:
    """Store a message and send `message.created` to every device of the owner.

    The same `client_id` in the same conversation returns the stored message instead of a
    duplicate, and sends nothing. The check and the write share the event lock, so two retries
    racing each other still store one message.

    An agent message posted for a task (its milestones, approval cards and items) answers the
    message that started the task (B-41): `reply_to_message_id` is filled from
    `Task.parent_message_id` when the caller gives none, if that is a user message in the same
    conversation. System notes and the owner's own messages are left as they are.
    """
    if reply_to_message_id is None and task_id is not None and role == MessageRole.agent:
        task = session.get(Task, task_id)
        if task is not None and task.parent_message_id is not None:
            reply_to_message_id = answerable(session, conv.id, task.parent_message_id)
    with events.lock:
        if client_id is not None:
            existing = session.exec(
                select(Message).where(
                    Message.conversation_id == conv.id, Message.client_id == client_id
                )
            ).first()
            if existing is not None:
                return existing
        msg = Message(
            conversation_id=conv.id,
            role=role,
            kind=kind,
            body_json=body,
            task_id=task_id,
            project_id=project_id,
            client_id=client_id,
            artifact_id=artifact_id,
            reply_to_message_id=reply_to_message_id,
            mentions_json=mentions or [],
            # Who wrote it, for the app to credit (app-api 0.7.2): brain replies, side forks,
            # milestones, approval cards, items and digests are all posted by an agent here.
            agent_id=conv.agent_id if role == MessageRole.agent else None,
        )
        session.add(msg)
        session.flush()
        events.publish(session, owner_of(session, conv), 'message.created', message_json(msg))
        session.refresh(msg)
        return msg


# ---- routes -----------------------------------------------------------------------------------


def not_found(what: str) -> ApiError:
    return ApiError(404, f'{what}_not_found', f'That {what} is not on this hub.')


def own_conversation(session: Session, conversation_id: str) -> Conversation:
    """The conversation, of any buddy of the one person; else 404 `conversation_not_found`."""
    conv = session.get(Conversation, conversation_id)
    if conv is None:
        raise not_found('conversation')
    return conv


def own_message(session: Session, message_id: str) -> tuple[Message, Conversation]:
    """The message and its conversation; else 404 `message_not_found`."""
    msg = session.get(Message, message_id)
    conv = session.get(Conversation, msg.conversation_id) if msg else None
    if msg is None or conv is None:
        raise not_found('message')
    return msg, conv


def agent_of_artifact(session: Session, art: Artifact) -> str | None:
    """The agent an item belongs to: its project's, else its task's; None for neither."""
    if art.project_id is not None:
        project = session.get(Project, art.project_id)
        if project is not None:
            return project.agent_id
    if art.task_id is not None:
        task = session.get(Task, art.task_id)
        if task is not None:
            return task.agent_id
    return None


def own_artifact(session: Session, artifact_id: str, agent_id: str) -> Artifact:
    """An item of this agent's, or 404 `artifact_not_found` (also for another agent's item)."""
    art = session.get(Artifact, artifact_id)
    if art is None or agent_of_artifact(session, art) != agent_id:
        raise ApiError(404, 'artifact_not_found', 'That item is not on this hub. It may be gone.')
    return art


def events_of(request: Request) -> EventLog:
    return request.app.state.events


@router.get('/conversations')
def list_conversations(agent_id: str, session: DbSession) -> list[dict]:
    agent = session.get(Agent, agent_id)
    if agent is None:
        return []
    convs = session.exec(select(Conversation).where(Conversation.agent_id == agent.id)).all()
    main = [c for c in convs if c.kind == ConversationKind.main]
    sides = sorted(
        (c for c in convs if c.kind == ConversationKind.side),
        key=lambda c: (c.created_at, c.id),
        reverse=True,
    )
    stats = conversation_stats(session, [c.id for c in convs])
    return [conversation_json(c, stats.get(c.id)) for c in [*main, *sides]]


@router.get('/conversations/{conversation_id}/messages')
def list_messages(
    conversation_id: str,
    request: Request,
    session: DbSession,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    conv = own_conversation(session, conversation_id)
    query = select(Message).where(Message.conversation_id == conv.id)
    if cursor is not None:
        after = session.get(Message, cursor.removeprefix(CURSOR_PREFIX))
        if (
            not cursor.startswith(CURSOR_PREFIX)
            or after is None
            or after.conversation_id != conv.id
        ):
            raise ApiError(
                400, 'bad_cursor', 'That page marker is not from this conversation. Start again.'
            )
        # Newest first, so the next page is everything older than the last message shown.
        query = query.where(
            (col(Message.created_at) < after.created_at)
            | ((col(Message.created_at) == after.created_at) & (col(Message.id) < after.id))
        )
    rows = session.exec(
        query.order_by(col(Message.created_at).desc(), col(Message.id).desc()).limit(limit + 1)
    ).all()
    page = list(rows[:limit])
    threads = threads_of(session, [m.id for m in page])
    body: dict[str, Any] = {'items': [message_json(m, threads.get(m.id)) for m in page]}
    if len(rows) > limit:
        body['next_cursor'] = CURSOR_PREFIX + page[-1].id
    return body


@router.post('/conversations/{conversation_id}/messages', status_code=201)
async def send_message(
    conversation_id: str, body: NewMessage, request: Request, session: DbSession
) -> dict[str, Any]:
    """Store and return at once: never waits for Marcel (B-02).

    A `project_id` (sent from a project room, B-32) and an `artifact_id` ("Ask about this", B-21)
    are stored on the message; the channel gateway passes both to the brain with the text. An
    item that is not this agent's answers 404 `artifact_not_found`. `mentions` (B-42) must be
    the agent's own, or nothing is stored (404 `mention_not_found`); a `#project` mention files
    the message in that project when it has no `project_id`. In a task's own thread the
    message steers that task instead (`task_threads`, S-04.9)."""
    conv = own_conversation(session, conversation_id)
    mentions = resolve(session, body.mentions, conv.agent_id)
    if body.project_id is None:
        body.project_id = next((m['id'] for m in mentions if m['kind'] == 'project'), None)
    if body.project_id is not None:
        project = session.get(Project, body.project_id)
        if project is None or project.agent_id != conv.agent_id:
            raise ApiError(400, 'bad_request', 'project_id: that project is not this agent’s.')
    if body.artifact_id is not None:
        own_artifact(session, body.artifact_id, conv.agent_id)
    if conv.task_id is not None:
        msg = await request.app.state.task_threads.send(
            session,
            conv,
            body.text,
            client_id=body.client_id,
            project_id=body.project_id,
            artifact_id=body.artifact_id,
            mentions=mentions,
        )
        return message_json(msg, threads_of(session, [msg.id]).get(msg.id))
    msg = post_message(
        session,
        events_of(request),
        conv,
        role=MessageRole.user,
        kind=MessageKind.text,
        body={'text': body.text},
        project_id=body.project_id,
        client_id=body.client_id,
        artifact_id=body.artifact_id,
        mentions=mentions,
    )
    return message_json(msg, threads_of(session, [msg.id]).get(msg.id))


def thread_title(msg: Message) -> str | None:
    if 'text' in msg.body_json:
        first_line = str(msg.body_json['text']).strip().splitlines()[0:1]
        return first_line[0][:TITLE_LENGTH] if first_line else None
    milestone = msg.body_json.get('milestone')
    return milestone.get('task_title') if isinstance(milestone, dict) else None


@router.get('/messages/{message_id}')
def get_message(message_id: str, request: Request, session: DbSession) -> dict[str, Any]:
    """One message by id, for a `marcel://message/<id>` deep link; 404 `message_not_found` for
    one that does not exist."""
    msg, _ = own_message(session, message_id)
    return message_json(msg, threads_of(session, [msg.id]).get(msg.id))


@router.post('/messages/{message_id}/thread')
def open_thread(message_id: str, request: Request, session: DbSession) -> JSONResponse:
    """Open the side thread on a message, or return the one it already has (B-08)."""
    msg, parent = own_message(session, message_id)
    events = events_of(request)
    with events.lock:
        existing = session.exec(
            select(Conversation).where(Conversation.parent_message_id == msg.id)
        ).first()
        if existing is not None:
            stats = conversation_stats(session, [existing.id]).get(existing.id)
            return JSONResponse(conversation_json(existing, stats), status_code=200)
        side = Conversation(
            agent_id=parent.agent_id,
            kind=ConversationKind.side,
            parent_message_id=msg.id,
            task_id=msg.task_id if msg.kind in TASK_KINDS else None,
            title=thread_title(msg),
        )
        session.add(side)
        session.flush()
        body = conversation_json(side)
        events.publish(session, owner_of(session, parent), 'conversation.created', body)
    return JSONResponse(body, status_code=201)


@router.post('/conversations/{conversation_id}/send-to-main', status_code=201)
def send_to_main(
    conversation_id: str, body: SendToMain, request: Request, session: DbSession
) -> dict[str, Any]:
    side = own_conversation(session, conversation_id)
    if side.kind == ConversationKind.main:
        raise ApiError(400, 'already_main', 'This is already the main conversation.')
    agent = session.get(Agent, side.agent_id)
    main = session.get(Conversation, agent.main_conversation_id or '') if agent else None
    if main is None:
        raise not_found('conversation')

    if body.text is not None:
        kind, content = MessageKind.text, {'text': body.text}
    else:
        quoted = session.get(Message, body.message_id)
        if quoted is None or quoted.conversation_id != side.id:
            raise not_found('message')
        kind, content = (
            MessageKind.quote,
            {'text': quoted_text(quoted), 'quoted_message_id': quoted.id},
        )
    msg = post_message(
        session, events_of(request), main, role=MessageRole.user, kind=kind, body=content
    )
    return message_json(msg)


def quoted_text(msg: Message) -> str:
    """What a quote shows: the message's text, or a milestone's summary or task title."""
    if 'text' in msg.body_json:
        return str(msg.body_json['text'])
    milestone = msg.body_json.get('milestone') or {}
    return str(
        milestone.get('summary') or milestone.get('task_title') or 'A message from the thread'
    )
