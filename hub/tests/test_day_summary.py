"""The brain's day summary (brain-api.yaml `GET /brain/summary`): built from the hub's own records,
oldest first, in the owner's time zone, at most 4 KB. Nothing reaches a runner or `claude`."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session
from test_brain_api import Brain
from test_channel_fakes import ChannelHub, channel_hub

from marcel_hub.day_summary import LEFT_OUT, MAX_BYTES, day_summary
from marcel_hub.models import (
    Agent,
    Message,
    MessageKind,
    MessageRole,
    Task,
    TaskEvent,
    TaskEventType,
    TaskLocation,
    TaskOrigin,
    TaskState,
)
from marcel_hub.tasks.states import FINISHED

BRUSSELS = ZoneInfo('Europe/Brussels')
NOW = datetime(2026, 10, 13, 18, 0, tzinfo=UTC)  # 20:00 in Brussels


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[ChannelHub]:
    with channel_hub(tmp_path) as hub:
        yield hub


def say(hub: ChannelHub, at: datetime, text: str, role: MessageRole = MessageRole.user) -> None:
    with Session(hub.engine) as s:
        s.add(Message(conversation_id=hub.main_id, role=role, kind=MessageKind.text,
                      body_json={'text': text}, created_at=at))  # fmt: skip
        s.commit()


def thread(hub: ChannelHub, title: str, state: TaskState, made: datetime, changed: datetime) -> str:
    """A thread now in `state`; a finished one has its `state` event at `changed`, as the
    registry writes it."""
    with Session(hub.engine) as s:
        task = Task(agent_id=hub.agent_id, title=title, location=TaskLocation.nuc, state=state,
                    model='sonnet', origin=TaskOrigin.marcel, created_at=made,
                    updated_at=changed)  # fmt: skip
        s.add(task)
        s.commit()
        if state in FINISHED:
            moved = {'from': 'working', 'to': state.value, 'reason': 'x'}
            s.add(TaskEvent(task_id=task.id, seq=1, type=TaskEventType.state, at=changed,
                            data_json=moved))  # fmt: skip
            s.commit()
        return task.id


def test_an_old_thread_archived_or_moved_today_did_not_finish_today(hub: ChannelHub) -> None:
    # Archiving or moving a thread changes `updated_at`, not when it finished.
    old = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)
    task_id = thread(hub, 'Last week', TaskState.done, old, old + timedelta(hours=1))
    with Session(hub.engine) as s:
        task = s.get(Task, task_id)
        assert task is not None
        task.archived_at = task.updated_at = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
        s.add(task)
        s.commit()
    assert summary(hub)[1] == 'Nothing happened yet today.\nRunning now: nothing.\n'


def summary(hub: ChannelHub, now: datetime = NOW) -> tuple[str, str]:
    with Session(hub.engine) as s:
        agent = s.get(Agent, hub.agent_id)
        assert agent is not None
        today, text = day_summary(s, agent, BRUSSELS, now)
    return today.isoformat(), text


def test_a_quiet_day_still_says_so(hub: ChannelHub) -> None:
    assert summary(hub) == ('2026-10-13', 'Nothing happened yet today.\nRunning now: nothing.\n')


def test_the_day_oldest_first_in_the_owners_time_zone(hub: ChannelHub) -> None:
    say(hub, datetime(2026, 10, 12, 21, 30, tzinfo=UTC), 'Yesterday, 23:30 in Brussels')
    say(hub, datetime(2026, 10, 12, 22, 30, tzinfo=UTC), 'Fix the flaky login test')  # 00:30 today
    say(hub, datetime(2026, 10, 13, 7, 0, tzinfo=UTC), 'On it.', MessageRole.agent)
    thread(
        hub,
        'Fix flaky login test',
        TaskState.done,
        datetime(2026, 10, 13, 6, 59, tzinfo=UTC),
        datetime(2026, 10, 13, 9, 40, tzinfo=UTC),
    )
    thread(
        hub,
        'Compare energy offers',
        TaskState.needs_you,
        datetime(2026, 10, 12, 8, 0, tzinfo=UTC),
        datetime(2026, 10, 13, 10, 0, tzinfo=UTC),
    )
    thread(
        hub,
        'Old and finished',
        TaskState.done,
        datetime(2026, 10, 11, 8, 0, tzinfo=UTC),
        datetime(2026, 10, 12, 8, 0, tzinfo=UTC),
    )
    _, text = summary(hub)
    assert text == (
        '- 00:30 Owner: Fix the flaky login test\n'
        '- 08:59 started "Fix flaky login test" (nuc)\n'
        '- 09:00 Marcel: On it.\n'
        '- 11:40 "Fix flaky login test" done\n'
        'Running now: "Compare energy offers" (nuc, needs_you)\n'
    )


def test_a_long_day_keeps_the_newest_lines_within_4_kb(hub: ChannelHub) -> None:
    start = datetime(2026, 10, 13, 6, 0, tzinfo=UTC)
    for n in range(40):
        say(hub, start + timedelta(minutes=n), f'message {n:02d} ' + 'é' * 300)
    thread(hub, 'Still going', TaskState.working, start, start)
    _, text = summary(hub)
    assert len(text.encode()) <= MAX_BYTES
    assert text.startswith(LEFT_OUT + '\n')
    assert 'message 39' in text and 'message 00' not in text, 'the newest lines are kept'
    assert text.endswith('Running now: "Still going" (nuc, working)\n'), 'running is always kept'


def test_the_route_answers_the_brain_and_refuses_a_worker(hub: ChannelHub) -> None:
    brain = Brain(hub, hub.brain())
    res = brain.get('/summary')
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {'date', 'text'} and 1 <= len(body['text'].encode()) <= MAX_BYTES
    datetime.strptime(body['date'], '%Y-%m-%d')
    _, sid = hub.task()
    worker = Brain(hub, sid)
    res = worker.get('/summary')
    assert res.status_code == 403 and res.json()['code'] == 'not_brain'
