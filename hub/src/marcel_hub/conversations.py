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

from marcel_hub.auth import ApiError, current_device, require_device
from marcel_hub.auth.deps import DbSession
from marcel_hub.models import (
    Agent,
    Conversation,
    ConversationKind,
    Device,
    Message,
    MessageKind,
    MessageRole,
    Project,
)
from marcel_hub.ws import EventLog

router = APIRouter(prefix='/api', tags=['conversations'], dependencies=[Depends(require_device)])

CURSOR_PREFIX = 'c_'
TITLE_LENGTH = 80
# Message kinds about a task: a thread opened on one belongs to that task (Conversation.task_id).
TASK_KINDS = {MessageKind.milestone, MessageKind.approval}


class NewMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=20000)
    client_id: str | None = Field(default=None, max_length=200)
    # Sent from a project room (B-32): stored on the message, which stays in this conversation.
    project_id: str | None = None


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
    if msg.task_id is not None:
        body['task_id'] = msg.task_id
    if msg.project_id is not None:
        body['project_id'] = msg.project_id
    if msg.client_id is not None:
        body['client_id'] = msg.client_id
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
) -> Message:
    """Store a message and send `message.created` to every device of the owner.

    The same `client_id` in the same conversation returns the stored message instead of a
    duplicate, and sends nothing. The check and the write share the event lock, so two retries
    racing each other still store one message.
    """
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
        )
        session.add(msg)
        session.flush()
        events.publish(session, owner_of(session, conv), 'message.created', message_json(msg))
        session.refresh(msg)
        return msg


# ---- routes -----------------------------------------------------------------------------------


def not_found(what: str) -> ApiError:
    return ApiError(404, f'{what}_not_found', f'That {what} is not on this hub.')


def own_conversation(session: Session, conversation_id: str, me: Device) -> Conversation:
    conv = session.get(Conversation, conversation_id)
    if conv is None or owner_of(session, conv) != me.user_id:
        raise not_found('conversation')
    return conv


def own_message(session: Session, message_id: str, me: Device) -> tuple[Message, Conversation]:
    msg = session.get(Message, message_id)
    conv = session.get(Conversation, msg.conversation_id) if msg else None
    if msg is None or conv is None or owner_of(session, conv) != me.user_id:
        raise not_found('message')
    return msg, conv


def events_of(request: Request) -> EventLog:
    return request.app.state.events


@router.get('/conversations')
def list_conversations(agent_id: str, request: Request, session: DbSession) -> list[dict]:
    me = current_device(request)
    agent = session.get(Agent, agent_id)
    if agent is None or agent.owner_user_id != me.user_id:
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
    conv = own_conversation(session, conversation_id, current_device(request))
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
def send_message(
    conversation_id: str, body: NewMessage, request: Request, session: DbSession
) -> dict[str, Any]:
    """Store and return at once: never waits for Marcel (B-02).

    A `project_id` (sent from a project room, B-32) is stored on the message; whatever hands user
    messages to the brain passes `message.project_id` along with the text."""
    conv = own_conversation(session, conversation_id, current_device(request))
    if body.project_id is not None:
        project = session.get(Project, body.project_id)
        if project is None or project.agent_id != conv.agent_id:
            raise ApiError(400, 'bad_request', 'project_id: that project is not this agent’s.')
    msg = post_message(
        session,
        events_of(request),
        conv,
        role=MessageRole.user,
        kind=MessageKind.text,
        body={'text': body.text},
        project_id=body.project_id,
        client_id=body.client_id,
    )
    return message_json(msg, threads_of(session, [msg.id]).get(msg.id))


def thread_title(msg: Message) -> str | None:
    if 'text' in msg.body_json:
        first_line = str(msg.body_json['text']).strip().splitlines()[0:1]
        return first_line[0][:TITLE_LENGTH] if first_line else None
    milestone = msg.body_json.get('milestone')
    return milestone.get('task_title') if isinstance(milestone, dict) else None


@router.post('/messages/{message_id}/thread')
def open_thread(message_id: str, request: Request, session: DbSession) -> JSONResponse:
    """Open the side thread on a message, or return the one it already has (B-08)."""
    msg, parent = own_message(session, message_id, current_device(request))
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
    side = own_conversation(session, conversation_id, current_device(request))
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
