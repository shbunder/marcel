"""People, pairing a member's phone, owner-only routes, quoted replies, mentions and look changes
(contract 0.7: B-37, B-41, B-42, B-44), driven in order over HTTP and the WebSocket."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import AUTH, frames_until
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from marcel_mock_hub import contract

MAIN = '/api/conversations/cnv_main_marcel/messages'
EMMA = 'usr_01JA3E1'
PIP = 'agt_01JA3E2'
LOOK = {'animal': 'panda', 'color': 'teal', 'personality': 'cozy'}


def valid(resp: Any, path: str, method: str) -> Any:
    """The response is documented for this operation and matches its schema."""
    documented = contract.load_api()['paths'][path][method]['responses']
    assert str(resp.status_code) in documented, f'{resp.status_code} is not documented'
    pointer = contract.response_pointer(path, method, resp.status_code)
    if pointer is not None:
        contract.validator(pointer).validate(resp.json())
        return resp.json()
    return None


def as_member(token: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {token}'}


def pair_emma(client: TestClient) -> str:
    code = client.post(f'/api/users/{EMMA}/pairing-codes').json()['code']
    resp = client.post('/api/pair', json={'code': code, 'device_name': "Emma's iPhone"}, headers={})
    return resp.json()['token']


def test_the_people_steps_in_order(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        assert ws.receive_json()['type'] == 'hello'

        # 1. The owner's People screen: the owner first, then Emma.
        users = valid(client.get('/api/users'), '/users', 'get')
        assert [(u['name'], u['role']) for u in users] == [('Shaun', 'owner'), ('Emma', 'member')]
        assert users[1]['agent']['name'] == 'Pip'

        # 2. The owner adds a member; without a look the helper gets the example's.
        added = valid(
            client.post('/api/users', json={'name': 'Lou', 'agent': {'name': 'Bo'}}),
            '/users',
            'post',
        )
        assert added['role'] == 'member' and added['agent']['look']['animal'] == 'fox'
        names = [u['name'] for u in client.get('/api/users').json()]
        assert names == ['Shaun', 'Emma', 'Lou']

        # 3. A pairing code for Emma's phone, and the phone pairs to her.
        path = '/users/{user_id}/pairing-codes'
        issued = valid(client.post(f'/api/users/{EMMA}/pairing-codes'), path, 'post')
        assert issued['code'] in issued['link']
        paired = valid(
            client.post(
                '/api/pair',
                json={'code': issued['code'], 'device_name': "Emma's iPhone"},
                headers={},
            ),
            '/pair',
            'post',
        )
        assert paired['user_id'] == EMMA
        emma = as_member(paired['token'])

        # 4. The code works once.
        again = client.post(
            '/api/pair', json={'code': issued['code'], 'device_name': 'Another phone'}, headers={}
        )
        assert valid(again, '/pair', 'post')['code'] == 'bad_request'

        # 5. Emma sees her own helper and nothing of the owner's.
        me = valid(client.get('/api/me', headers=emma), '/me', 'get')
        assert (me['user_id'], me['role'], me['device_id']) == (EMMA, 'member', paired['device_id'])
        assert [a['id'] for a in me['agents']] == [PIP]
        assert client.get('/api/agents/agt_marcel', headers=emma).status_code == 404
        assert client.get('/api/projects', headers=emma).json() == []
        assert client.get('/api/tasks', headers=emma).json()['items'] == []
        assert client.get('/api/tasks/tsk_01J9ZQ6', headers=emma).status_code == 404
        conversations = client.get('/api/conversations', headers=emma).json()
        assert [c['agent_id'] for c in conversations] == [PIP]
        assert client.get(MAIN, headers=emma).status_code == 404

        # 6. A member gets 403 on every owner-only route, with a plain message.
        for method, url, spec in (
            ('get', '/api/users', '/users'),
            ('post', '/api/users', '/users'),
            ('delete', f'/api/users/{EMMA}', '/users/{user_id}'),
            ('post', f'/api/users/{EMMA}/pairing-codes', path),
        ):
            body = {'name': 'X', 'agent': {'name': 'Y'}} if spec == '/users' else None
            resp = client.request(method.upper(), url, headers=emma, json=body)
            assert resp.status_code == 403, url
            error = valid(resp, spec, method)
            assert error['code'] == 'owner_only'
            assert 'Ask Shaun' in error['message']

        # 7. Emma finishes onboarding and changes her helper's look: `agent.updated`, on her
        # own phone's stream only.
        with client.websocket_connect('/api/ws', headers=emma) as emma_ws:
            assert emma_ws.receive_json()['type'] == 'hello'
            client.patch(f'/api/agents/{PIP}', json={'look': LOOK, 'onboarded': True}, headers=emma)
            frame = frames_until(emma_ws, ('agent.updated', None))[-1]
        agent = frame['data']['agent']
        assert (agent['id'], agent['animal'], agent['look']) == (PIP, 'panda', LOOK)
        assert frame['data']['avatar_etag'] == '"lk-panda-teal-cozy"'
        avatar = client.get(f'/api/agents/{PIP}/avatar', headers=emma)
        assert avatar.headers['etag'] == frame['data']['avatar_etag']
        assert client.get('/api/users').json()[1]['agent']['look'] == LOOK

        # 8. The owner removes Emma: her phone is signed out at once.
        assert client.delete(f'/api/users/{EMMA}').status_code == 204
        assert client.get('/api/me', headers=emma).status_code == 401
        with pytest.raises(WebSocketDisconnect), client.websocket_connect('/api/ws', headers=emma):
            pass
        assert [u['name'] for u in client.get('/api/users').json()] == ['Shaun', 'Lou']


def test_the_owner_cannot_be_removed_and_unknown_people_are_404(client: TestClient):
    owner = client.delete('/api/users/usr_owner')
    assert valid(owner, '/users/{user_id}', 'delete')['code'] == 'cannot_remove_owner'
    gone = client.delete('/api/users/usr_nobody')
    assert valid(gone, '/users/{user_id}', 'delete')['code'] == 'user_not_found'
    code = client.post('/api/users/usr_nobody/pairing-codes')
    assert valid(code, '/users/{user_id}/pairing-codes', 'post')['code'] == 'user_not_found'


def test_a_removed_persons_code_no_longer_pairs(client: TestClient):
    code = client.post(f'/api/users/{EMMA}/pairing-codes').json()['code']
    client.delete(f'/api/users/{EMMA}')
    resp = client.post('/api/pair', json={'code': code, 'device_name': 'x phone'}, headers={})
    assert resp.status_code == 400
    assert 'removed' in resp.json()['message']


def test_the_owner_changes_marcels_look(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        assert ws.receive_json()['type'] == 'hello'
        resp = client.patch('/api/agents/agt_marcel', json={'name': 'Marcella', 'look': LOOK})
        assert valid(resp, '/agents/{agent_id}', 'patch')['name'] == 'Marcella'
        frame = frames_until(ws, ('agent.updated', None))[-1]
        assert frame['data']['agent']['look'] == LOOK
    me = client.get('/api/me').json()
    assert (me['role'], me['agents'][0]['name'], me['agents'][0]['look']) == (
        'owner',
        'Marcella',
        LOOK,
    )
    assert me['agents'][0]['state']['kind'] == 'idle'


def test_onboarding_is_set_once(client: TestClient):
    emma = as_member(pair_emma(client))
    before = client.get('/api/me', headers=emma).json()['agents'][0]
    assert 'onboarded_at' not in before, 'Emma has not met her helper yet'
    first = client.patch(f'/api/agents/{PIP}', json={'onboarded': True}, headers=emma).json()
    second = client.patch(f'/api/agents/{PIP}', json={'onboarded': True}, headers=emma).json()
    assert first['onboarded_at'] == second['onboarded_at']
    renamed = client.patch(f'/api/agents/{PIP}', json={'palette': 'dusk'}, headers=emma).json()
    assert 'palette' not in renamed  # ignored once the agent has a look


def test_a_deprecated_palette_still_works_without_a_look(client: TestClient):
    agent = client.patch('/api/agents/agt_marcel', json={'palette': 'dusk'}).json()
    assert agent['palette'] == 'dusk'
    assert 'avatar_etag' not in client.get('/api/agents/agt_marcel/avatar').headers.get('etag', '')
    assert client.get(f'/api/agents/{PIP}/avatar').status_code == 404


def test_a_result_quotes_the_message_that_started_it(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        assert ws.receive_json()['type'] == 'hello'
        asked = client.post(MAIN, json={'text': 'Fix the flaky login test'}).json()
        before = frames_until(ws, ('agent.state', 'needs_you'))

        # Something else is said while the run waits; it starts nothing new.
        later = client.post(MAIN, json={'text': 'And the signup test too, after that.'}).json()
        assert frames_until(ws, ('message.created', 'text'))[-1]['data'] == later

        approval = next(
            a for a in client.get('/api/approvals').json() if a['id'].startswith('apr_mock')
        )
        client.post(f'/api/approvals/{approval["id"]}', json={'decision': 'approve'})
        after = frames_until(ws, ('agent.state', 'idle'))

    answers = [
        f['data']
        for f in before + after
        if f['type'] == 'message.created' and f['data']['role'] == 'agent'
    ]
    assert [m['kind'] for m in answers] == ['milestone', 'approval', 'artifact', 'milestone']
    assert {m['reply_to_message_id'] for m in answers} == {asked['id']}
    assert {m['agent_id'] for m in answers} == {'agt_marcel'}  # who wrote it (app-api 0.7.2)
    assert 'agent_id' not in asked  # the person's own message names no agent
    assert later['id'] not in {m['reply_to_message_id'] for m in answers}
    done = answers[-1]
    assert done['milestone']['event'] == 'done'
    # It answers an older message than the person's latest, so the app shows a quote.
    history = [m['id'] for m in client.get(MAIN, params={'limit': 200}).json()['items']]
    assert history.index(asked['id']) < history.index(later['id']) < history.index(done['id'])


def test_mention_search_newest_first_and_by_kind(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        assert ws.receive_json()['type'] == 'hello'
        client.post(MAIN, json={'text': 'Fix the flaky login test'})
        frames_until(ws, ('agent.state', 'needs_you'))

    found = valid(client.get('/api/mentions', params={'q': 'LOGIN'}), '/mentions', 'get')
    assert found[0]['kind'] == 'task' and found[0]['id'].startswith('tsk_mock')
    assert all('login' in m['title'].lower() for m in found)
    projects = client.get('/api/mentions', params={'q': 'energy', 'kind': 'project'}).json()
    assert projects == [{'kind': 'project', 'id': 'prj_01JA0A1', 'title': 'Energy contracts'}]
    newest = client.get('/api/mentions').json()
    assert 0 < len(newest) <= 20
    assert all(m['kind'] != 'artifact' or 'board' not in m['title'] for m in newest)
    assert client.get('/api/mentions', params={'q': 'x' * 101}).status_code == 400
    assert client.get('/api/mentions', params={'kind': 'person'}).status_code == 400
    emma = as_member(pair_emma(client))
    assert client.get('/api/mentions', headers=emma).json() == []


def test_a_message_with_mentions(client: TestClient):
    project = {'kind': 'project', 'id': 'prj_01JA0A1', 'title': 'whatever the app had'}
    task = {'kind': 'task', 'id': 'tsk_01J9ZQ6', 'title': 'old title'}
    resp = client.post(
        '/api/conversations/cnv_side_01J9ZR0/messages',
        json={'text': 'See #energy and @the login fix', 'mentions': [project, task]},
    )
    message = valid(resp, '/conversations/{conversation_id}/messages', 'post')
    assert message['project_id'] == 'prj_01JA0A1'  # a project mention files the message
    titles = {m['id']: m['title'] for m in message['mentions']}
    assert titles['prj_01JA0A1'] == 'Energy contracts'
    assert titles['tsk_01J9ZQ6'] == client.get('/api/tasks/tsk_01J9ZQ6').json()['title']


def test_a_mention_that_is_not_yours_stores_nothing(client: TestClient):
    count = len(client.get(MAIN, params={'limit': 200}).json()['items'])
    unknown = {'kind': 'artifact', 'id': 'art_nope', 'title': 'Nothing'}
    resp = client.post(MAIN, json={'text': 'Look at @this', 'mentions': [unknown]})
    error = valid(resp, '/conversations/{conversation_id}/messages', 'post')
    assert error['code'] == 'mention_not_found'
    assert len(client.get(MAIN, params={'limit': 200}).json()['items']) == count

    emma = as_member(pair_emma(client))
    pip = client.get('/api/me', headers=emma).json()['agents'][0]['main_conversation_id']
    owners = {'kind': 'project', 'id': 'prj_01JA0A1', 'title': 'Energy contracts'}
    resp = client.post(
        f'/api/conversations/{pip}/messages',
        json={'text': 'hi', 'mentions': [owners]},
        headers=emma,
    )
    assert resp.status_code == 404
    # Nor can she file a message under the owner's project.
    resp = client.post(
        f'/api/conversations/{pip}/messages',
        json={'text': 'hi', 'project_id': 'prj_01JA0A1'},
        headers=emma,
    )
    assert resp.status_code == 400


# ---- each phone gets only its own person's things (contract 0.7: "Each person sees only their
# own agents and everything under them") ----------------------------------------------------


def test_each_phone_gets_only_its_own_persons_events(client: TestClient):
    emma = as_member(pair_emma(client))
    with (
        client.websocket_connect('/api/ws', headers=AUTH) as owner_ws,
        client.websocket_connect('/api/ws', headers=emma) as emma_ws,
    ):
        assert owner_ws.receive_json()['type'] == 'hello'
        assert emma_ws.receive_json()['type'] == 'hello'
        # Emma's look change, then the owner's run up to its approval card.
        client.patch(f'/api/agents/{PIP}', json={'look': LOOK}, headers=emma)
        client.post(MAIN, json={'text': 'Fix the flaky login test'})
        owner_frames = frames_until(owner_ws, ('agent.state', 'needs_you'))
        # A marker on Emma's side: her own message, after everything of the owner's.
        pip = client.get('/api/me', headers=emma).json()['agents'][0]['main_conversation_id']
        client.post(f'/api/conversations/{pip}/messages', json={'text': 'hi Pip'}, headers=emma)
        emma_frames = frames_until(emma_ws, ('message.created', 'text'))

    assert [f['type'] for f in emma_frames] == ['agent.updated', 'message.created']
    assert emma_frames[1]['data']['conversation_id'] == pip
    assert 'agent.updated' not in [f['type'] for f in owner_frames]
    assert all(f['data'].get('agent_id', 'agt_marcel') == 'agt_marcel' for f in owner_frames)
    # Ids stay global, so Emma's phone sees a gap where the owner's events were.
    assert emma_frames[1]['id'] - emma_frames[0]['id'] > 1


def test_a_replay_gives_only_the_persons_own_events(client: TestClient):
    emma = as_member(pair_emma(client))
    client.patch(f'/api/agents/{PIP}', json={'look': LOOK}, headers=emma)
    client.post('/api/projects', json={'agent_id': 'agt_marcel', 'name': 'Garden'})
    with client.websocket_connect('/api/ws?since=0', headers=emma) as ws:
        hello = ws.receive_json()
        assert hello['data']['replayed'] == 1
        assert ws.receive_json()['type'] == 'agent.updated'
    with client.websocket_connect('/api/ws?since=0', headers=AUTH) as ws:
        hello = ws.receive_json()
        replayed = [ws.receive_json() for _ in range(hello['data']['replayed'])]
    assert [f['type'] for f in replayed] == ['artifact.created', 'project.created']


def test_actions_by_id_on_someone_elses_things_are_404(client: TestClient):
    hub = client.app.state.hub  # type: ignore[attr-defined]
    emma = as_member(pair_emma(client))
    owners_card = {**hub.approvals['apr_01J9ZR2'], 'id': 'apr_owner_self', 'tool': 'self_change'}
    hub.approvals['apr_owner_self'] = {**owners_card, 'artifact_id': 'art_01J9ZT1'}
    task, art, project = 'tsk_01J9ZQ6', 'art_01JA0B1', 'prj_01JA0A1'
    for method, url, body in (
        ('POST', f'/api/tasks/{task}/messages', {'text': 'Also this.'}),
        ('POST', f'/api/tasks/{task}/stop', None),
        ('POST', f'/api/tasks/{task}/archive', None),
        ('POST', f'/api/tasks/{task}/handback', {'note': 'Over to you.'}),
        ('POST', f'/api/tasks/{task}/move', {'project_id': 'prj_inbox'}),
        ('GET', f'/api/tasks/{task}/events', None),
        ('POST', '/api/approvals/apr_01J9ZR2', {'decision': 'approve'}),
        ('POST', '/api/approvals/apr_owner_self', {'decision': 'approve'}),
        ('POST', '/api/projects', {'agent_id': 'agt_marcel', 'name': 'Not mine'}),
        ('GET', f'/api/artifacts/{art}/content', None),
        ('GET', f'/api/artifacts/{art}/versions', None),
        ('PATCH', f'/api/projects/{project}', {'goal': 'Mine now.'}),
        ('POST', f'/api/projects/{project}/archive', None),
    ):
        resp = client.request(method, url, json=body, headers=emma)
        assert resp.status_code == 404, url
        contract.validator('app-api.yaml#/components/schemas/Error').validate(resp.json())
    # Nothing changed: the owner still sees the approval open and the task where it was.
    assert any(a['id'] == 'apr_01J9ZR2' for a in client.get('/api/approvals').json())
    assert client.get(f'/api/tasks/{task}').json()['project_id'] == 'prj_inbox'
    assert [p['name'] for p in client.get('/api/projects').json()].count('Not mine') == 0


def test_a_task_moves_only_into_the_callers_own_project(client: TestClient):
    hub = client.app.state.hub  # type: ignore[attr-defined]
    emma = as_member(pair_emma(client))
    hub.tasks['tsk_emma'] = {**hub.tasks['tsk_01J9ZQ6'], 'id': 'tsk_emma', 'agent_id': PIP}
    resp = client.post('/api/tasks/tsk_emma/move', json={'project_id': 'prj_01JA0A1'}, headers=emma)
    assert resp.status_code == 404
    assert 'project' in resp.json()['message']


def test_a_self_change_card_is_answered_by_the_owner_only(client: TestClient):
    hub = client.app.state.hub  # type: ignore[attr-defined]
    emma = as_member(pair_emma(client))
    # A self-change card on a task of Emma's own helper: still the owner's to answer.
    hub.tasks['tsk_emma'] = {**hub.tasks['tsk_01J9ZQ6'], 'id': 'tsk_emma', 'agent_id': PIP}
    card = {**hub.approvals['apr_01J9ZR2'], 'id': 'apr_self', 'tool': 'self_change'}
    card.update(task_id='tsk_emma', artifact_id='art_01J9ZT1')  # the PR it is about (B-24)
    hub.approvals['apr_self'] = card
    resp = client.post('/api/approvals/apr_self', json={'decision': 'approve'}, headers=emma)
    error = valid(resp, '/approvals/{approval_id}', 'post')
    assert (resp.status_code, error['code']) == (403, 'owner_only')
    assert 'Ask Shaun' in error['message']
    assert hub.approvals['apr_self']['state'] == 'open'
    resp = client.post('/api/approvals/apr_self', json={'decision': 'approve'})
    assert valid(resp, '/approvals/{approval_id}', 'post')['state'] == 'approved'


def test_signing_a_phone_out_forgets_its_token(client: TestClient):
    """S-17.5's "Sign out of this phone": `DELETE /devices/{own id}` and the token is refused."""
    token = pair_emma(client)
    emma = as_member(token)
    device = client.get('/api/me', headers=emma).json()['device_id']
    other = as_member(pair_emma(client))
    other_device = client.get('/api/me', headers=other).json()['device_id']
    assert client.delete(f'/api/devices/{device}', headers=emma).status_code == 204
    assert client.get('/api/me', headers=emma).status_code == 401
    assert client.get('/api/me', headers=other).status_code == 200, "Emma's other phone stays"
    client.delete(f'/api/devices/{other_device}', headers=AUTH)
    assert client.get('/api/me', headers=other).status_code == 200, (
        "the owner's phone signs out only itself"
    )
