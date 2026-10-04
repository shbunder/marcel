"""One round trip per table: write, read back in a fresh session, compare."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel

from marcel_hub.models import (
    Agent,
    AnsweredVia,
    Approval,
    ApprovalState,
    Artifact,
    ArtifactKind,
    Conversation,
    ConversationKind,
    Device,
    Message,
    MessageKind,
    MessageRole,
    QueuedReason,
    Schedule,
    ScheduleLocation,
    Task,
    TaskEvent,
    TaskEventType,
    TaskLocation,
    TaskOrigin,
    TaskState,
    UsageSnapshot,
    UsageWindow,
    new_id,
)  # fmt: skip

NOW = datetime(2026, 10, 4, 18, 6, 40, tzinfo=UTC)


def roundtrip[T: SQLModel](engine: Engine, row: T, *key: Any) -> T:
    with Session(engine, expire_on_commit=False) as s:
        s.add(row)
        s.commit()
    with Session(engine) as s:
        loaded = s.get(type(row), key[0] if len(key) == 1 else key)
        assert loaded is not None
        return loaded


def seed(engine: Engine) -> tuple[str, str]:
    """An agent and a task, which most other rows hang off."""
    agent = Agent(
        owner_user_id='usr_1', name='Marcel', animal='giraffe', palette='marcel', seed='M'
    )
    task = Task(
        agent_id=agent.id, title='t', location=TaskLocation.nuc, state=TaskState.working,
        model='sonnet', origin=TaskOrigin.marcel,
    )  # fmt: skip
    with Session(engine, expire_on_commit=False) as s:
        s.add(agent)
        s.commit()
        s.add(task)
        s.commit()
    return agent.id, task.id


def test_ids_are_prefixed_and_sortable() -> None:
    a, b = new_id('tsk'), new_id('tsk')
    assert a.startswith('tsk_') and a != b


def test_agent(engine: Engine) -> None:
    row = Agent(owner_user_id='u', name='Marcel', animal='giraffe', palette='marcel', seed='M')
    got = roundtrip(engine, row, row.id)
    assert (got.name, got.model_brain, got.model_worker) == ('Marcel', 'opus', 'sonnet')


def test_device(engine: Engine) -> None:
    row = Device(user_id='u', name="Shaun's iPhone", token_hash='h', ntfy_topic='t', last_seen=NOW)
    got = roundtrip(engine, row, row.id)
    assert (got.token_hash, got.ntfy_topic, got.last_seen) == ('h', 't', NOW)


def test_conversation_and_message(engine: Engine) -> None:
    agent_id, task_id = seed(engine)
    conv = Conversation(agent_id=agent_id, kind=ConversationKind.main, task_id=task_id)
    assert roundtrip(engine, conv, conv.id).kind is ConversationKind.main
    msg = Message(
        conversation_id=conv.id, role=MessageRole.agent, kind=MessageKind.milestone,
        body_json={'milestone': {'event': 'done'}}, task_id=task_id, client_id='c1',
    )  # fmt: skip
    got = roundtrip(engine, msg, msg.id)
    assert got.body_json == {'milestone': {'event': 'done'}}
    assert (got.kind, got.client_id) == (MessageKind.milestone, 'c1')


def test_task(engine: Engine) -> None:
    agent_id, _ = seed(engine)
    row = Task(
        agent_id=agent_id, title='Fix', location=TaskLocation.cloud, state=TaskState.queued,
        queued_reason=QueuedReason.cap, model='opus', origin=TaskOrigin.schedule,
        repo='shbunder/marcel', expected_by=NOW,
    )  # fmt: skip
    got = roundtrip(engine, row, row.id)
    assert (got.state, got.queued_reason, got.expected_by) == (
        TaskState.queued,
        QueuedReason.cap,
        NOW,
    )


def test_task_event_has_a_composite_key(engine: Engine) -> None:
    _, task_id = seed(engine)
    row = TaskEvent(task_id=task_id, seq=1, type=TaskEventType.state, data_json={'to': 'working'})
    got = roundtrip(engine, row, task_id, 1)
    assert (got.type, got.data_json) == (TaskEventType.state, {'to': 'working'})


def test_approval(engine: Engine) -> None:
    _, task_id = seed(engine)
    row = Approval(task_id=task_id, tool='Bash', summary='Run df', request_id='r1')
    assert roundtrip(engine, row, row.id).state is ApprovalState.open
    with Session(engine) as s:
        a = s.get(Approval, row.id)
        assert a
        a.state, a.answered_via, a.answered_at = ApprovalState.approved, AnsweredVia.app, NOW
        s.commit()
    with Session(engine) as s:
        a = s.get(Approval, row.id)
        assert a and (a.state, a.answered_via, a.answered_at) == (
            ApprovalState.approved, AnsweredVia.app, NOW,
        )  # fmt: skip


def test_artifact(engine: Engine) -> None:
    _, task_id = seed(engine)
    row = Artifact(
        task_id=task_id, kind=ArtifactKind.pr, title='PR', url='https://x/y',
        meta_json={'pr': {'number': 14, 'state': 'open'}}, self_change=True,
    )  # fmt: skip
    got = roundtrip(engine, row, row.id)
    assert (got.trusted, got.self_change, got.meta_json['pr']['number']) == (False, True, 14)


def test_schedule(engine: Engine) -> None:
    agent_id, _ = seed(engine)
    row = Schedule(
        agent_id=agent_id, name='Morning digest', cron='0 7 * * *', tz='Europe/Brussels',
        location=ScheduleLocation.brain, prompt='p', builtin=True, deadline_minutes=30,
    )  # fmt: skip
    got = roundtrip(engine, row, row.id)
    assert (got.paused, got.builtin, got.location) == (False, True, ScheduleLocation.brain)


def test_usage_snapshot(engine: Engine) -> None:
    row = UsageSnapshot(
        window=UsageWindow.five_hour, used_pct=62.5, resets_at=NOW, source='statusline'
    )
    got = roundtrip(engine, row, row.id)
    assert (got.window, got.used_pct, got.resets_at) == (UsageWindow.five_hour, 62.5, NOW)
