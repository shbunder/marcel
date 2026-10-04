"""Every response the mock gives validates against the contract, for every operation."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from marcel_mock_hub import contract

PATH_PARAMS = {
    'agent_id': 'agt_marcel',
    'device_id': 'dev_01J9ZQ4K2B',
    'conversation_id': 'cnv_main_marcel',
    'message_id': 'msg_01J9ZQZ8',
    'task_id': 'tsk_01J9ZQ6',
    'artifact_id': 'art_01J9ZT2',
    'approval_id': 'apr_01J9ZR2',
    'schedule_id': 'sch_digest_morning',
    'path': 'MEMORY.md',
}
OPERATIONS = [(m, p, op) for m, p, op in contract.operations() if p != '/ws']
IDS = [op['operationId'] for _, _, op in OPERATIONS]


def url(path: str) -> str:
    for name, value in PATH_PARAMS.items():
        path = path.replace('{' + name + '}', value)
    return '/api' + path


def request_body(path: str, method: str) -> Any:
    pointer = contract.request_pointer(path, method)
    if pointer is None:
        return None
    schema = contract.load_api()['paths'][path][method]['requestBody']['content'][
        'application/json'
    ]['schema']
    if '$ref' in schema:
        return contract.example(contract.schema_name(schema['$ref']))
    return schema['example']


def call(client: TestClient, method: str, path: str, **kwargs: Any):
    body = request_body(path, method)
    if body is not None:
        kwargs.setdefault('json', body)
    return client.request(method.upper(), url(path), **kwargs)


def assert_documented(resp: Any, path: str, method: str) -> None:
    documented = contract.load_api()['paths'][path][method]['responses']
    assert str(resp.status_code) in documented, f'{resp.status_code} is not documented'
    pointer = contract.response_pointer(path, method, resp.status_code)
    if pointer is not None:
        contract.validator(pointer).validate(resp.json())


def test_the_contract_was_read():
    assert len(OPERATIONS) >= 30


@pytest.mark.parametrize(('method', 'path', 'op'), OPERATIONS, ids=IDS)
def test_operation_is_served_and_validates(client: TestClient, method: str, path: str, op: Any):
    resp = call(client, method, path)
    success = [int(c) for c in op['responses'] if c.startswith('2')]
    assert resp.status_code in success
    assert_documented(resp, path, method)


@pytest.mark.parametrize(('method', 'path', 'op'), OPERATIONS, ids=IDS)
def test_token_is_required_except_where_the_contract_says_not(
    client: TestClient, method: str, path: str, op: Any
):
    resp = call(client, method, path, headers={'Authorization': ''})
    if op.get('security') == []:
        assert resp.status_code != 401
    else:
        assert resp.status_code == 401
        assert_documented(resp, path, method)


@pytest.mark.parametrize(
    ('method', 'path', 'op'),
    [o for o in OPERATIONS if contract.request_pointer(o[1], o[0])],
    ids=[o[2]['operationId'] for o in OPERATIONS if contract.request_pointer(o[1], o[0])],
)
def test_a_bad_body_is_a_400_in_the_error_shape(
    client: TestClient, method: str, path: str, op: Any
):
    resp = call(client, method, path, json={'nonsense': True})
    assert resp.status_code == 400
    # steerTask and handBackTask take a body but the contract lists no 400 for them (reported in
    # the PR); the generic BadRequest shape still applies.
    contract.validator('app-api.yaml#/components/schemas/Error').validate(resp.json())
    assert 'not valid' in resp.json()['message']


def test_a_body_that_is_not_json_is_a_400(client: TestClient):
    resp = client.post('/api/pair', content=b'not json')
    assert resp.status_code == 400
    assert 'not JSON' in resp.json()['message']


def test_any_pairing_code_works(client: TestClient):
    resp = client.post(
        '/api/pair', json={'code': 'ZZZZZZ', 'device_name': 'Test phone'}, headers={}
    )
    assert resp.status_code == 201
    assert resp.json()['token']


def test_unknown_ids_are_404_in_the_error_shape(client: TestClient):
    for path in (
        '/agents/nobody',
        '/tasks/tsk_nope',
        '/tasks/tsk_nope/events',
        '/tasks/tsk_nope/stop',
        '/artifacts/art_nope',
        '/artifacts/art_nope/content',
        '/conversations/cnv_nope/messages',
    ):
        method = 'post' if path.endswith('/stop') else 'get'
        resp = client.request(method.upper(), '/api' + path)
        assert resp.status_code == 404, path
        contract.validator('app-api.yaml#/components/schemas/Error').validate(resp.json())
    resp = client.post('/api/conversations/cnv_nope/messages', json={'text': 'hi'})
    assert resp.status_code == 404
    assert client.post('/api/approvals/apr_nope', json={'decision': 'approve'}).status_code == 404
    assert client.patch('/api/agents/nobody', json={'palette': 'dusk'}).status_code == 404


def test_artifact_content_is_the_documents_text(client: TestClient):
    resp = client.get('/api/artifacts/art_01J9ZT2/content')
    assert resp.headers['content-type'].startswith('text/markdown')
    assert 'Weekly review' in resp.text
    assert client.get('/api/artifacts/art_01J9ZT1/content').status_code == 404


def test_pagination_limit_and_task_event_paging(client: TestClient):
    assert client.get('/api/tasks', params={'limit': 1}).json()['items'][0]['id']
    page = client.get('/api/tasks/tsk_01J9ZQ6/events', params={'after': 1}).json()
    assert page == {'items': [], 'has_more': False}
    assert (
        client.get('/api/tasks/tsk_01J9ZQ6/events', params={'limit': 1}).json()['has_more'] is False
    )


def test_step_delay_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    from marcel_mock_hub.app import create_app

    monkeypatch.setenv('MOCK_DELAY', '0.25')
    assert create_app().state.hub.step_delay == 0.25
    monkeypatch.delenv('MOCK_DELAY')
    assert create_app().state.hub.step_delay == 1.0
