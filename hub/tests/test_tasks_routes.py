"""The `/api/tasks` routes, through the real app, checked against contracts/app-api.yaml."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_tasks_fakes import AGENT, FakeRunner, add_agent, put_task, validator

from marcel_hub.app import Settings, create_app
from marcel_hub.models import TaskState
from marcel_hub.tasks.ports import MemoryOutbox, NoRunner
from marcel_hub.tasks.registry import TaskRegistry

TASK = validator('app-api.yaml#/components/schemas/Task')
TASK_PAGE = validator('app-api.yaml#/components/schemas/TaskPage')
EVENT_PAGE = validator('app-api.yaml#/components/schemas/TaskEventPage')
ERROR = validator('app-api.yaml#/components/schemas/Error')
STEER = validator('channel.schema.json#/$defs/Steer')
SYSTEM_EVENT = validator('channel.schema.json#/$defs/SystemEvent')


class Hub:
    def __init__(self, client: TestClient, registry: TaskRegistry) -> None:
        self.client = client
        self.registry = registry
        self.runner = FakeRunner()
        self.outbox = MemoryOutbox()
        registry.runner = self.runner
        registry.outbox = self.outbox

    def task(self, state: TaskState, **fields: Any) -> str:
        return put_task(self.registry.engine, state, **fields)

    def post(self, path: str, **kw: Any) -> Any:
        return self.client.post(f'/api{path}', **kw)

    def get(self, path: str, **kw: Any) -> Any:
        return self.client.get(f'/api{path}', **kw)


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[Hub]:
    app = create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db'))
    with TestClient(app) as client:
        add_agent(app.state.engine)
        yield Hub(client, app.state.tasks)


def error_code(res: Any, status: int) -> str:
    assert res.status_code == status, res.text
    ERROR.validate(res.json())
    return res.json()['code']


# Archive -------------------------------------------------------------------------------------


def test_archiving_a_working_task_is_refused(hub: Hub) -> None:
    task_id = hub.task(TaskState.working)
    res = hub.post(f'/tasks/{task_id}/archive')
    assert error_code(res, 409) == 'task_not_finished'
    assert 'working' in res.json()['message']
    assert hub.registry.get(task_id).archived_at is None


@pytest.mark.parametrize('state', [TaskState.done, TaskState.failed, TaskState.stopped])
def test_an_archived_finished_task_leaves_the_default_list(hub: Hub, state: TaskState) -> None:
    task_id, other = hub.task(state), hub.task(TaskState.working)

    res = hub.post(f'/tasks/{task_id}/archive')

    assert res.status_code == 200
    TASK.validate(res.json())
    assert res.json()['archived_at']
    live = hub.get('/tasks', params={'agent_id': AGENT}).json()
    TASK_PAGE.validate(live)
    assert [t['id'] for t in live['items']] == [other]
    archived = hub.get('/tasks', params={'agent_id': AGENT, 'archived': 'true'}).json()
    assert [t['id'] for t in archived['items']] == [task_id]


def test_archiving_twice_keeps_the_first_time(hub: Hub) -> None:
    task_id = hub.task(TaskState.done)
    first = hub.post(f'/tasks/{task_id}/archive').json()['archived_at']
    assert hub.post(f'/tasks/{task_id}/archive').json()['archived_at'] == first


# Steer ---------------------------------------------------------------------------------------


@pytest.mark.parametrize('state', [TaskState.stopped, TaskState.failed])
def test_steering_a_finished_task_answers_task_finished(hub: Hub, state: TaskState) -> None:
    task_id = hub.task(state)
    res = hub.post(f'/tasks/{task_id}/messages', json={'text': 'One more thing'})
    assert error_code(res, 409) == 'task_finished'
    assert hub.outbox.tasks == {}
    assert hub.registry.events(task_id)[0] == []


def test_steering_a_done_task_sends_it_back_to_work(hub: Hub) -> None:
    task_id = hub.task(TaskState.done, session_id='ses-1')

    res = hub.post(
        f'/tasks/{task_id}/messages', json={'text': 'Also add a test for the empty case.'}
    )

    assert res.status_code == 202
    TASK.validate(res.json())
    assert res.json()['state'] == 'working'
    events = hub.get(f'/tasks/{task_id}/events').json()
    EVENT_PAGE.validate(events)
    note, state = events['items']
    assert (note['type'], note['data']) == (
        'note',
        {'author': 'user', 'text': 'Also add a test for the empty case.'},
    )
    assert state['data'] == {'from': 'done', 'to': 'working', 'reason': 'A follow-up was sent'}
    [frame] = hub.outbox.tasks[task_id]
    STEER.validate({**frame, 'seq': 1})
    assert frame['text'] == 'Also add a test for the empty case.'


@pytest.mark.parametrize('state', [TaskState.queued, TaskState.starting, TaskState.working])
def test_a_steer_waits_in_the_outbox_without_changing_state(hub: Hub, state: TaskState) -> None:
    task_id = hub.task(state)
    res = hub.post(f'/tasks/{task_id}/messages', json={'text': 'Use the staging database.'})
    assert res.status_code == 202
    assert res.json()['state'] == state.value
    assert [f['text'] for f in hub.outbox.tasks[task_id]] == ['Use the staging database.']


def test_steering_a_task_that_needs_you_answers_it(hub: Hub) -> None:
    task_id = hub.task(TaskState.needs_you, waiting_for='Which branch?')
    res = hub.post(f'/tasks/{task_id}/messages', json={'text': 'main'})
    assert res.json()['state'] == 'working'
    assert 'waiting_for' not in res.json()


@pytest.mark.parametrize(
    'body', [{}, {'text': ''}, {'text': 'x', 'extra': 1}, {'text': 'x' * 20001}, None]
)
def test_a_bad_steer_body_answers_400(hub: Hub, body: Any) -> None:
    task_id = hub.task(TaskState.working)
    res = hub.post(f'/tasks/{task_id}/messages', json=body)
    assert error_code(res, 400) == 'bad_request'


# Stop ----------------------------------------------------------------------------------------


def test_stopping_a_queued_task_removes_it_from_the_queue(hub: Hub) -> None:
    task_id = hub.task(TaskState.queued, queued_reason='cap')
    assert [t.id for t in hub.registry.queue()] == [task_id]

    res = hub.post(f'/tasks/{task_id}/stop')

    assert res.status_code == 200
    TASK.validate(res.json())
    assert res.json()['state'] == 'stopped'
    assert 'queued_reason' not in res.json()
    assert hub.registry.queue() == []
    assert hub.runner.stopped == []


def test_stopping_a_working_task_asks_the_runner(hub: Hub) -> None:
    task_id = hub.task(TaskState.working, session_id='ses-7')
    res = hub.post(f'/tasks/{task_id}/stop')
    assert res.json()['state'] == 'stopped'
    assert hub.runner.stopped == ['ses-7']


def test_stopping_a_finished_task_changes_nothing(hub: Hub) -> None:
    task_id = hub.task(TaskState.done, session_id='ses-7')
    res = hub.post(f'/tasks/{task_id}/stop')
    assert res.status_code == 200
    assert res.json()['state'] == 'done'
    assert hub.runner.stopped == []


def test_when_the_runner_is_down_stop_says_so_and_changes_nothing(hub: Hub) -> None:
    task_id = hub.task(TaskState.working, session_id='ses-7')
    hub.registry.runner = NoRunner()

    res = hub.post(f'/tasks/{task_id}/stop')

    assert error_code(res, 503) == 'runner_unreachable'
    assert 'not stopped' in res.json()['message']
    assert hub.registry.get(task_id).state == TaskState.working


# Hand-back -----------------------------------------------------------------------------------


def test_hand_back_reaches_the_brains_outbox(hub: Hub) -> None:
    task_id = hub.task(TaskState.needs_you)

    res = hub.post(f'/tasks/{task_id}/handback', json={'note': 'Try another approach.'})

    assert res.status_code == 202
    TASK.validate(res.json())
    [frame] = hub.outbox.brains[AGENT]
    SYSTEM_EVENT.validate({**frame, 'seq': 1})
    assert (frame['kind'], frame['task_id']) == ('handback', task_id)
    assert frame['data']['note'] == 'Try another approach.'
    [note] = hub.registry.events(task_id)[0]
    assert note.data_json == {'author': 'user', 'text': 'Try another approach.', 'handback': True}


def test_hand_back_without_a_note(hub: Hub) -> None:
    task_id = hub.task(TaskState.failed)
    assert hub.post(f'/tasks/{task_id}/handback').status_code == 202
    assert hub.outbox.brains[AGENT][0]['data']['note'] == ''


def test_a_bad_hand_back_body_answers_400(hub: Hub) -> None:
    task_id = hub.task(TaskState.failed)
    res = hub.post(f'/tasks/{task_id}/handback', json={'nope': 1})
    assert error_code(res, 400) == 'bad_request'


# Reading -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    'path', ['', '/events', '/messages', '/stop', '/archive', '/handback']
)  # fmt: skip
def test_an_unknown_task_answers_404(hub: Hub, path: str) -> None:
    call = hub.get if path in ('', '/events') else hub.post
    kw = {'json': {'text': 'hi'}} if path == '/messages' else {}
    assert error_code(call(f'/tasks/tsk_missing{path}', **kw), 404) == 'task_not_found'


def test_get_a_task(hub: Hub) -> None:
    task_id = hub.task(TaskState.working, repo='shbunder/marcel', worktree='/tmp/wt')
    body = hub.get(f'/tasks/{task_id}').json()
    TASK.validate(body)
    assert body['approval_ids'] == [] and body['artifact_count'] == 0
    assert body['created_at'].endswith('Z')


def test_the_list_filters_by_state_and_pages(hub: Hub) -> None:
    ids = [hub.task(TaskState.working) for _ in range(3)]
    hub.task(TaskState.done)

    first = hub.get('/tasks', params={'agent_id': AGENT, 'state': 'working', 'limit': 2}).json()
    assert len(first['items']) == 2
    rest = hub.get(
        '/tasks',
        params={'agent_id': AGENT, 'state': 'working', 'limit': 2, 'cursor': first['next_cursor']},
    ).json()
    assert 'next_cursor' not in rest
    assert {t['id'] for t in first['items'] + rest['items']} == set(ids)
    both = hub.get('/tasks', params=[('agent_id', AGENT), ('state', 'working'), ('state', 'done')])
    assert len(both.json()['items']) == 4


@pytest.mark.parametrize(
    'params',
    [
        {},
        {'agent_id': AGENT, 'state': 'sleeping'},
        {'agent_id': AGENT, 'archived': 'maybe'},
        {'agent_id': AGENT, 'limit': '0'},
        {'agent_id': AGENT, 'cursor': 'abc'},
    ],
)
def test_a_bad_list_query_answers_400(hub: Hub, params: dict[str, str]) -> None:
    assert error_code(hub.get('/tasks', params=params), 400) == 'bad_request'


def test_events_page_with_after(hub: Hub) -> None:
    task_id = hub.task(TaskState.working)
    for n in range(3):
        hub.post(f'/tasks/{task_id}/messages', json={'text': f'm{n}'})
    page = hub.get(f'/tasks/{task_id}/events', params={'limit': 2}).json()
    assert [e['seq'] for e in page['items']] == [1, 2] and page['has_more'] is True
    page = hub.get(f'/tasks/{task_id}/events', params={'after': 2}).json()
    assert [e['seq'] for e in page['items']] == [3] and page['has_more'] is False


@pytest.mark.parametrize('params', [{'after': '-1'}, {'limit': '201'}])
def test_a_bad_events_query_answers_400(hub: Hub, params: dict[str, str]) -> None:
    task_id = hub.task(TaskState.working)
    assert error_code(hub.get(f'/tasks/{task_id}/events', params=params), 400) == 'bad_request'
