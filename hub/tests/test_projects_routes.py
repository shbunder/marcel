"""`/api/projects` and `POST /api/tasks/{id}/move`, through the real app, checked against the
contract. Covers the controls: the inbox and busy projects cannot be archived, nothing moves into
an archived project, slugs are unique among live projects and match the pattern, and only the
owner sees a project."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from test_conversations import Phone, add_agent, add_phone, events
from test_tasks_fakes import put_task, validator

from marcel_hub.app import Settings, create_app
from marcel_hub.models import Artifact, ArtifactKind, Task, TaskState

PROJECT = validator('app-api.yaml#/components/schemas/Project')
TASK = validator('app-api.yaml#/components/schemas/Task')
EVENT = validator('events.schema.json')


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'm.db'))) as c:
        yield c


@pytest.fixture
def phone(client: TestClient) -> Phone:
    return add_phone(client)


@pytest.fixture
def agent(client: TestClient) -> str:
    return add_agent(client)[0]


class Api:
    def __init__(self, client: TestClient, phone: Phone) -> None:
        self.client, self.phone = client, phone

    def __call__(self, method: str, path: str, **kw: Any) -> Any:
        return self.client.request(method, f'/api{path}', headers=self.phone.headers, **kw)

    def create(self, agent: str, name: str, **fields: Any) -> dict[str, Any]:
        res = self('POST', '/projects', json={'agent_id': agent, 'name': name, **fields})
        assert res.status_code == 201, res.text
        PROJECT.validate(res.json())
        return res.json()


@pytest.fixture
def api(client: TestClient, phone: Phone) -> Api:
    return Api(client, phone)


def engine(client: TestClient) -> Any:
    return client.app.state.engine  # type: ignore[attr-defined]


def frames(client: TestClient, after: int = 0) -> list[dict[str, Any]]:
    out = []
    for e in events(client):
        if e.id > after:
            body = {'id': e.id, 'type': e.type, 'at': e.at.isoformat(), 'data': e.data_json}
            EVENT.validate(body)
            out.append(body)
    return out


def last_id(client: TestClient) -> int:
    all_events = events(client)
    return all_events[-1].id if all_events else 0


# ---- listing and creating --------------------------------------------------------------------


def test_the_map_lists_live_projects_with_the_inbox_last(
    api: Api, client: TestClient, agent: str
) -> None:
    energy = api.create(agent, 'Energy contracts', goal='Cheaper gas.')
    garden = api.create(agent, 'Garden')
    put_task(engine(client), TaskState.working, agent, project_id=energy['id'])
    listed = api('GET', f'/projects?agent_id={agent}').json()
    for body in listed:
        PROJECT.validate(body)
    assert [p['slug'] for p in listed] == ['energy-contracts', 'garden', 'inbox']
    assert listed[0]['counts'] == {'working': 1, 'needs_you': 0, 'done_7d': 0}
    assert 'goal' not in garden  # unset fields are left out
    assert api('GET', f'/projects?agent_id={agent}&archived=true').json() == []


def test_creating_sends_project_created_and_makes_the_notes_file(
    api: Api, client: TestClient, agent: str, tmp_path: Path
) -> None:
    before = last_id(client)
    project = api.create(agent, 'Garden', goal='Spring planting.',
                         claude_project_url='https://claude.ai/project/1')  # fmt: skip
    assert project['slug'] == 'garden'
    assert project['claude_project_url'] == 'https://claude.ai/project/1'
    assert project['counts'] == {'working': 0, 'needs_you': 0, 'done_7d': 0}
    sent = frames(client, before)
    assert [f['type'] for f in sent] == ['project.created']
    assert sent[0]['data'] == project
    [notes] = (tmp_path / 'memory' / 'agents').glob('*/projects/garden.md')  # its agent's (S-17.4)
    assert notes.read_text().startswith('# Garden\n\n## What it is for\n\nSpring planting.')


def test_a_taken_slug_is_a_409(api: Api, agent: str) -> None:
    api.create(agent, 'Garden', slug='garden')
    for slug in ('garden', 'inbox'):
        res = api('POST', '/projects', json={'agent_id': agent, 'name': 'Other', 'slug': slug})
        assert res.status_code == 409
        assert res.json()['code'] == 'slug_taken'


def test_a_slug_can_be_used_again_once_its_project_is_archived(api: Api, agent: str) -> None:
    old = api.create(agent, 'Garden', slug='garden')
    assert api('POST', f'/projects/{old["id"]}/archive').status_code == 200
    new = api.create(agent, 'Garden again', slug='garden')
    assert new['id'] != old['id']


def test_another_agent_may_use_the_same_slug(api: Api, client: TestClient, agent: str) -> None:
    other = add_agent(client)[0]
    api.create(agent, 'Garden', slug='garden')
    assert api.create(other, 'Garden', slug='garden')['slug'] == 'garden'


@pytest.mark.parametrize(
    'body',
    [
        {'name': 'x', 'slug': 'Garden'},
        {'name': 'x', 'slug': '-garden'},
        {'name': 'x', 'slug': 'g' * 41},
        {'name': 'x', 'slug': 'gar den'},
        {'name': ''},
        {'name': 'x' * 61},
        {'name': 'x', 'colour': 'red'},
        {'name': 'x', 'claude_project_url': 'not a url'},
        {},
    ],
)
def test_a_bad_new_project_is_a_400(api: Api, agent: str, body: dict[str, Any]) -> None:
    res = api('POST', '/projects', json={'agent_id': agent, **body} if body else None)
    assert res.status_code == 400
    assert res.json()['code'] == 'bad_request'


def test_list_needs_an_agent_and_a_boolean(api: Api, agent: str) -> None:
    assert api('GET', '/projects').status_code == 400
    assert api('GET', f'/projects?agent_id={agent}&archived=maybe').status_code == 400


# ---- owner only ------------------------------------------------------------------------------


def test_unknown_ids_are_not_found(client: TestClient, api: Api, agent: str) -> None:
    project = api.create(agent, 'Garden')
    assert api('GET', '/projects?agent_id=agt_nope').json() == []
    for method, path, body in [
        ('GET', '/projects/prj_nope', None),
        ('PATCH', '/projects/prj_nope', {'name': 'Mine now'}),
        ('POST', '/projects/prj_nope/archive', None),
    ]:
        res = api(method, path, json=body)
        assert res.status_code == 404, path
        assert res.json()['code'] == 'project_not_found'
    res = api('POST', '/projects', json={'agent_id': 'agt_nope', 'name': 'Sneaky'})
    assert (res.status_code, res.json()['code']) == (404, 'agent_not_found')
    res = api('POST', '/tasks/tsk_nope/move', json={'project_id': project['id']})
    assert (res.status_code, res.json()['code']) == (404, 'task_not_found')


def test_another_phone_sees_and_changes_every_buddys_projects(
    client: TestClient, api: Api, agent: str
) -> None:
    # One person, many buddies (ADR-013): no phone is a stranger to a project.
    project = api.create(agent, 'Garden')
    other = Api(client, add_phone(client))
    assert [p['id'] for p in other('GET', f'/projects?agent_id={agent}').json()][0] == project['id']
    res = other('PATCH', f'/projects/{project["id"]}', json={'name': 'Allotment'})
    assert res.status_code == 200 and res.json()['name'] == 'Allotment'


# ---- changing and archiving ------------------------------------------------------------------


def test_update_changes_the_fields_sent_and_says_so(
    api: Api, client: TestClient, agent: str
) -> None:
    project = api.create(agent, 'Energy', goal='Old goal.')
    before = last_id(client)
    res = api('PATCH', f'/projects/{project["id"]}',
              json={'instructions': 'Yearly totals.', 'name': 'Energy contracts'})  # fmt: skip
    assert res.status_code == 200
    body = res.json()
    PROJECT.validate(body)
    assert (body['name'], body['goal'], body['instructions'], body['slug']) == (
        'Energy contracts',
        'Old goal.',
        'Yearly totals.',
        'energy',
    )
    assert [f['type'] for f in frames(client, before)] == ['project.updated']


@pytest.mark.parametrize('body', [{}, {'name': None}, {'name': ''}, {'slug': 'new'}])
def test_a_bad_update_is_a_400(api: Api, agent: str, body: dict[str, Any]) -> None:
    project = api.create(agent, 'Energy')
    assert api('PATCH', f'/projects/{project["id"]}', json=body).status_code == 400


def test_unknown_projects_are_404(api: Api) -> None:
    assert api('GET', '/projects/prj_missing').status_code == 404
    assert api('PATCH', '/projects/prj_missing', json={'goal': 'x'}).status_code == 404
    assert api('POST', '/projects/prj_missing/archive').status_code == 404


def test_the_inbox_cannot_be_archived(api: Api, agent: str) -> None:
    inbox = api('GET', f'/projects?agent_id={agent}').json()[-1]
    res = api('POST', f'/projects/{inbox["id"]}/archive')
    assert res.status_code == 409
    assert res.json()['code'] == 'project_is_inbox'
    assert api('GET', f'/projects/{inbox["id"]}').json()['archived'] is False


@pytest.mark.parametrize('state', [TaskState.working, TaskState.queued, TaskState.needs_you])
def test_a_project_with_live_tasks_cannot_be_archived(
    api: Api, client: TestClient, agent: str, state: TaskState
) -> None:
    project = api.create(agent, 'Energy')
    put_task(engine(client), state, agent, project_id=project['id'])
    res = api('POST', f'/projects/{project["id"]}/archive')
    assert res.status_code == 409
    assert res.json()['code'] == 'project_busy'
    assert 'still has 1 live thread' in res.json()['message']


def test_a_finished_project_archives(api: Api, client: TestClient, agent: str) -> None:
    project = api.create(agent, 'Energy')
    for state in (TaskState.done, TaskState.failed, TaskState.stopped):
        put_task(engine(client), state, agent, project_id=project['id'])
    res = api('POST', f'/projects/{project["id"]}/archive')
    assert res.status_code == 200
    assert res.json()['archived'] is True
    assert api('POST', f'/projects/{project["id"]}/archive').json()['archived'] is True
    archived = api('GET', f'/projects?agent_id={agent}&archived=true').json()
    assert [p['id'] for p in archived] == [project['id']]


# ---- moving tasks ----------------------------------------------------------------------------


def test_moving_a_task_files_it_and_tells_the_app(api: Api, client: TestClient, agent: str) -> None:
    inbox = api('GET', f'/projects?agent_id={agent}').json()[-1]
    energy = api.create(agent, 'Energy')
    task_id = put_task(engine(client), TaskState.working, agent)
    with Session(engine(client)) as s:
        s.add(Artifact(task_id=task_id, kind=ArtifactKind.link, title='L', url='https://a.b'))
        s.commit()
    before = last_id(client)
    res = api('POST', f'/tasks/{task_id}/move', json={'project_id': energy['id']})
    assert res.status_code == 200
    TASK.validate(res.json())
    assert res.json()['project_id'] == energy['id']
    sent = frames(client, before)
    # The avatar hears of the working task too (S-04.8; test_agent_state covers it).
    assert [f['type'] for f in sent].count('agent.state') == 1
    sent = [f for f in sent if f['type'] != 'agent.state']
    assert [f['type'] for f in sent] == [
        'task.moved',
        'task.updated',
        'project.updated',
        'project.updated',
        'artifact.updated',  # its item moved with it (S-04.11)
    ]
    moved = sent[4]['data']
    assert moved['artifact']['project_id'] == energy['id']
    assert moved['version'] == {**moved['version'], 'artifact_id': moved['artifact']['id'],
                                'version': 1}  # fmt: skip
    # Both projects' boards are marked for a rebuild.
    assert {inbox['id'], energy['id']} <= set(client.app.state.boards.due())  # type: ignore[attr-defined]  # app is typed as ASGI
    assert sent[0]['data'] == {
        'task_id': task_id,
        'from_project_id': inbox['id'],
        'to_project_id': energy['id'],
    }
    assert [sent[2]['data']['id'], sent[3]['data']['id']] == [energy['id'], inbox['id']]
    assert sent[2]['data']['counts']['working'] == 1
    assert sent[3]['data']['counts']['working'] == 0
    # The task's items go with it, and the lists follow.
    items = api('GET', f'/artifacts?project_id={energy["id"]}').json()['items']
    assert [a['project_id'] for a in items] == [energy['id']]
    assert api('GET', f'/artifacts?project_id={inbox["id"]}').json()['items'] == []
    tasks = api('GET', f'/tasks?agent_id={agent}&project_id={energy["id"]}').json()['items']
    assert [t['id'] for t in tasks] == [task_id]
    assert api('GET', f'/tasks?agent_id={agent}&project_id={inbox["id"]}').json()['items'] == []


def test_moving_into_the_same_project_changes_nothing(
    api: Api, client: TestClient, agent: str
) -> None:
    inbox = api('GET', f'/projects?agent_id={agent}').json()[-1]
    task_id = put_task(engine(client), TaskState.working, agent)
    before = last_id(client)
    res = api('POST', f'/tasks/{task_id}/move', json={'project_id': inbox['id']})
    assert res.status_code == 200
    assert frames(client, before) == []


def test_nothing_moves_into_an_archived_project(api: Api, client: TestClient, agent: str) -> None:
    old = api.create(agent, 'Old')
    api('POST', f'/projects/{old["id"]}/archive')
    task_id = put_task(engine(client), TaskState.working, agent)
    res = api('POST', f'/tasks/{task_id}/move', json={'project_id': old['id']})
    assert res.status_code == 409
    assert res.json()['code'] == 'project_archived'
    with Session(engine(client)) as s:
        task = s.get(Task, task_id)
        assert task is not None and task.project_id != old['id']


def test_a_task_moves_only_within_its_agent(api: Api, client: TestClient, agent: str) -> None:
    other = api.create(add_agent(client)[0], 'Elsewhere')
    task_id = put_task(engine(client), TaskState.working, agent)
    res = api('POST', f'/tasks/{task_id}/move', json={'project_id': other['id']})
    assert (res.status_code, res.json()['code']) == (404, 'project_not_found')
    res = api('POST', f'/tasks/{task_id}/move', json={'project_id': 'prj_missing'})
    assert (res.status_code, res.json()['code']) == (404, 'project_not_found')
    res = api('POST', '/tasks/tsk_missing/move', json={'project_id': other['id']})
    assert (res.status_code, res.json()['code']) == (404, 'task_not_found')
    assert api('POST', f'/tasks/{task_id}/move', json={}).status_code == 400
