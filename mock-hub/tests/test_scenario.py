"""The scripted scenario over HTTP and the WebSocket, with the order of events asserted."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import AUTH, frames_until, tag
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from marcel_mock_hub import contract

MAIN = '/api/conversations/cnv_main_marcel/messages'

UNTIL_APPROVAL = [
    ('message.created', 'text'),
    ('agent.state', 'thinking'),
    ('task.created', None),
    ('message.created', 'started'),
    ('task.event', 'state'),
    ('task.updated', 'working'),
    ('agent.state', 'working'),
    ('task.event', 'text'),
    ('task.event', 'tool_call'),
    ('task.event', 'diff'),
    ('task.event', 'permission'),
    ('approval.created', None),
    ('message.created', 'approval'),
    ('task.event', 'state'),
    ('task.updated', 'needs_you'),
    ('agent.state', 'needs_you'),
]
AFTER_APPROVE = [
    ('approval.resolved', None),
    ('task.event', 'state'),
    ('task.updated', 'working'),
    ('agent.state', 'working'),
    ('task.event', 'progress'),
    ('task.event', 'artifact'),
    ('artifact.created', 'pr'),
    ('task.event', 'state'),
    ('task.updated', 'done'),
    ('message.created', 'done'),
    ('agent.state', 'done'),
    ('agent.state', 'idle'),
]


def order(frames: list[dict[str, Any]]) -> list[tuple[str, str | None]]:
    return [(f['type'], tag(f)) for f in frames]


def ids(frames: list[dict[str, Any]]) -> list[int]:
    return [f['id'] for f in frames]


def send(
    client: TestClient, text: str = 'Fix the flaky login test', **extra: Any
) -> dict[str, Any]:
    resp = client.post(MAIN, json={'text': text, **extra})
    assert resp.status_code == 201
    return resp.json()


def test_the_full_scenario_in_order(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        hello = ws.receive_json()
        contract.validate_event(hello)
        assert hello['type'] == 'hello'
        assert hello['data'] == {'last_event_id': 0, 'replayed': 0}

        user = send(client)
        first = frames_until(ws, ('agent.state', 'needs_you'))
        assert order(first) == UNTIL_APPROVAL
        assert first[0]['data'] == user

        # The agent is waiting on a person: the card is open and the task needs them.
        (approval,) = client.get('/api/approvals').json()[-1:]
        assert approval['state'] == 'open'
        task = client.get(f'/api/tasks/{approval["task_id"]}').json()
        assert task['state'] == 'needs_you'
        assert task['approval_ids'] == [approval['id']]
        assert 'waiting_for' in task

        resp = client.post(f'/api/approvals/{approval["id"]}', json={'decision': 'approve'})
        assert resp.status_code == 200
        assert resp.json()['state'] == 'approved'
        rest = frames_until(ws, ('agent.state', 'idle'))
        assert order(rest) == AFTER_APPROVE

    # One sequence: ids rise by one across the whole run.
    everything = [*first, *rest]
    assert ids(everything) == list(range(1, len(everything) + 1))

    done = next(f for f in rest if f['type'] == 'message.created' and tag(f) == 'done')
    pr = next(f for f in rest if f['type'] == 'artifact.created')['data']
    assert done['data']['milestone']['artifact_ids'] == [pr['id']]
    finished = client.get(f'/api/tasks/{approval["task_id"]}').json()
    assert finished['state'] == 'done'
    assert finished['artifact_count'] == 1
    assert 'waiting_for' not in finished
    assert 'approval_ids' not in finished
    assert client.get(f'/api/artifacts/{pr["id"]}').json() == pr
    assert client.get('/api/me').json()['agents'][0]['state'] == {'kind': 'idle'}


def test_the_rest_api_shows_what_the_scenario_made(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        ws.receive_json()
        user = send(client)
        frames_until(ws, ('agent.state', 'needs_you'))
        assert client.get('/api/agents/agt_marcel').json()['state'] == {'kind': 'needs_you'}
        task_id = client.get('/api/approvals').json()[-1]['task_id']
        events = client.get(f'/api/tasks/{task_id}/events').json()
        assert [e['seq'] for e in events['items']] == list(range(1, len(events['items']) + 1))
        assert [e['type'] for e in events['items']] == [
            'state', 'text', 'tool_call', 'diff', 'permission', 'state'
        ]  # fmt: skip
        resumed = client.get(f'/api/tasks/{task_id}/events', params={'after': 4}).json()
        assert [e['seq'] for e in resumed['items']] == [5, 6]
    texts = [m['id'] for m in client.get(MAIN).json()['items']]
    assert user['id'] in texts


def test_denying_stops_the_task(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        ws.receive_json()
        send(client)
        frames_until(ws, ('agent.state', 'needs_you'))
        approval = client.get('/api/approvals').json()[-1]
        resp = client.post(f'/api/approvals/{approval["id"]}', json={'decision': 'deny'})
        assert resp.json()['state'] == 'denied'
        rest = frames_until(ws, ('agent.state', 'idle'))
    assert order(rest) == [
        ('approval.resolved', None),
        ('task.event', 'state'),
        ('task.updated', 'stopped'),
        ('message.created', 'stopped'),
        ('agent.state', 'done'),
        ('agent.state', 'idle'),
    ]
    assert not any(f['type'] == 'artifact.created' for f in rest)


def test_the_second_answer_is_a_409_with_the_approval_as_it_stands(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        ws.receive_json()
        send(client)
        frames_until(ws, ('agent.state', 'needs_you'))
        approval = client.get('/api/approvals').json()[-1]
        client.post(f'/api/approvals/{approval["id"]}', json={'decision': 'approve'})
        late = client.post(f'/api/approvals/{approval["id"]}', json={'decision': 'deny'})
        assert late.status_code == 409
        assert late.json()['state'] == 'approved'
        contract.validator('app-api.yaml#/components/schemas/Approval').validate(late.json())
        frames_until(ws, ('agent.state', 'idle'))


def test_a_message_while_busy_is_stored_and_starts_no_second_run(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        ws.receive_json()
        send(client)
        frames_until(ws, ('agent.state', 'needs_you'))
        extra = send(client, 'And add a test for the empty case.')
        frame = ws.receive_json()
        assert frame['type'] == 'message.created'
        assert frame['data'] == extra
        approval = client.get('/api/approvals').json()[-1]
        client.post(f'/api/approvals/{approval["id"]}', json={'decision': 'approve'})
        rest = frames_until(ws, ('agent.state', 'idle'))
    assert [f for f in rest if f['type'] == 'task.created'] == []
    created = [e for e in client.app.state.hub.events if e['type'] == 'task.created']  # type: ignore[attr-defined]
    assert len(created) == 1


def test_a_second_run_can_start_once_the_first_is_over(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        ws.receive_json()
        for _ in range(2):
            send(client)
            frames_until(ws, ('agent.state', 'needs_you'))
            approval = client.get('/api/approvals').json()[-1]
            client.post(f'/api/approvals/{approval["id"]}', json={'decision': 'approve'})
            frames_until(ws, ('agent.state', 'idle'))


def test_the_same_client_id_returns_the_same_message(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        ws.receive_json()
        first = send(client, client_id='abc')
        again = send(client, client_id='abc')
        assert again == first
        frames_until(ws, ('agent.state', 'needs_you'))


def test_a_message_in_a_side_thread_starts_no_run(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        ws.receive_json()
        resp = client.post(
            '/api/conversations/cnv_side_01J9ZR0/messages', json={'text': 'Use SQLAlchemy.'}
        )
        assert resp.status_code == 201
        assert ws.receive_json()['type'] == 'message.created'
        assert client.get('/api/approvals').json()[-1]['id'] == 'apr_01J9ZR2'


def test_replay_since(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        ws.receive_json()
        send(client)
        first = frames_until(ws, ('agent.state', 'needs_you'))

    with client.websocket_connect('/api/ws?since=3', headers=AUTH) as ws:
        hello = ws.receive_json()
        contract.validate_event(hello)
        assert hello['data'] == {'last_event_id': len(first), 'replayed': len(first) - 3}
        replay = [ws.receive_json() for _ in range(len(first) - 3)]
    assert ids(replay) == ids(first[3:])
    for frame in replay:
        contract.validate_event(frame)

    with client.websocket_connect(f'/api/ws?since={len(first)}', headers=AUTH) as ws:
        assert ws.receive_json()['data'] == {'last_event_id': len(first), 'replayed': 0}
    with client.websocket_connect('/api/ws?since=0', headers=AUTH) as ws:
        assert ws.receive_json()['data']['replayed'] == len(first)


def test_replay_then_live_has_no_gap_or_duplicate(client: TestClient):
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        ws.receive_json()
        send(client)
        first = frames_until(ws, ('agent.state', 'needs_you'))
        approval = client.get('/api/approvals').json()[-1]
    with client.websocket_connect('/api/ws?since=5', headers=AUTH) as ws:
        hello = ws.receive_json()
        replay = [ws.receive_json() for _ in range(hello['data']['replayed'])]
        client.post(f'/api/approvals/{approval["id"]}', json={'decision': 'approve'})
        live = frames_until(ws, ('agent.state', 'idle'))
    assert ids([*replay, *live]) == list(range(6, len(first) + len(live) + 1))


def test_a_since_from_the_future_asks_the_app_to_resync(client: TestClient):
    with client.websocket_connect('/api/ws?since=999', headers=AUTH) as ws:
        frame = ws.receive_json()
        contract.validate_event(frame)
        assert frame['type'] == 'resync.required'
        assert frame['data'] == {'last_event_id': 0}
        # Still live afterwards: the app re-fetches, then reconnects, but nothing is cut off.
        send(client)
        assert ws.receive_json()['type'] == 'message.created'


def test_the_stream_needs_a_token_and_a_numeric_since(client: TestClient):
    for url, headers in (('/api/ws', {'Authorization': ''}), ('/api/ws?since=x', AUTH)):
        with (
            pytest.raises(WebSocketDisconnect) as refused,
            client.websocket_connect(url, headers=headers),
        ):
            pass
        assert refused.value.code == 1008


def test_two_phones_see_the_same_frames(client: TestClient):
    with (
        client.websocket_connect('/api/ws', headers=AUTH) as one,
        client.websocket_connect('/api/ws', headers=AUTH) as two,
    ):
        one.receive_json(), two.receive_json()
        send(client)
        a = frames_until(one, ('agent.state', 'needs_you'))
        b = frames_until(two, ('agent.state', 'needs_you'))
    assert a == b


def test_every_task_event_matches_the_contract_shape(client: TestClient):
    """Task events are checked against TaskEvent, which pins `data` for each type to
    transcript.schema.json. A guessed shape fails here."""
    with client.websocket_connect('/api/ws', headers=AUTH) as ws:
        ws.receive_json()
        send(client)
        frames_until(ws, ('agent.state', 'needs_you'))
        approval = client.get('/api/approvals').json()[-1]
        client.post(f'/api/approvals/{approval["id"]}', json={'decision': 'approve'})
        frames_until(ws, ('agent.state', 'idle'))
    check = contract.validator('app-api.yaml#/components/schemas/TaskEvent')
    logged = client.get(f'/api/tasks/{approval["task_id"]}/events').json()['items']
    assert {e['type'] for e in logged} >= {'text', 'tool_call', 'diff', 'state', 'permission'}
    for event in logged:
        check.validate(event)


@pytest.mark.parametrize(
    'event',
    [
        {'type': 'text', 'data': {'text': 'no role'}},
        {'type': 'tool_call', 'data': {'tool': 'Bash', 'summary': 'no tool_use_id'}},
        {'type': 'diff', 'data': {'path': 'a.py', 'additions': 1, 'deletions': 0}},
    ],
)
def test_the_old_guessed_shapes_are_rejected(event: dict[str, Any]):
    full = {'task_id': 'tsk_x', 'seq': 1, 'at': '2026-10-04T18:06:39Z', **event}
    with pytest.raises(Exception, match='required|Additional'):
        contract.validator('app-api.yaml#/components/schemas/TaskEvent').validate(full)
