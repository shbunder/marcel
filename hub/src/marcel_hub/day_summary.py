"""The brain's day summary (brain-api.yaml `GET /brain/summary`, 0.1.3; ADR-010).

After Claude Code compacts the brain's context, the plugin's `SessionStart` hook asks for this so
the brain keeps the thread of the day. Built from the hub's own records, with no model call, in the
owner's time zone (the `tz` setting), oldest first:

- each message of the main conversation today (the person's, the agent's, a thread's report), in
  the same words the rollover seed uses (`message_line`);
- each thread started today, and each that finished today (`done`, `failed`, `stopped`), timed by
  its `state` task event: archiving or moving an old thread changes it, but never makes it
  "finished today";
- the threads running now.

At most 4 KB of UTF-8: past that, the oldest lines go first and one line says so. A day with
nothing yet still says so in words, never an empty text.
"""

from datetime import UTC, date, datetime, time, tzinfo
from typing import Any

from sqlmodel import Session, col, select

from marcel_hub.conversations import ensure_main_conversation
from marcel_hub.models import (
    Agent,
    Message,
    MessageKind,
    MessageRole,
    Task,
    TaskEvent,
    TaskEventType,
    TaskState,
)
from marcel_hub.tasks.states import FINISHED

MAX_BYTES = 4096
MAX_LINE = 400  # the rollover seed's limit for one message
LEFT_OUT = '(earlier lines left out)'
RUNNING = (TaskState.queued, TaskState.starting, TaskState.working, TaskState.needs_you,
           TaskState.silent, TaskState.unknown)  # fmt: skip


def _utc(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def message_line(msg: Message, name: str, tz: tzinfo | None) -> str:
    """One message as a line: `- HH:MM who: text`. Shared with the rollover seed (`brain.py`)."""
    at = _utc(msg.created_at)
    local = at.astimezone(tz) if tz is not None else at.astimezone()
    who = {MessageRole.user: 'Owner', MessageRole.agent: name}.get(msg.role, 'Marcel hub')
    body: dict[str, Any] = msg.body_json or {}
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


def day_summary(session: Session, agent: Agent, tz: tzinfo, now: datetime) -> tuple[date, str]:
    """Today's date in `tz` and the summary text (1 to `MAX_BYTES` bytes of UTF-8)."""
    today = _utc(now).astimezone(tz).date()
    start = datetime.combine(today, time(0), tzinfo=tz).astimezone(UTC)
    main = ensure_main_conversation(session, agent)
    events: list[tuple[datetime, str]] = []
    for msg in session.exec(
        select(Message)
        .where(Message.conversation_id == main.id)
        .where(Message.created_at >= start)
        .order_by(col(Message.created_at))
    ):
        events.append((_utc(msg.created_at), message_line(msg, agent.name, tz)))
    tasks = list(
        session.exec(
            select(Task)
            .where(Task.agent_id == agent.id)
            .where((col(Task.created_at) >= start) | col(Task.state).in_(list(RUNNING)))
        )
    )
    for task in tasks:
        created = _utc(task.created_at)
        if created >= start:
            local = created.astimezone(tz)
            line = f'- {local:%H:%M} started "{task.title}" ({task.location.value})'
            events.append((created, line))
    finished = {state.value for state in FINISHED}
    for event, title in session.exec(
        select(TaskEvent, Task.title)
        .join(Task, col(Task.id) == col(TaskEvent.task_id))
        .where(Task.agent_id == agent.id)
        .where(TaskEvent.type == TaskEventType.state)
        .where(TaskEvent.at >= start)
    ):
        to = (event.data_json or {}).get('to')
        if to in finished:
            at = _utc(event.at)
            events.append((at, f'- {at.astimezone(tz):%H:%M} "{title}" {to}'))
    events.sort(key=lambda e: e[0])
    lines = [line for _, line in events] or ['Nothing happened yet today.']
    running = sorted((t for t in tasks if t.state in RUNNING), key=lambda t: _utc(t.created_at))
    tail = [
        'Running now: '
        + (
            '; '.join(f'"{t.title}" ({t.location.value}, {t.state.value})' for t in running)
            if running
            else 'nothing.'
        )
    ]
    return today, _fit(lines, tail)


def _fit(lines: list[str], tail: list[str]) -> str:
    """Keep the newest lines that fit in `MAX_BYTES`, with the `tail` always last."""
    text = '\n'.join(lines + tail) + '\n'
    if len(text.encode()) <= MAX_BYTES:
        return text
    kept = list(lines)
    while kept:
        kept.pop(0)
        text = '\n'.join([LEFT_OUT, *kept, *tail]) + '\n'
        if len(text.encode()) <= MAX_BYTES:
            return text
    return ('\n'.join([LEFT_OUT, *tail]) + '\n').encode()[:MAX_BYTES].decode(errors='ignore')
