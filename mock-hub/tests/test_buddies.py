"""Buddies (app-api 0.8.0: B-49, ADR-013), quoted replies, mentions and look changes (B-41, B-42,
B-44), driven in order over HTTP and the WebSocket. One person, several buddies: every phone sees
every buddy, and each phone shows one."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import AUTH, frames_until
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from marcel_mock_hub import contract

MAIN = '/api/conversations/cnv_main_marcel/messages'
PIP = 'agt_01JA3E2'
PIP_MAIN = '/api/conversations/cnv_main_pip/messages'
FIRST_PHONE = 'dev_01J9ZQ4K2B'
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


def as_phone(token: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {token}'}


def pair_phone(client: TestClient, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """A second phone, paired with a code from `POST /pairing-codes`."""
    issued = client.post('/api/pairing-codes', json=body or {})
    code = valid(issued, '/pairing-codes', 'post')['code']
    resp = client.post('/api/pair', json={'code': code, 'device_name': 'iPad'}, headers={})
    return valid(resp, '/pair', 'post')


def test_changing_marcels_look(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        assert ws.receive_json()['type'] == 'hello'
        resp = client.patch('/api/agents/agt_marcel', json={'name': 'Marcella', 'look': LOOK})
        assert valid(resp, '/agents/{agent_id}', 'patch')['name'] == 'Marcella'
        frame = frames_until(ws, ('agent.updated', None))[-1]
        assert frame['data']['agent']['look'] == LOOK
    me = client.get('/api/me').json()
    assert 'role' not in me  # app-api 0.8.0: one person, no roles
    assert (me['agents'][0]['name'], me['agents'][0]['look']) == ('Marcella', LOOK)
    assert me['agents'][0]['state']['kind'] == 'idle'


def test_onboarding_is_set_once(client: TestClient):
    assert 'onboarded_at' not in client.get(f'/api/agents/{PIP}').json()
    first = client.patch(f'/api/agents/{PIP}', json={'onboarded': True}).json()
    second = client.patch(f'/api/agents/{PIP}', json={'onboarded': True}).json()
    assert first['onboarded_at'] == second['onboarded_at']


def test_a_deprecated_palette_is_ignored_once_a_buddy_has_a_look(client: TestClient):
    agent = client.patch('/api/agents/agt_marcel', json={'palette': 'dusk'}).json()
    assert 'palette' not in agent or agent['palette'] != 'dusk'
    assert client.get('/api/agents/agt_nobody').status_code == 404
    assert client.patch('/api/agents/agt_nobody', json={'name': 'x'}).status_code == 404


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
    # Every buddy's things are found (ADR-013): Pip's Inbox is a project too.
    inboxes = client.get('/api/mentions', params={'q': 'inbox', 'kind': 'project'}).json()
    assert len(inboxes) == 2


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

    # A message to Pip may mention any buddy's things; a project room tag must be Pip's own.
    energy = {'kind': 'project', 'id': 'prj_01JA0A1', 'title': 'Energy contracts'}
    mentioned = client.post(PIP_MAIN, json={'text': 'Like #energy', 'mentions': [energy]})
    assert mentioned.status_code == 201
    filed = client.post(PIP_MAIN, json={'text': 'hi', 'project_id': 'prj_01JA0A1'})
    assert filed.status_code == 400


# ---- buddies (B-49) -------------------------------------------------------------------------


def test_two_buddies_each_with_a_main_conversation_and_an_inbox(client: TestClient):
    me = valid(client.get('/api/me'), '/me', 'get')
    assert [(a['name'], a['look']) for a in me['agents']] == [
        ('Marcel', {'animal': 'giraffe', 'color': 'rose', 'personality': 'professional'}),
        ('Pip', {'animal': 'fox', 'color': 'tangerine', 'personality': 'cheerful'}),
    ]
    assert me['device_id'] == FIRST_PHONE
    for agent in me['agents']:
        conversations = client.get('/api/conversations', params={'agent_id': agent['id']}).json()
        assert conversations[0]['id'] == agent['main_conversation_id']
        assert conversations[0]['kind'] == 'main'
        projects = client.get('/api/projects', params={'agent_id': agent['id']}).json()
        assert projects[-1]['slug'] == 'inbox'
    hello = valid(client.get(PIP_MAIN), '/conversations/{conversation_id}/messages', 'get')
    assert hello['items'][0]['agent_id'] == PIP
    assert client.get('/api/conversations', params={'agent_id': 'agt_nobody'}).json() == []
    assert client.get('/api/projects', params={'agent_id': 'agt_nobody'}).json() == []


def test_health_says_the_hub_runs_on_a_mac(client: TestClient):
    health = valid(client.get('/api/health', headers={}), '/health', 'get')
    assert health['os'] == 'macos'


def test_adding_a_buddy(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        assert ws.receive_json()['type'] == 'hello'
        resp = client.post('/api/agents', json={'name': 'Juno', 'look': LOOK})
        juno = valid(resp, '/agents', 'post')
        frames = frames_until(ws, ('agent.updated', None))
    assert (juno['name'], juno['look'], juno['animal']) == ('Juno', LOOK, 'panda')
    assert frames[-1]['data']['agent']['id'] == juno['id']
    assert frames[-1]['data']['avatar_etag'] == '"lk-panda-teal-cozy"'
    assert juno['id'] in [a['id'] for a in client.get('/api/me').json()['agents']]
    inbox = client.get('/api/projects', params={'agent_id': juno['id']}).json()
    assert [p['slug'] for p in inbox] == ['inbox']
    sent = client.post(
        f'/api/conversations/{juno["main_conversation_id"]}/messages', json={'text': 'hi Juno'}
    )
    assert sent.status_code == 201
    # No phone switches to a new buddy by itself.
    assert client.get('/api/devices').json()[0]['agent_id'] == 'agt_marcel'


def test_a_buddy_without_a_look_gets_one_from_its_name(client: TestClient):
    first = client.post('/api/agents', json={'name': 'Bo'}).json()
    again = client.post('/api/agents', json={'name': 'Bo'}).json()
    assert first['look'] == again['look'] and first['id'] != again['id']
    assert client.get(f'/api/agents/{first["id"]}/avatar').status_code == 200
    assert client.post('/api/agents', json={}).status_code == 400


def test_switching_the_buddy_a_phone_shows(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        assert ws.receive_json()['type'] == 'hello'
        resp = client.patch(f'/api/devices/{FIRST_PHONE}', json={'agent_id': PIP})
        device = valid(resp, '/devices/{device_id}', 'patch')
        frame = frames_until(ws, ('device.updated', None))[-1]
    assert (device['agent_id'], device['current']) == (PIP, True)
    assert frame['data']['agent_id'] == PIP
    assert client.get('/api/devices').json()[0]['agent_id'] == PIP


def test_switching_refuses_an_unknown_phone_or_buddy(client: TestClient):
    nobody = client.patch(f'/api/devices/{FIRST_PHONE}', json={'agent_id': 'agt_nobody'})
    assert valid(nobody, '/devices/{device_id}', 'patch')['code'] == 'agent_not_found'
    no_phone = client.patch('/api/devices/dev_nope', json={'agent_id': PIP})
    assert valid(no_phone, '/devices/{device_id}', 'patch')['code'] == 'device_not_found'
    assert client.get('/api/devices').json()[0]['agent_id'] == 'agt_marcel', 'nothing changed'


def test_any_phone_switches_any_phone(client: TestClient):
    ipad = pair_phone(client)
    resp = client.patch(f'/api/devices/{ipad["device_id"]}', json={'agent_id': PIP})
    assert resp.json()['current'] is False  # asked from the first phone, about the iPad
    shown = {d['id']: d for d in client.get('/api/devices', headers=as_phone(ipad['token'])).json()}
    assert shown[ipad['device_id']]['agent_id'] == PIP
    assert shown[ipad['device_id']]['current'] is True


def test_archiving_a_buddy(client: TestClient):
    ipad = pair_phone(client, {'agent_id': PIP})
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        assert ws.receive_json()['type'] == 'hello'
        resp = client.post(f'/api/agents/{PIP}/archive')
        pip = valid(resp, '/agents/{agent_id}/archive', 'post')
        frames = frames_until(ws, ('device.updated', None))
    assert 'archived_at' in pip
    assert [f['type'] for f in frames[-2:]] == ['agent.updated', 'device.updated']
    assert frames[-1]['data'] == {
        **frames[-1]['data'],
        'id': ipad['device_id'],
        'agent_id': 'agt_marcel',
    }
    assert [a['id'] for a in client.get('/api/me').json()['agents']] == ['agt_marcel']
    assert client.get(f'/api/agents/{PIP}').json()['archived_at'] == pip['archived_at']
    assert client.get(PIP_MAIN).status_code == 200, 'its chat stays readable'
    again = client.post(f'/api/agents/{PIP}/archive').json()
    assert again['archived_at'] == pip['archived_at']
    refused = client.patch(f'/api/devices/{FIRST_PHONE}', json={'agent_id': PIP})
    assert refused.json()['code'] == 'agent_not_found'


def test_the_last_buddy_stays(client: TestClient):
    client.post(f'/api/agents/{PIP}/archive')
    resp = client.post('/api/agents/agt_marcel/archive')
    assert valid(resp, '/agents/{agent_id}/archive', 'post')['code'] == 'last_buddy'
    assert 'archived_at' not in client.get('/api/agents/agt_marcel').json()
    assert client.post('/api/agents/agt_nobody/archive').status_code == 404


def test_a_pairing_code_starts_the_new_phone_on_its_buddy(client: TestClient):
    issued = valid(
        client.post('/api/pairing-codes', json={'agent_id': PIP}), '/pairing-codes', 'post'
    )
    assert issued['link'].endswith(f'&code={issued["code"]}&buddy={PIP}')
    resp = client.post(
        '/api/pair', json={'code': issued['code'], 'device_name': 'iPad'}, headers={}
    )
    phone = valid(resp, '/pair', 'post')
    me = client.get('/api/me', headers=as_phone(phone['token'])).json()
    assert me['device_id'] == phone['device_id']
    devices = {d['id']: d for d in client.get('/api/devices').json()}
    assert devices[phone['device_id']]['agent_id'] == PIP
    used = client.post('/api/pair', json={'code': issued['code'], 'device_name': 'x'}, headers={})
    assert used.status_code == 400


def test_a_pairing_code_without_a_buddy_starts_on_the_first(client: TestClient):
    issued = client.post('/api/pairing-codes').json()
    assert '&buddy=' not in issued['link']
    phone = client.post(
        '/api/pair', json={'code': issued['code'], 'device_name': 'iPad'}, headers={}
    ).json()
    devices = {d['id']: d for d in client.get('/api/devices').json()}
    assert devices[phone['device_id']]['agent_id'] == 'agt_marcel'
    unknown = client.post('/api/pairing-codes', json={'agent_id': 'agt_nobody'})
    assert valid(unknown, '/pairing-codes', 'post')['code'] == 'agent_not_found'


def test_a_code_for_a_buddy_archived_since_starts_on_the_first(client: TestClient):
    issued = client.post('/api/pairing-codes', json={'agent_id': PIP}).json()
    client.post(f'/api/agents/{PIP}/archive')
    phone = client.post(
        '/api/pair', json={'code': issued['code'], 'device_name': 'iPad'}, headers={}
    ).json()
    devices = {d['id']: d for d in client.get('/api/devices').json()}
    assert devices[phone['device_id']]['agent_id'] == 'agt_marcel'


RELAY = {
    'kind': 'relay',
    'relay_url': 'https://push.marcel-bot.com',
    'handle': 'A' * 22,
    'send_secret': 'b' * 43,
    'device_key': 'c' * 42 + 'A',
}


def test_registering_the_relay_push_target(client: TestClient):
    assert client.get('/api/devices').json()[0]['push'] == 'none'
    resp = client.put(f'/api/devices/{FIRST_PHONE}/push', json=RELAY)
    device = valid(resp, '/devices/{device_id}/push', 'put')
    assert device['push'] == 'relay'
    assert client.get('/api/devices').json()[0]['push'] == 'relay'
    hub = client.app.state.hub  # type: ignore[attr-defined]
    assert hub.buddies.push_targets[FIRST_PHONE] == RELAY  # stored, never shown again
    assert 'send_secret' not in str(client.get('/api/devices').json())
    missing = client.put(f'/api/devices/{FIRST_PHONE}/push', json={'kind': 'relay'})
    assert missing.status_code == 400
    assert client.put('/api/devices/dev_nope/push', json=RELAY).status_code == 404


def test_signing_out_a_paired_phone(client: TestClient):
    ipad, other = pair_phone(client), pair_phone(client)
    assert client.delete(f'/api/devices/{ipad["device_id"]}').status_code == 204
    assert client.get('/api/me', headers=as_phone(ipad['token'])).status_code == 401
    assert client.get('/api/me', headers=as_phone(other['token'])).status_code == 200
    assert ipad['device_id'] not in {d['id'] for d in client.get('/api/devices').json()}
    # A signed-out phone gets no events.
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect('/api/ws', headers=as_phone(ipad['token'])),
    ):
        pass
    assert client.delete(f'/api/devices/{FIRST_PHONE}').status_code == 204


def test_every_phone_gets_every_buddys_events(client: TestClient):
    ipad = pair_phone(client, {'agent_id': PIP})
    with client.websocket_connect('/api/ws', headers=as_phone(ipad['token'])) as ws:
        assert ws.receive_json()['type'] == 'hello'
        client.patch('/api/agents/agt_marcel', json={'look': LOOK})
        client.post(PIP_MAIN, json={'text': 'hi Pip'})
        frames = frames_until(ws, ('message.created', 'text'))
    assert [f['type'] for f in frames] == ['agent.updated', 'message.created']
    assert frames[0]['data']['agent']['id'] == 'agt_marcel'
    assert frames[1]['id'] == frames[0]['id'] + 1, 'no gaps: one person sees every buddy'
