"""`POST /channel/session-end` (S-05.8, session-api.yaml `sessionEnded`): the marcel plugin's `Stop`
and `SessionEnd` hooks, in the real app with a fake runner. Nothing reaches a runner or `claude`."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, select
from test_channel_fakes import SECRET, ChannelHub, channel_hub

from marcel_hub.models import ChannelFrame, Task, TaskOrigin, TaskState

URL = '/channel/session-end'


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[ChannelHub]:
    with channel_hub(tmp_path) as hub:
        yield hub


def hook(hub: ChannelHub, sid: str, body: Any, secret: str = SECRET, **headers: str) -> Any:
    return hub.client.post(
        URL, json=body, headers={'X-Marcel-Secret': secret, 'X-Marcel-Session': sid, **headers}
    )


def state(hub: ChannelHub, task_id: str) -> TaskState:
    with Session(hub.engine) as s:
        task = s.get(Task, task_id)
        assert task is not None
        return task.state


def brain_frames(hub: ChannelHub) -> list[dict[str, Any]]:
    with Session(hub.engine) as s:
        rows = s.exec(
            select(ChannelFrame).where(ChannelFrame.target == f'conversation:{hub.main_id}')
        )
        return [r.frame_json for r in rows]


@pytest.mark.parametrize(
    ('now', 'owed'),
    [
        (TaskState.working, True),
        (TaskState.needs_you, True),
        (TaskState.done, False),
        (TaskState.failed, False),
        (TaskState.silent, False),
    ],
)
def test_stop_asks_whether_a_report_is_owed(hub: ChannelHub, now: TaskState, owed: bool) -> None:
    task_id, sid = hub.task(now)
    res = hook(hub, sid, {'event': 'stop'})
    assert res.status_code == 200 and res.json() == {'report_owed': owed}
    assert state(hub, task_id) == now, 'a stop changes nothing'


def test_a_worker_that_ends_without_a_report_fails_and_the_brain_is_told(hub: ChannelHub) -> None:
    # Control: delete the "ended without a report" branch and this goes red.
    task_id, sid = hub.task(TaskState.working)
    res = hook(hub, sid, {'event': 'session_end', 'reason': 'prompt_input_exit'})
    assert res.status_code == 200 and res.json() == {'report_owed': True}
    assert state(hub, task_id) == TaskState.failed
    [moved] = [e for e in hub.task_events(task_id, 'state') if e.data_json['to'] == 'failed']
    assert moved.data_json['reason'].startswith('ended_without_report: ')
    assert 'prompt_input_exit' in moved.data_json['reason']
    [told] = [f for f in brain_frames(hub) if f.get('kind') == 'task_failed']
    assert told['task_id'] == task_id and 'ended_without_report' in told['text']


def test_a_silent_task_whose_session_ends_fails_too(hub: ChannelHub) -> None:
    # task-states.md: silent → failed when the session ended without a report. Not "owed" (the
    # Stop hook does not remind a silent worker), but the end is the sign B-23 waited for.
    task_id, sid = hub.task(TaskState.silent)
    res = hook(hub, sid, {'event': 'session_end', 'reason': 'other'})
    assert res.json() == {'report_owed': False}
    assert state(hub, task_id) == TaskState.failed


def test_a_task_that_already_ended_only_has_its_end_logged(
    hub: ChannelHub, caplog: pytest.LogCaptureFixture
) -> None:
    task_id, sid = hub.task(TaskState.done)
    with caplog.at_level('INFO'):
        res = hook(hub, sid, {'event': 'session_end', 'reason': 'other'})
    assert res.json() == {'report_owed': False}
    assert state(hub, task_id) == TaskState.done
    assert brain_frames(hub) == []
    assert 'ended (other); the task is already done' in caplog.text


@pytest.mark.parametrize('who', ['brain', 'adopted'])
def test_a_brain_or_adopted_session_never_owes_a_report(hub: ChannelHub, who: str) -> None:
    if who == 'brain':
        sid, task_id = hub.brain(), None
    else:
        task_id, sid = hub.task(TaskState.working, origin=TaskOrigin.adopted)
    for event in ({'event': 'stop'}, {'event': 'session_end', 'reason': 'logout'}):
        assert hook(hub, sid, event).json() == {'report_owed': False}
    if task_id is not None:
        assert state(hub, task_id) == TaskState.working


def test_who_is_asking_is_checked_like_the_channel(hub: ChannelHub) -> None:
    task_id, sid = hub.task(TaskState.working)
    wrong = hook(hub, sid, {'event': 'session_end'}, secret='hs_' + 'f' * 40)
    assert wrong.status_code == 401 and wrong.json()['code'] == 'bad_secret'
    stranger = hook(hub, 'not-a-session', {'event': 'session_end'})
    assert stranger.status_code == 401 and stranger.json()['code'] == 'unknown_session'
    # Before the body: a caller without the secret learns nothing about it.
    assert hook(hub, sid, {'event': 'bye'}, secret='').status_code == 401
    tunnel = hook(hub, sid, {'event': 'stop'}, **{'cf-connecting-ip': '203.0.113.9'})
    assert tunnel.status_code == 404
    assert state(hub, task_id) == TaskState.working


@pytest.mark.parametrize(
    'body',
    [{'event': 'bye'}, {'event': 'stop', 'reason': ''}, {'event': 'stop', 'x': 1}, [], 'stop'],
)
def test_a_bad_body_from_a_known_session_is_400(hub: ChannelHub, body: Any) -> None:
    _, sid = hub.task(TaskState.working)
    res = hook(hub, sid, body)
    assert res.status_code == 400 and res.json()['code'] == 'bad_request'


def test_not_json_is_400(hub: ChannelHub) -> None:
    _, sid = hub.task(TaskState.working)
    headers = {'X-Marcel-Secret': SECRET, 'X-Marcel-Session': sid}
    res = hub.client.post(URL, content=b'not json', headers=headers)
    assert res.status_code == 400


def test_no_secret_on_the_hub_is_503(hub: ChannelHub) -> None:
    _, sid = hub.task(TaskState.working)
    (hub.tmp_path / 'hub-secret').unlink()
    res = hook(hub, sid, {'event': 'stop'})
    assert res.status_code == 503 and res.json()['code'] == 'no_secret'
