"""Projects, living artifacts and boards (S-02.5 operations), and the items run: flows 4-6."""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import AUTH, frames_until, tag
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from marcel_mock_hub import contract

MAIN = '/api/conversations/cnv_main_marcel/messages'
ENERGY = 'prj_01JA0A1'
MARCEL_DEV = 'prj_01J9ZM1'
INBOX = 'prj_inbox'
DOC = 'art_01JA0B1'


def check(name: str, body: Any) -> None:
    contract.validator(f'app-api.yaml#/components/schemas/{name}').validate(body)


def order(frames: list[dict[str, Any]]) -> list[tuple[str, str | None]]:
    return [(f['type'], tag(f)) for f in frames]


def projects(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    resp = client.get('/api/projects', params={'agent_id': 'agt_marcel', **params})
    assert resp.status_code == 200
    for project in resp.json():
        check('Project', project)
    return resp.json()


def board_of(client: TestClient, project_id: str, version: int | None = None) -> dict[str, Any]:
    board_id = client.get(f'/api/projects/{project_id}').json()['board_artifact_id']
    params = {'version': version} if version else {}
    resp = client.get(f'/api/artifacts/{board_id}/content', params=params)
    assert resp.status_code == 200
    assert resp.headers['content-type'].startswith('application/json')
    content = resp.json()
    contract.validate_dashboard(content)
    return content


def section(board: dict[str, Any], title: str) -> list[str]:
    """The text of every list entry in one section of a board."""
    (found,) = [w for w in board['widgets'] if w['type'] == 'section' and w['title'] == title]
    return [item['text'] for widget in found['widgets'] for item in widget['items']]


def drain(ws: WebSocketTestSession, count: int) -> list[dict[str, Any]]:
    frames = [ws.receive_json() for _ in range(count)]
    for frame in frames:
        contract.validate_event(frame)
    return frames


# ---- the seed ---------------------------------------------------------------------------------


def test_the_map_lists_live_projects_with_inbox_last(client: TestClient):
    listed = projects(client)
    ids = [p['id'] for p in listed]
    assert ids[-1] == INBOX
    assert set(ids) == {INBOX, MARCEL_DEV, ENERGY}
    stamps = [p['updated_at'] for p in listed[:-1]]
    assert stamps == sorted(stamps, reverse=True)
    assert projects(client, archived='true') == []
    assert projects(client, agent_id='agt_nobody') == []


def test_every_project_has_a_valid_board(client: TestClient):
    for project in projects(client):
        board = client.get(f'/api/artifacts/{project["board_artifact_id"]}').json()
        check('Artifact', board)
        assert board['kind'] == 'board'
        assert board['project_id'] == project['id']
        content = board_of(client, project['id'])
        assert content['title'] == project['name']


def test_every_task_and_artifact_belongs_to_a_known_project(client: TestClient):
    buddies = [a['id'] for a in client.get('/api/me').json()['agents']]
    known = {p['id'] for b in buddies for p in projects(client, agent_id=b)}
    tasks = client.get('/api/tasks').json()['items']
    artifacts = client.get('/api/artifacts').json()['items']
    assert tasks and artifacts
    for task in tasks:
        check('Task', task)
        assert task['project_id'] in known
    for artifact in artifacts:
        check('Artifact', artifact)
        assert artifact['project_id'] in known


def test_counts_follow_the_tasks(client: TestClient):
    inbox = client.get(f'/api/projects/{INBOX}').json()
    assert inbox['counts']['needs_you'] == 1  # the contract's example task waits on you
    dev = client.get(f'/api/projects/{MARCEL_DEV}').json()
    assert dev['counts']['working'] == 1
    assert section(board_of(client, INBOX), 'Waiting on you') == [
        client.get('/api/approvals').json()[0]['summary']
    ]


# ---- project operations -----------------------------------------------------------------------


def test_creating_a_project_makes_its_board(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        ws.receive_json()
        resp = client.post('/api/projects', json={'agent_id': 'agt_marcel', 'name': 'Garden'})
        assert resp.status_code == 201
        project = resp.json()
        frames = drain(ws, 2)
    check('Project', project)
    assert project['slug'] == 'garden'
    assert project['counts'] == {'working': 0, 'needs_you': 0, 'done_7d': 0}
    assert order(frames) == [('artifact.created', 'board'), ('project.created', 'garden')]
    assert frames[0]['data']['id'] == project['board_artifact_id']
    assert frames[1]['data'] == project
    assert client.get(f'/api/projects/{project["id"]}').json() == project
    assert section(board_of(client, project['id']), 'In flight') == []
    assert project['id'] in [p['id'] for p in projects(client)]


def test_a_taken_slug_is_a_409(client: TestClient):
    resp = client.post(
        '/api/projects', json={'agent_id': 'agt_marcel', 'name': 'Energy', 'slug': 'energy'}
    )
    assert resp.status_code == 409
    check('Error', resp.json())
    assert resp.json()['code'] == 'slug_taken'


@pytest.mark.parametrize(
    ('name', 'slug'),
    [('Home energy!', 'home-energy'), ('Été', 't'), ('???', 'project'), ('a' * 60, 'a' * 40)],
)
def test_a_slug_is_derived_from_the_name(client: TestClient, name: str, slug: str):
    resp = client.post('/api/projects', json={'agent_id': 'agt_marcel', 'name': name})
    assert resp.status_code == 201
    assert resp.json()['slug'] == slug


def test_updating_a_project_rebuilds_its_board(client: TestClient):
    before = client.get(
        f'/api/artifacts/{client.get(f"/api/projects/{ENERGY}").json()["board_artifact_id"]}'
    ).json()
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        ws.receive_json()
        resp = client.patch(f'/api/projects/{ENERGY}', json={'name': 'Home energy'})
        assert resp.status_code == 200
        frames = drain(ws, 2)
    assert resp.json()['name'] == 'Home energy'
    assert order(frames) == [('artifact.updated', 'board'), ('project.updated', 'energy')]
    assert frames[0]['data']['artifact']['current_version'] == before['current_version'] + 1
    assert board_of(client, ENERGY)['title'] == 'Home energy'
    resp = client.patch(f'/api/projects/{ENERGY}', json={'goal': 'Lower the bill by 20%.'})
    assert resp.json()['goal'] == 'Lower the bill by 20%.'


def test_archiving(client: TestClient):
    inbox = client.post(f'/api/projects/{INBOX}/archive')
    assert inbox.status_code == 409
    check('Error', inbox.json())
    busy = client.post(f'/api/projects/{MARCEL_DEV}/archive')  # a task in it is working
    assert busy.status_code == 409
    assert 'still' in busy.json()['message']

    resp = client.post(f'/api/projects/{ENERGY}/archive')
    assert resp.status_code == 200
    assert resp.json()['archived'] is True
    assert ENERGY not in [p['id'] for p in projects(client)]
    assert [p['id'] for p in projects(client, archived='true')] == [ENERGY]
    assert client.post(f'/api/projects/{ENERGY}/archive').status_code == 200  # already archived

    moved = client.post('/api/tasks/tsk_01J9ZQ6/move', json={'project_id': ENERGY})
    assert moved.status_code == 409
    assert moved.json()['code'] == 'project_archived'


def test_unknown_projects_are_404(client: TestClient):
    assert client.get('/api/projects/prj_nope').status_code == 404
    assert client.patch('/api/projects/prj_nope', json={'name': 'x'}).status_code == 404
    assert client.post('/api/projects/prj_nope/archive').status_code == 404
    resp = client.post('/api/tasks/tsk_01J9ZQ6/move', json={'project_id': 'prj_nope'})
    assert resp.status_code == 404
    check('Error', resp.json())
    assert client.post('/api/tasks/tsk_nope/move', json={'project_id': ENERGY}).status_code == 404


# ---- moving a task ----------------------------------------------------------------------------


def test_moving_a_task_updates_both_projects(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        ws.receive_json()
        resp = client.post('/api/tasks/tsk_01J9ZQ6/move', json={'project_id': ENERGY})
        assert resp.status_code == 200
        frames = drain(ws, 5)
    check('Task', resp.json())
    assert resp.json()['project_id'] == ENERGY
    assert order(frames) == [
        ('task.moved', None),
        ('artifact.updated', 'board'),
        ('project.updated', 'inbox'),
        ('artifact.updated', 'board'),
        ('project.updated', 'energy'),
    ]
    assert frames[0]['data'] == {
        'task_id': 'tsk_01J9ZQ6',
        'from_project_id': INBOX,
        'to_project_id': ENERGY,
    }
    assert frames[2]['data']['counts']['needs_you'] == 0
    assert frames[4]['data']['counts']['needs_you'] == 1
    assert [
        t['id'] for t in client.get('/api/tasks', params={'project_id': ENERGY}).json()['items']
    ] == ['tsk_01J9ZQ6']
    assert section(board_of(client, INBOX), 'Waiting on you') == []
    assert len(section(board_of(client, ENERGY), 'Waiting on you')) == 1


def test_moving_a_task_to_its_own_project_changes_nothing(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        ws.receive_json()
        resp = client.post('/api/tasks/tsk_01J9ZQ6/move', json={'project_id': INBOX})
        assert resp.status_code == 200
        client.post(MAIN.replace('cnv_main_marcel', 'cnv_side_01J9ZR0'), json={'text': 'marker'})
        assert ws.receive_json()['type'] == 'message.created'  # nothing came before it


# ---- versions ---------------------------------------------------------------------------------


def test_versions_newest_first_and_old_content(client: TestClient):
    resp = client.get(f'/api/artifacts/{DOC}/versions')
    assert resp.status_code == 200
    versions = resp.json()
    for version in versions:
        check('ArtifactVersion', version)
    assert [v['version'] for v in versions] == [3, 2, 1]
    assert versions[0] == contract.example('ArtifactVersion')
    v1 = client.get(f'/api/artifacts/{DOC}/content', params={'version': 1})
    assert v1.status_code == 200
    assert 'Version 1' in v1.text
    assert 'Version 3' in client.get(f'/api/artifacts/{DOC}/content').text
    assert client.get(f'/api/artifacts/{DOC}/content', params={'version': 4}).status_code == 404
    assert client.get('/api/artifacts/art_nope/versions').status_code == 404
    pr = client.get('/api/artifacts/art_01J9ZT1/versions').json()
    assert [v['version'] for v in pr] == [1]
    assert pr[0]['url'] == client.get('/api/artifacts/art_01J9ZT1').json()['url']


@pytest.mark.parametrize('version', ['x', '0', '-1', '1.5'])
def test_a_bad_version_is_a_400(client: TestClient, version: str):
    resp = client.get(f'/api/artifacts/{DOC}/content', params={'version': version})
    assert resp.status_code == 400
    check('Error', resp.json())


# ---- query parameters -------------------------------------------------------------------------


@pytest.mark.parametrize(
    'url',
    [
        '/api/tasks?limit=ten',
        '/api/tasks?limit=0',
        '/api/tasks?limit=201',
        '/api/artifacts?limit=x',
        '/api/conversations/cnv_main_marcel/messages?limit=1.5',
        '/api/tasks/tsk_01J9ZQ6/events?after=x',
        '/api/tasks/tsk_01J9ZQ6/events?after=-1',
        '/api/tasks/tsk_01J9ZQ6/events?limit=x',
    ],
)
def test_a_bad_limit_or_after_is_a_400_not_a_500(client: TestClient, url: str):
    resp = client.get(url)
    assert resp.status_code == 400
    check('Error', resp.json())
    assert resp.json()['code'] == 'bad_request'


def test_list_filters(client: TestClient):
    inbox = client.get('/api/tasks', params={'project_id': INBOX}).json()['items']
    assert [t['id'] for t in inbox] == ['tsk_01J9ZQ6']
    assert (
        client.get('/api/tasks', params={'state': 'working'}).json()['items'][0]['id']
        == 'tsk_01J9ZQ5'
    )
    assert client.get('/api/tasks', params={'agent_id': 'agt_nobody'}).json()['items'] == []
    energy = client.get('/api/artifacts', params={'project_id': ENERGY}).json()['items']
    assert {a['id'] for a in energy} == {DOC, 'art_01JA0A2'}
    boards = client.get('/api/artifacts', params={'kind': 'board'}).json()['items']
    assert len(boards) == 4  # Marcel's three projects and Pip's Inbox
    assert all(a['kind'] == 'board' for a in boards)
    by_task = client.get('/api/artifacts', params={'task_id': 'tsk_01J9ZQ5'}).json()['items']
    assert [a['id'] for a in by_task] == ['art_01J9ZT1']
    stamps = [a['updated_at'] for a in client.get('/api/artifacts').json()['items']]
    assert stamps == sorted(stamps, reverse=True)  # newest change first


# ---- messages from a project room (B-32) ------------------------------------------------------


def test_a_message_from_a_project_room_is_tagged_and_files_the_task_there(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        ws.receive_json()
        resp = client.post(MAIN, json={'text': 'Fix the flaky login test', 'project_id': ENERGY})
        assert resp.status_code == 201
        assert resp.json()['project_id'] == ENERGY
        frames = frames_until(ws, ('agent.state', 'needs_you'))
    created = next(f for f in frames if f['type'] == 'task.created')
    assert created['data']['project_id'] == ENERGY


def test_a_message_naming_an_unknown_project_is_a_400(client: TestClient):
    resp = client.post(MAIN, json={'text': 'hi', 'project_id': 'prj_nope'})
    assert resp.status_code == 400
    check('Error', resp.json())


# ---- flows 4-6: map -> project room -> item v2 -------------------------------------------------

UNTIL_APPROVAL = [
    ('message.created', 'text'),
    ('agent.state', 'thinking'),
    ('task.created', None),
    ('artifact.updated', 'board'),  # inbox board: a thread in flight
    ('project.updated', 'inbox'),
    ('message.created', 'started'),
    ('task.event', 'state'),
    ('task.updated', 'working'),
    ('agent.state', 'working'),
    ('task.event', 'text'),
    ('task.event', 'tool_call'),
    ('task.event', 'artifact'),
    ('artifact.created', 'doc'),  # item v1
    ('message.created', 'artifact'),  # YOU GOT: ... (B-36)
    ('artifact.updated', 'board'),  # inbox board: a recent result
    ('project.updated', 'inbox'),
    ('task.event', 'permission'),
    ('approval.created', None),
    ('message.created', 'approval'),
    ('task.event', 'state'),
    ('task.updated', 'needs_you'),
    ('artifact.updated', 'board'),  # inbox board: waiting on you
    ('project.updated', 'inbox'),
    ('agent.state', 'needs_you'),
]
MOVE = [
    ('task.moved', None),
    ('artifact.updated', 'board'),
    ('project.updated', 'inbox'),
    ('artifact.updated', 'board'),
    ('project.updated', 'energy'),
]
AFTER_APPROVE = [
    ('approval.resolved', None),
    ('artifact.updated', 'board'),  # energy board: nothing waiting on you any more
    ('project.updated', 'energy'),
    ('task.event', 'state'),
    ('task.updated', 'working'),
    ('project.updated', 'energy'),  # counts: needs_you -> working
    ('agent.state', 'working'),
    ('task.event', 'progress'),
    ('task.event', 'artifact'),
    ('artifact.updated', 'doc'),  # item v2, no second "YOU GOT"
    ('artifact.updated', 'board'),
    ('project.updated', 'energy'),
    ('task.event', 'state'),
    ('task.updated', 'done'),
    ('artifact.updated', 'board'),  # energy board: no longer in flight
    ('project.updated', 'energy'),
    ('message.created', 'done'),
    ('agent.state', 'done'),
    ('agent.state', 'idle'),
]


def check_boards(client: TestClient, frames: list[dict[str, Any]]) -> None:
    """Every board version the stream announced is a valid dashboard."""
    for frame in frames:
        if frame['type'] == 'artifact.updated' and tag(frame) == 'board':
            artifact = frame['data']['artifact']
            assert frame['data']['version']['version'] == artifact['current_version']
            resp = client.get(
                f'/api/artifacts/{artifact["id"]}/content',
                params={'version': artifact['current_version']},
            )
            contract.validate_dashboard(resp.json())


def test_flows_4_to_6_map_project_room_item_v2(client: TestClient):
    # The map: three projects, inbox last.
    assert [p['slug'] for p in projects(client)][-1] == 'inbox'
    energy_board_before = client.get('/api/artifacts/art_01JA0A2').json()['current_version']

    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        ws.receive_json()
        client.post(MAIN, json={'text': 'Compare the energy offers and switch if cheaper'})
        first = frames_until(ws, ('agent.state', 'needs_you'))
        assert order(first) == UNTIL_APPROVAL
        task = first[2]['data']
        assert task['project_id'] == INBOX  # Marcel filed it in the inbox
        item = next(f for f in first if f['type'] == 'artifact.created')['data']
        got = next(f for f in first if order([f]) == [('message.created', 'artifact')])['data']
        assert (got['artifact_id'], got['project_id'], got['task_id']) == (
            item['id'],
            INBOX,
            task['id'],
        )
        assert item['current_version'] == 1

        # You move the thread from its card to the Energy project (B-31).
        resp = client.post(f'/api/tasks/{task["id"]}/move', json={'project_id': ENERGY})
        assert resp.status_code == 200
        moved = drain(ws, len(MOVE))
        assert order(moved) == MOVE
        assert moved[0]['data'] == {
            'task_id': task['id'],
            'from_project_id': INBOX,
            'to_project_id': ENERGY,
        }

        # The project room: its board, quests and items.
        room = client.get(f'/api/projects/{ENERGY}').json()
        assert room['counts']['needs_you'] == 1
        assert task['id'] in [
            t['id'] for t in client.get('/api/tasks', params={'project_id': ENERGY}).json()['items']
        ]
        room_items = client.get('/api/artifacts', params={'project_id': ENERGY}).json()['items']
        assert item['id'] in [a['id'] for a in room_items]  # the item followed its thread
        board = board_of(client, ENERGY)
        (approval,) = [a for a in client.get('/api/approvals').json() if a['task_id'] == task['id']]
        assert section(board, 'Waiting on you') == [approval['summary']]

        # Answering the approval changes the board's version; the item gets a v2.
        client.post(f'/api/approvals/{approval["id"]}', json={'decision': 'approve'})
        rest = frames_until(ws, ('agent.state', 'idle'))
        assert order(rest) == AFTER_APPROVE

    everything = [*first, *moved, *rest]
    assert [f['id'] for f in everything] == list(range(1, len(everything) + 1))
    check_boards(client, everything)

    board_version = rest[1]['data']['artifact']['current_version']
    assert board_version == energy_board_before + 2  # +1 for the move, +1 for the answer
    v2 = next(f for f in rest if order([f]) == [('artifact.updated', 'doc')])['data']
    assert v2['artifact']['id'] == item['id']
    assert v2['artifact']['current_version'] == 2
    assert v2['artifact']['project_id'] == ENERGY
    assert v2['version']['version'] == 2
    assert v2['version']['summary']

    versions = client.get(f'/api/artifacts/{item["id"]}/versions').json()
    assert [v['version'] for v in versions] == [2, 1]
    old = client.get(f'/api/artifacts/{item["id"]}/content', params={'version': 1}).text
    new = client.get(f'/api/artifacts/{item["id"]}/content').text
    assert old != new
    assert client.get(f'/api/artifacts/{item["id"]}').json() == v2['artifact']
    done = next(f for f in rest if order([f]) == [('message.created', 'done')])['data']['milestone']
    assert done['artifact_ids'] == [item['id']]
    assert section(board_of(client, ENERGY), 'Waiting on you') == []
    assert any('v2' in text for text in section(board_of(client, ENERGY), 'Recent results'))
    finished = client.get(f'/api/projects/{ENERGY}').json()
    assert finished['counts'] == {'working': 0, 'needs_you': 0, 'done_7d': 1}


def test_denying_the_items_run_stops_it_with_v1_only(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        ws.receive_json()
        client.post(MAIN, json={'text': 'Energy: compare the offers'})
        first = frames_until(ws, ('agent.state', 'needs_you'))
        task_id = first[2]['data']['id']
        (approval,) = [a for a in client.get('/api/approvals').json() if a['task_id'] == task_id]
        client.post(f'/api/approvals/{approval["id"]}', json={'decision': 'deny'})
        rest = frames_until(ws, ('agent.state', 'idle'))
    assert ('task.updated', 'stopped') in order(rest)
    assert not any(tag(f) == 'doc' for f in rest)
    item = next(f for f in first if f['type'] == 'artifact.created')['data']
    assert client.get(f'/api/artifacts/{item["id"]}').json()['current_version'] == 1


def test_board_content_is_json_text(client: TestClient):
    raw = client.get('/api/artifacts/art_01JA0A2/content').text
    assert json.loads(raw)['version'] == 1
