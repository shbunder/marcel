"""The app WebSocket: token check, fan-out to every device, replay with `?since=`, resync, order.

Every frame is checked against `contracts/events.schema.json`.
"""

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from sqlmodel import Session, select
from starlette.websockets import WebSocketDisconnect
from test_conversations import Phone, add_agent, add_phone, send

from marcel_hub.app import Settings, create_app
from marcel_hub.auth import tokens
from marcel_hub.models import AppEvent
from marcel_hub.ws import EventLog

CONTRACTS = Path(__file__).resolve().parents[2] / 'contracts'
EVENTS = json.loads((CONTRACTS / 'events.schema.json').read_text())
API = yaml.safe_load((CONTRACTS / 'app-api.yaml').read_text())
FRAME = Draft202012Validator(
    EVENTS,
    registry=Registry()
    .with_resource(EVENTS['$id'], Resource.from_contents(EVENTS))
    .with_resource(
        EVENTS['$id'].replace('events.schema.json', 'app-api.yaml'),
        Resource.from_contents(API | {'$schema': 'https://json-schema.org/draft/2020-12/schema'}),
    ),
)


def frame(ws: Any) -> dict[str, Any]:
    body = ws.receive_json()
    FRAME.validate(body)
    return body


def connect(client: TestClient, phone: Phone, since: int | str | None = None) -> Any:
    url = '/api/ws' if since is None else f'/api/ws?since={since}'
    return client.websocket_connect(url, headers=phone.headers)


def log(client: TestClient) -> EventLog:
    return client.app.state.events  # type: ignore[attr-defined]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db')


@pytest.fixture
def client(settings: Settings) -> Any:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def phone(client: TestClient) -> Phone:
    return add_phone(client)


@pytest.fixture
def main(client: TestClient) -> str:
    return add_agent(client)[1]


# ---- done when: fan-out and replay ------------------------------------------------------------


def test_two_devices_both_receive_message_created(
    client: TestClient, phone: Phone, main: str
) -> None:
    other = add_phone(client)
    with connect(client, phone) as ws1, connect(client, other) as ws2:
        assert frame(ws1)['type'] == 'hello'
        assert frame(ws2)['type'] == 'hello'

        msg = send(client, phone, main, 'Both phones see this')

        for ws in (ws1, ws2):
            event = frame(ws)
            assert event['type'] == 'message.created'
            assert event['id'] == 1
            assert event['data'] == msg


def test_a_reconnect_with_since_replays_the_missed_events(
    client: TestClient, phone: Phone, main: str
) -> None:
    with connect(client, phone) as ws:
        frame(ws)
        send(client, phone, main, 'seen live')
        last_seen = frame(ws)['id']

    missed = [send(client, phone, main, f'missed {i}') for i in range(3)]

    with connect(client, phone, since=last_seen) as ws:
        hello = frame(ws)
        assert hello == hello | {'id': 0, 'type': 'hello'}
        assert hello['data'] == {'last_event_id': 4, 'replayed': 3}
        replayed = [frame(ws) for _ in range(3)]
        assert [e['id'] for e in replayed] == [2, 3, 4]
        assert [e['data'] for e in replayed] == missed

        live = send(client, phone, main, 'live again')
        assert frame(ws) | {'at': 'x'} == {
            'id': 5,
            'type': 'message.created',
            'at': 'x',
            'data': live,
        }


def test_a_first_connect_replays_nothing(client: TestClient, phone: Phone, main: str) -> None:
    send(client, phone, main, 'before')
    with connect(client, phone) as ws:
        assert frame(ws)['data'] == {'last_event_id': 1, 'replayed': 0}
        send(client, phone, main, 'after')
        assert frame(ws)['id'] == 2


def test_since_equal_to_the_last_event_replays_nothing(
    client: TestClient, phone: Phone, main: str
) -> None:
    send(client, phone, main, 'one')
    with connect(client, phone, since=1) as ws:
        assert frame(ws)['data'] == {'last_event_id': 1, 'replayed': 0}


def test_a_new_side_thread_reaches_every_device(
    client: TestClient, phone: Phone, main: str
) -> None:
    parent = send(client, phone, main, 'question')
    with connect(client, add_phone(client), since=1) as ws:
        frame(ws)
        side = client.post(f'/api/messages/{parent["id"]}/thread', headers=phone.headers).json()
        event = frame(ws)
        assert event['type'] == 'conversation.created'
        assert event['data'] == side


# ---- control: the token check ------------------------------------------------------------------


@pytest.mark.parametrize(
    'headers', [{}, {'Authorization': 'Bearer mrc_wrong'}, {'Authorization': 'Basic x'}]
)
def test_a_socket_without_a_valid_token_is_refused(
    client: TestClient, headers: dict[str, str]
) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as refused,
        client.websocket_connect('/api/ws', headers=headers),
    ):
        pass  # pragma: no cover - the hub must not accept
    assert refused.value.code == 1008


def test_a_revoked_device_cannot_connect(client: TestClient, phone: Phone) -> None:
    other = add_phone(client)
    assert (
        client.delete(f'/api/devices/{phone.device_id}', headers=other.headers).status_code == 204
    )
    with pytest.raises(WebSocketDisconnect), connect(client, phone):
        pass  # pragma: no cover


def test_a_device_revoked_while_connected_is_cut_off_before_the_next_event(
    client: TestClient, phone: Phone, main: str
) -> None:
    other = add_phone(client)
    with connect(client, phone) as ws:
        frame(ws)
        client.delete(f'/api/devices/{phone.device_id}', headers=other.headers)
        send(client, other, main, 'not for the removed phone')
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008


def test_events_reach_only_the_users_own_devices(
    client: TestClient, phone: Phone, main: str
) -> None:
    stranger = add_phone(client, user_id='usr_other')
    _, their_main = add_agent(client, user_id='usr_other')
    with connect(client, stranger) as ws:
        frame(ws)
        send(client, phone, main, 'private')
        theirs = send(client, stranger, their_main, 'mine')
        event = frame(ws)
        assert event['data'] == theirs
        assert event['id'] == 1  # every user counts from 1


# ---- control: resync ------------------------------------------------------------------------


def test_since_older_than_the_hub_keeps_asks_for_a_resync(
    client: TestClient, phone: Phone, main: str
) -> None:
    log(client).keep = 3
    for i in range(5):
        send(client, phone, main, f'm{i}')

    with connect(client, phone, since=1) as ws:  # event 2 is gone
        assert frame(ws) | {'at': 'x'} == {
            'id': 0,
            'type': 'resync.required',
            'at': 'x',
            'data': {'last_event_id': 5},
        }
        send(client, phone, main, 'live')
        assert frame(ws)['id'] == 6

    with connect(client, phone, since=3) as ws:  # 4, 5 and 6 are still kept
        assert frame(ws)['data'] == {'last_event_id': 6, 'replayed': 3}
        assert [frame(ws)['id'] for _ in range(3)] == [4, 5, 6]


def test_only_the_latest_events_are_kept(client: TestClient, phone: Phone, main: str) -> None:
    log(client).keep = 3
    for i in range(6):
        send(client, phone, main, f'm{i}')
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        assert [e.id for e in s.exec(select(AppEvent)).all()] == [4, 5, 6]


@pytest.mark.parametrize('since', [9, 'abc', -1])
def test_a_since_the_hub_never_sent_asks_for_a_resync(
    client: TestClient, phone: Phone, main: str, since: int | str
) -> None:
    send(client, phone, main, 'one')
    with connect(client, phone, since=since) as ws:
        event = frame(ws)
        assert event['type'] == 'resync.required'
        assert event['data'] == {'last_event_id': 1}


def test_event_ids_and_replay_survive_a_hub_restart(settings: Settings) -> None:
    with TestClient(create_app(settings)) as first:
        phone = add_phone(first)
        main = add_agent(first)[1]
        send(first, phone, main, 'before the restart')
    with TestClient(create_app(settings)) as second:
        send(second, phone, main, 'after the restart')
        with connect(second, phone, since=0) as ws:
            assert frame(ws)['data'] == {'last_event_id': 2, 'replayed': 2}
            assert [frame(ws)['data']['text'] for _ in range(2)] == [
                'before the restart',
                'after the restart',
            ]


# ---- control: ordering ------------------------------------------------------------------------


def test_concurrent_sends_get_consecutive_ids_and_arrive_in_order(
    client: TestClient, phone: Phone, main: str
) -> None:
    with connect(client, phone) as ws:
        frame(ws)

        def burst(n: int) -> None:
            for i in range(5):
                send(client, phone, main, f'{n}-{i}')

        threads = [threading.Thread(target=burst, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        live = [frame(ws) for _ in range(30)]

    assert [e['id'] for e in live] == list(range(1, 31))
    with connect(client, phone, since=0) as ws:
        frame(ws)
        assert [frame(ws) for _ in range(30)] == live


# ---- the event log itself ---------------------------------------------------------------------


def test_closing_a_socket_stops_its_fan_out(client: TestClient, phone: Phone, main: str) -> None:
    with connect(client, phone) as ws:
        frame(ws)
        assert log(client).listeners(tokens.OWNER_USER_ID) == 1
    deadline = time.monotonic() + 2
    while log(client).listeners(tokens.OWNER_USER_ID) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert log(client).listeners(tokens.OWNER_USER_ID) == 0
    send(client, phone, main, 'nobody is listening')


def test_a_listener_whose_loop_has_closed_is_dropped(client: TestClient, main: str) -> None:
    events = log(client)
    loop = asyncio.new_event_loop()

    async def listen() -> None:
        events.subscribe('usr_x', None)

    loop.run_until_complete(listen())
    loop.close()
    assert events.listeners('usr_x') == 1
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        events.publish(s, 'usr_x', 'message.created', {})
    assert events.listeners('usr_x') == 0


def test_frames_from_the_app_are_ignored(client: TestClient, phone: Phone, main: str) -> None:
    with connect(client, phone) as ws:
        frame(ws)
        ws.send_text('anything')
        send(client, phone, main, 'still live')
        assert frame(ws)['type'] == 'message.created'
