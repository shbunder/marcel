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

        # 7. Emma finishes onboarding and changes her helper's look: `agent.updated`.
        client.patch(f'/api/agents/{PIP}', json={'look': LOOK, 'onboarded': True}, headers=emma)
        frame = frames_until(ws, ('agent.updated', None))[-1]
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
