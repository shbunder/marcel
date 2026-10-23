"""Pairing a phone and the device-token check (B-27)."""

import dataclasses
import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from sqlmodel import Session, select
from test_contract import RETIRED_FIELDS, served_contract
from test_tasks_fakes import validator

from marcel_hub.app import Settings, create_app
from marcel_hub.auth import current_device, tokens
from marcel_hub.conversations import ensure_main_conversation
from marcel_hub.models import Agent, AppEvent, Device, PairingCode, utcnow
from marcel_hub.users.store import ensure_owner_agent

CONTRACT = Path(__file__).resolve().parents[2] / 'contracts' / 'app-api.yaml'
SPEC = served_contract(yaml.safe_load(CONTRACT.read_text()))  # S-02.13: as the hub serves it
REGISTRY = Registry().with_resource(
    'urn:contract',
    Resource.from_contents(SPEC | {'$schema': 'https://json-schema.org/draft/2020-12/schema'}),
)


APP_EVENT = validator('events.schema.json')
PUBLIC_URL = 'https://marcel.example.ts.net'


def validate(schema: str, body: Any) -> None:
    Draft202012Validator(
        {'$ref': f'urn:contract#/components/schemas/{schema}'}, registry=REGISTRY
    ).validate(body)


def validate_list(schema: str, body: Any) -> None:
    Draft202012Validator(
        {'type': 'array', 'items': {'$ref': f'urn:contract#/components/schemas/{schema}'}},
        registry=REGISTRY,
    ).validate(body)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db', owner_name='Shaun',
                    public_url=PUBLIC_URL)  # fmt: skip


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def buddy(client: TestClient) -> str:
    """The first buddy, made as `marcel-hub setup` makes it; a phone paired after shows it."""
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        agent, _ = ensure_owner_agent(s, 'Marcel')
        return agent.id


def new_code(client: TestClient, **kw: Any) -> str:
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        return tokens.create_pairing_code(s, **kw)


def pair(client: TestClient, name: str = "Shaun's iPhone") -> dict[str, Any]:
    res = client.post('/api/pair', json={'code': new_code(client), 'device_name': name})
    assert res.status_code == 201, res.text
    return res.json()


def bearer(token: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {token}'}


# --- tokens -----------------------------------------------------------------------------------


def test_a_token_is_32_random_bytes_with_a_prefix() -> None:
    a, b = tokens.new_token(), tokens.new_token()
    assert a != b
    assert a.startswith('mrc_')
    assert len(bytes.fromhex(a[len('mrc_') :])) == 32


def test_only_the_sha256_of_the_token_is_stored(client: TestClient) -> None:
    body = pair(client)
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        device = s.get(Device, body['device_id'])
    assert device is not None
    assert device.token_hash == hashlib.sha256(body['token'].encode()).hexdigest()
    assert body['token'] not in device.token_hash


def test_pairing_codes_match_the_contract_and_are_random() -> None:
    codes = {tokens.new_pairing_code() for _ in range(50)}
    assert len(codes) == 50
    for code in codes:
        validate('PairingCode', tokens.pairing_url('https://marcel-bot.com/api', code))


def test_pairing_codes_are_stored_hashed(client: TestClient) -> None:
    code = new_code(client)
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        rows = s.exec(select(PairingCode)).all()
    assert [r.code_hash for r in rows] == [hashlib.sha256(code.encode()).hexdigest()]


# --- POST /pair -------------------------------------------------------------------------------


def test_pairing_gives_a_token_that_works(client: TestClient) -> None:
    body = pair(client)
    validate('PairResponse', body)
    assert body['user_id'] == tokens.OWNER_USER_ID
    me = client.get('/api/me', headers=bearer(body['token']))
    assert me.status_code == 200
    assert me.json()['device_id'] == body['device_id']


def test_a_code_works_only_once(client: TestClient) -> None:
    code = new_code(client)
    first = client.post('/api/pair', json={'code': code, 'device_name': 'one'})
    second = client.post('/api/pair', json={'code': code, 'device_name': 'two'})
    assert first.status_code == 201
    assert second.status_code == 400
    validate('Error', second.json())
    assert second.json()['code'] == 'pairing_code_used'
    assert 'marcel-hub pair' in second.json()['message']


def test_an_expired_code_is_refused_with_a_readable_message(client: TestClient) -> None:
    code = new_code(client, now=utcnow() - tokens.PAIRING_CODE_TTL - timedelta(seconds=1))
    res = client.post('/api/pair', json={'code': code, 'device_name': 'late'})
    assert res.status_code == 400
    validate('Error', res.json())
    assert res.json()['code'] == 'pairing_code_expired'
    assert res.json()['message'] == (
        'That pairing code has expired. Run `marcel-hub pair` again for a new one.'
    )


def test_a_code_is_valid_for_ten_minutes(client: TestClient) -> None:
    assert timedelta(minutes=10) == tokens.PAIRING_CODE_TTL
    code = new_code(client, now=utcnow() - timedelta(minutes=9, seconds=50))
    assert client.post('/api/pair', json={'code': code, 'device_name': 'x'}).status_code == 201


def test_an_unknown_code_is_refused(client: TestClient) -> None:
    res = client.post('/api/pair', json={'code': 'ZZZZZZZZ', 'device_name': 'x'})
    assert res.status_code == 400
    validate('Error', res.json())
    assert res.json()['code'] == 'pairing_code_invalid'


def test_codes_are_compared_case_insensitively(client: TestClient) -> None:
    code = new_code(client)
    res = client.post('/api/pair', json={'code': code.lower(), 'device_name': 'x'})
    assert res.status_code == 201


@pytest.mark.parametrize(
    'body',
    [{}, {'code': 'ABC', 'device_name': 'x'}, {'code': 'ABCDEFGH', 'device_name': ''}],
)
def test_a_malformed_pair_request_is_a_readable_400(client: TestClient, body: dict) -> None:
    res = client.post('/api/pair', json=body)
    assert res.status_code == 400
    validate('Error', res.json())
    assert res.json()['code'] == 'bad_request'
    assert res.json()['message'].endswith('.')


def test_making_a_code_clears_old_ones(client: TestClient) -> None:
    new_code(client, now=utcnow() - timedelta(hours=1))
    used = new_code(client)
    client.post('/api/pair', json={'code': used, 'device_name': 'x'})
    fresh = new_code(client)
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        rows = s.exec(select(PairingCode)).all()
    assert [r.code_hash for r in rows] == [tokens.hash_secret(fresh)]


# --- the device-token check -------------------------------------------------------------------


def test_no_token_is_401(client: TestClient) -> None:
    res = client.get('/api/me')
    assert res.status_code == 401
    validate('Error', res.json())
    assert res.json()['code'] == 'unauthorized'
    assert res.headers['www-authenticate'] == 'Bearer'


def test_a_wrong_token_is_401(client: TestClient) -> None:
    pair(client)
    assert client.get('/api/me', headers=bearer(tokens.new_token())).status_code == 401


def test_a_token_without_bearer_is_401(client: TestClient) -> None:
    token = pair(client)['token']
    assert client.get('/api/me', headers={'Authorization': token}).status_code == 401
    assert client.get('/api/me', headers={'Authorization': f'Basic {token}'}).status_code == 401


def test_the_token_check_compares_in_constant_time(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = pair(client)['token']
    seen: list[tuple[str, str]] = []

    def refuse(a: str, b: str) -> bool:
        seen.append((a, b))
        return False

    monkeypatch.setattr('marcel_hub.auth.deps.hmac.compare_digest', refuse)
    assert client.get('/api/me', headers=bearer(token)).status_code == 401
    assert seen, 'the token check must go through hmac.compare_digest'


def test_a_revoked_token_is_401(client: TestClient) -> None:
    keep, gone = pair(client, 'iPhone'), pair(client, 'iPad')
    res = client.delete(f'/api/devices/{gone["device_id"]}', headers=bearer(keep['token']))
    assert res.status_code == 204
    assert client.get('/api/me', headers=bearer(gone['token'])).status_code == 401
    assert client.get('/api/me', headers=bearer(keep['token'])).status_code == 200


def test_a_device_can_revoke_itself(client: TestClient) -> None:
    me = pair(client)
    res = client.delete(f'/api/devices/{me["device_id"]}', headers=bearer(me['token']))
    assert res.status_code == 204
    assert client.get('/api/devices', headers=bearer(me['token'])).status_code == 401


def test_revoking_an_unknown_device_is_404(client: TestClient) -> None:
    me = pair(client)
    res = client.delete('/api/devices/dev_nope', headers=bearer(me['token']))
    assert res.status_code == 404
    validate('Error', res.json())


def test_every_phone_sees_and_may_revoke_every_phone(client: TestClient) -> None:
    # One person, many phones (ADR-013): no phone is another's stranger.
    me, other = pair(client, 'mine'), pair(client, 'the iPad')
    devices = client.get('/api/devices', headers=bearer(me['token'])).json()
    assert {d['id'] for d in devices} == {me['device_id'], other['device_id']}
    res = client.delete(f'/api/devices/{other["device_id"]}', headers=bearer(me['token']))
    assert res.status_code == 204
    assert client.get('/api/me', headers=bearer(other['token'])).status_code == 401


# --- GET /me, /devices, PUT /devices/{id}/push ------------------------------------------------


def test_me_matches_the_contract(client: TestClient) -> None:
    me = pair(client)
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        s.add(
            Agent(
                owner_user_id=tokens.OWNER_USER_ID,
                name='Marcel',
                animal='giraffe',
                palette='marcel',
                seed='Marcel',
                main_conversation_id='cnv_main',
            )
        )
        s.add(
            Agent(
                owner_user_id=tokens.OWNER_USER_ID,
                name='Half',
                animal='giraffe',
                palette='marcel',
                seed='Half',
            )
        )
        s.add(
            Agent(
                owner_user_id=tokens.OWNER_USER_ID,
                name='Gone',
                animal='giraffe',
                palette='marcel',
                seed='G',
                main_conversation_id='cnv_gone',
                archived_at=utcnow(),
            )
        )
        s.commit()  # fmt: skip
    body = client.get('/api/me', headers=bearer(me['token'])).json()
    validate('Me', body)
    assert body['name'] == 'Shaun'
    assert body['user_id'] == tokens.OWNER_USER_ID
    assert [a['name'] for a in body['agents']] == ['Marcel'], 'live buddies with a main chat'
    assert 'role' not in body, 'app-api 0.8.0 has no roles'
    assert body['push'] == {'apns': False, 'ntfy': False, 'relay': True}  # the relay: always on


def test_retired_fields_are_still_sent(client: TestClient) -> None:
    """test_contract.RETIRED_FIELDS lists what app-api 0.8.0 removed and the hub still sends; an
    entry the hub stopped sending must leave the list, so the response checks tighten."""
    me = pair(client)
    body = client.get('/api/me', headers=bearer(me['token'])).json()
    for field, story in RETIRED_FIELDS.get('Me', {}).items():
        assert field in body, (
            f'/me no longer sends {field} ({story}): remove it from RETIRED_FIELDS'
        )


def test_devices_lists_this_users_devices_newest_first(client: TestClient, buddy: str) -> None:
    first = pair(client, 'old')
    second = pair(client, 'new')
    body = client.get('/api/devices', headers=bearer(first['token'])).json()
    validate_list('Device', body)
    assert [d['name'] for d in body] == ['new', 'old']
    assert [d['agent_id'] for d in body] == [buddy, buddy]
    assert [d['current'] for d in body] == [False, True]
    assert body[0]['id'] == second['device_id']
    assert body[1]['push'] == 'none'


def test_using_a_token_marks_the_device_as_seen(client: TestClient) -> None:
    me = pair(client)
    body = client.get('/api/devices', headers=bearer(me['token'])).json()
    assert 'last_seen_at' in body[0]


def test_last_seen_is_written_at_most_once_a_minute(client: TestClient) -> None:
    me = pair(client)
    client.get('/api/me', headers=bearer(me['token']))
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        first = s.get(Device, me['device_id'])
        assert first is not None and first.last_seen is not None
        seen = first.last_seen
    client.get('/api/me', headers=bearer(me['token']))
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        again = s.get(Device, me['device_id'])
        assert again is not None
        assert again.last_seen == seen


@pytest.mark.parametrize(
    ('target', 'push'),
    [({'kind': 'ntfy', 'ntfy_topic': 'marcel-shaun'}, 'ntfy'),
     ({'kind': 'apns', 'apns_token': 'ab12' * 16}, 'apns'),
     ({'kind': 'apns', 'apns_token': 'AB12' * 40}, 'apns')],
)  # fmt: skip
def test_registering_push_is_saved(client: TestClient, buddy: str, target: dict, push: str) -> None:
    me = pair(client)
    res = client.put(
        f'/api/devices/{me["device_id"]}/push', json=target, headers=bearer(me['token'])
    )
    assert res.status_code == 200, res.text
    validate('Device', res.json())
    assert res.json()['push'] == push
    assert res.json()['agent_id'] == buddy


RELAY_TARGET = {
    'kind': 'relay',
    'relay_url': 'https://marcel-relay.example.workers.dev',
    'handle': 'hY3kQ9vT2mW8pL5xR1nB7c',
    'send_secret': 'Zk1QbXJ4V2c5dEw3cU5hYjJ5Rk1uVDhvSzZlUjRzVmQ',
    'device_key': 'njCdIc2oG-JgfnBbP2hZZXoNELO71-teMzDch9iRfBs',
}


def test_a_relay_target_is_stored_and_its_secret_never_shown(
    client: TestClient, buddy: str
) -> None:
    me = pair(client)
    res = client.put(
        f'/api/devices/{me["device_id"]}/push', json=RELAY_TARGET, headers=bearer(me['token'])
    )
    assert res.status_code == 200, res.text
    validate('Device', res.json())
    assert res.json()['push'] == 'relay'
    listed = client.get('/api/devices', headers=bearer(me['token']))
    for text in (res.text, listed.text):
        assert RELAY_TARGET['send_secret'] not in text
        assert RELAY_TARGET['handle'] not in text
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        device = s.get(Device, me['device_id'])
        assert device is not None
        assert (device.relay_url, device.relay_handle, device.relay_secret,
                device.relay_device_key) == (RELAY_TARGET['relay_url'], RELAY_TARGET['handle'],
                                             RELAY_TARGET['send_secret'],
                                             RELAY_TARGET['device_key'])  # fmt: skip
    # Another kind replaces it: one target per phone.
    res = client.put(
        f'/api/devices/{me["device_id"]}/push',
        json={'kind': 'ntfy', 'ntfy_topic': 'marcel-shaun'},
        headers=bearer(me['token']),
    )
    assert res.json()['push'] == 'ntfy'
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        device = s.get(Device, me['device_id'])
        assert device is not None
        assert (device.relay_url, device.relay_handle, device.relay_secret,
                device.relay_device_key) == (None, None, None, None)  # fmt: skip


@pytest.mark.parametrize(
    'change',
    [{'relay_url': None}, {'handle': None}, {'send_secret': None}, {'device_key': None},
     {'relay_url': 'http://relay.example'}, {'relay_url': 'https://user@relay.example'},
     {'relay_url': 'https://relay.example/?x=1'}, {'handle': 'short'}, {'send_secret': 'x' * 44},
     {'device_key': 'njCdIc2oG-JgfnBbP2hZZXoNELO71-teMzDch9iRfBt'}],
    ids=['no-url', 'no-handle', 'no-secret', 'no-key', 'http', 'userinfo', 'query',
         'bad-handle', 'bad-secret', 'bad-key'],
)  # fmt: skip
def test_a_bad_relay_target_is_400_and_stores_nothing(
    client: TestClient, change: dict[str, Any]
) -> None:
    me = pair(client)
    target = {k: v for k, v in {**RELAY_TARGET, **change}.items() if v is not None}
    res = client.put(
        f'/api/devices/{me["device_id"]}/push', json=target, headers=bearer(me['token'])
    )
    assert res.status_code == 400, res.text
    validate('Error', res.json())
    assert RELAY_TARGET['send_secret'] not in res.text
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        device = s.get(Device, me['device_id'])
        assert device is not None and device.relay_handle is None


@pytest.mark.parametrize(
    'target', [{'kind': 'ntfy'}, {'kind': 'apns'}, {'kind': 'pigeon'}, {'kind': 'ntfy', 'x': 1}]
)
def test_an_incomplete_push_target_is_400(client: TestClient, target: dict) -> None:
    me = pair(client)
    res = client.put(
        f'/api/devices/{me["device_id"]}/push', json=target, headers=bearer(me['token'])
    )
    assert res.status_code == 400
    validate('Error', res.json())


def test_push_for_an_unknown_device_is_404(client: TestClient) -> None:
    me = pair(client)
    res = client.put(
        '/api/devices/dev_nope/push',
        json={'kind': 'ntfy', 'ntfy_topic': 't'},
        headers=bearer(me['token']),
    )
    assert res.status_code == 404


def test_a_route_without_the_token_check_refuses_to_run() -> None:
    from starlette.requests import Request

    request = Request({'type': 'http', 'path': '/api/me', 'headers': [], 'query_string': b''})
    with pytest.raises(RuntimeError, match='require_device'):
        current_device(request)


# --- limits -----------------------------------------------------------------------------------


def test_too_many_wrong_codes_lock_pairing_for_a_minute(client: TestClient) -> None:
    clock = [1000.0]
    limit = tokens.PairGuessLimit(clock=lambda: clock[0])
    client.app.state.pair_guess_limit = limit  # type: ignore[attr-defined]
    for _ in range(tokens.MAX_WRONG_CODES):
        res = client.post('/api/pair', json={'code': 'ZZZZZZZZ', 'device_name': 'x'})
        assert res.status_code == 400
    good = new_code(client)
    res = client.post('/api/pair', json={'code': good, 'device_name': 'x'})
    assert res.status_code == 429
    validate('Error', res.json())
    assert res.json()['code'] == 'too_many_tries'
    assert res.headers['retry-after'] == '60'
    clock[0] += tokens.WRONG_CODE_WINDOW
    assert client.post('/api/pair', json={'code': good, 'device_name': 'x'}).status_code == 201


def test_expired_or_used_codes_do_not_count_as_guesses(client: TestClient) -> None:
    code = new_code(client)
    client.post('/api/pair', json={'code': code, 'device_name': 'x'})
    for _ in range(tokens.MAX_WRONG_CODES):
        client.post('/api/pair', json={'code': code, 'device_name': 'x'})
    assert (
        client.post('/api/pair', json={'code': new_code(client), 'device_name': 'y'}).status_code
        == 201
    )


def test_push_targets_have_a_length_limit(client: TestClient) -> None:
    me = pair(client)
    res = client.put(
        f'/api/devices/{me["device_id"]}/push',
        json={'kind': 'ntfy', 'ntfy_topic': 'x' * 201},
        headers=bearer(me['token']),
    )
    assert res.status_code == 400


def test_a_crash_answers_in_plain_words(settings: Settings) -> None:
    app = create_app(settings)

    @app.get('/api/boom')
    def boom() -> None:  # pyright: ignore[reportUnusedFunction]
        raise RuntimeError('secret detail')

    with TestClient(app, raise_server_exceptions=False) as c:
        res = c.get('/api/boom')
    assert res.status_code == 500
    validate('Error', res.json())
    assert res.json()['code'] == 'internal_error'
    assert 'secret detail' not in res.text


@pytest.mark.parametrize(
    'token',
    [
        'abc123',
        'zz' * 32,
        'ab12' * 15 + 'ab1',
        'ab' * 101,
        '../3/device/' + 'ab' * 30,
        'ab 12' * 16,
    ],
    ids=['short', 'not-hex', 'odd', 'long', 'path', 'spaces'],
)
def test_an_apns_token_that_is_not_hex_of_a_sane_length_is_400(
    client: TestClient, token: str
) -> None:
    me = pair(client)
    res = client.put(
        f'/api/devices/{me["device_id"]}/push',
        json={'kind': 'apns', 'apns_token': token},
        headers=bearer(me['token']),
    )
    assert res.status_code == 400
    assert 'apns_token' in res.json()['message']
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        device = s.get(Device, me['device_id'])
        assert device is not None and device.apns_token is None


# --- PATCH /devices/{id}, POST /pairing-codes (S-22.27, B-49) --------------------------------


def second_buddy(client: TestClient, name: str = 'Pip', archived: bool = False) -> str:
    """Another buddy with its main conversation (archived when asked)."""
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        agent = Agent(owner_user_id=tokens.OWNER_USER_ID, name=name, animal='fox',
                      palette='marcel', seed=name, slug=name.lower(),
                      archived_at=utcnow() if archived else None)  # fmt: skip
        s.add(agent)
        s.commit()
        ensure_main_conversation(s, agent)
        return agent.id


def device_events(client: TestClient) -> list[dict[str, Any]]:
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        rows = s.exec(select(AppEvent).where(AppEvent.type == 'device.updated')).all()
        frames = [{'id': e.id, 'type': e.type, 'at': e.at.isoformat(), 'data': e.data_json}
                  for e in rows]  # fmt: skip
    for frame in frames:
        APP_EVENT.validate(frame)
    return [f['data'] for f in frames]


def test_a_phone_switches_buddy_and_every_phone_hears_of_it(client: TestClient, buddy: str) -> None:
    pip = second_buddy(client)
    me, ipad = pair(client, 'mine'), pair(client, 'the iPad')
    # Any phone may change any phone's buddy: this one changes the iPad's.
    res = client.patch(
        f'/api/devices/{ipad["device_id"]}', json={'agent_id': pip}, headers=bearer(me['token'])
    )
    assert res.status_code == 200, res.text
    validate('Device', res.json())
    assert (res.json()['agent_id'], res.json()['current']) == (pip, False)
    [event] = device_events(client)  # validated against events.schema.json
    assert (event['id'], event['agent_id']) == (ipad['device_id'], pip)
    assert 'current' not in event, 'an event is read by every phone'
    devices = client.get('/api/devices', headers=bearer(me['token'])).json()
    assert {d['id']: d['agent_id'] for d in devices} == {
        me['device_id']: buddy,
        ipad['device_id']: pip,
    }
    # The same buddy again changes nothing and sends nothing.
    client.patch(f'/api/devices/{ipad["device_id"]}', json={'agent_id': pip},
                 headers=bearer(me['token']))  # fmt: skip
    assert len(device_events(client)) == 1


def test_switching_to_no_buddy_or_an_archived_one_is_404_and_changes_nothing(
    client: TestClient, buddy: str
) -> None:
    # Control: delete `find_live_buddy` in `update_device` and this goes red.
    gone = second_buddy(client, 'Old', archived=True)
    me = pair(client)
    url = f'/api/devices/{me["device_id"]}'
    for agent_id in ('agt_nope', gone):
        res = client.patch(url, json={'agent_id': agent_id}, headers=bearer(me['token']))
        assert res.status_code == 404 and res.json()['code'] == 'agent_not_found', agent_id
        validate('Error', res.json())
    res = client.patch('/api/devices/dev_nope', json={'agent_id': buddy},
                       headers=bearer(me['token']))  # fmt: skip
    assert res.status_code == 404 and res.json()['code'] == 'device_not_found'
    for body in ({}, {'agent_id': ''}, {'agent_id': buddy, 'name': 'x'}):
        assert client.patch(url, json=body, headers=bearer(me['token'])).status_code == 400
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        device = s.get(Device, me['device_id'])
        assert device is not None and device.agent_id == buddy
    assert device_events(client) == []


def test_a_pairing_code_with_a_buddy_pairs_a_phone_showing_it(
    client: TestClient, buddy: str
) -> None:
    pip = second_buddy(client)
    me = pair(client)
    res = client.post('/api/pairing-codes', json={'agent_id': pip}, headers=bearer(me['token']))
    assert res.status_code == 201, res.text
    body = res.json()
    validate('IssuedPairingCode', body)
    assert body['link'].endswith(f'&code={body["code"]}&buddy={pip}')
    expires = datetime.fromisoformat(body['expires_at'])
    assert timedelta(minutes=9) < expires - datetime.now(UTC) <= timedelta(minutes=10)
    new = client.post('/api/pair', json={'code': body['code'], 'device_name': 'iPad'})
    assert new.status_code == 201
    devices = client.get('/api/devices', headers=bearer(me['token'])).json()
    assert {d['id']: d['agent_id'] for d in devices}[new.json()['device_id']] == pip


def test_a_pairing_code_without_a_buddy_pairs_a_phone_on_the_first(
    client: TestClient, buddy: str
) -> None:
    second_buddy(client)
    me = pair(client)
    res = client.post('/api/pairing-codes', headers=bearer(me['token']))
    assert res.status_code == 201
    validate('IssuedPairingCode', res.json())
    assert 'buddy=' not in res.json()['link']
    assert res.json()['link'].startswith(
        'marcel://pair?hub=https%3A%2F%2Fmarcel.example.ts.net%2Fapi&code='
    )
    new = client.post('/api/pair', json={'code': res.json()['code'], 'device_name': 'iPad'})
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        device = s.get(Device, new.json()['device_id'])
        assert device is not None and device.agent_id == buddy


def test_a_pairing_code_for_no_buddy_or_an_archived_one_is_404(
    client: TestClient, buddy: str
) -> None:
    # Control: delete the `find_live_buddy` check in `new_pairing_code` and this goes red.
    gone = second_buddy(client, 'Old', archived=True)
    me = pair(client)
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        before = len(s.exec(select(PairingCode)).all())
    for agent_id in ('agt_nope', gone):
        res = client.post('/api/pairing-codes', json={'agent_id': agent_id},
                          headers=bearer(me['token']))  # fmt: skip
        assert res.status_code == 404 and res.json()['code'] == 'agent_not_found'
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        assert len(s.exec(select(PairingCode)).all()) == before, 'no code was made'


def test_a_code_whose_buddy_was_archived_since_pairs_on_the_first(
    client: TestClient, buddy: str
) -> None:
    pip = second_buddy(client)
    code = new_code(client, agent_id=pip)
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        agent = s.get(Agent, pip)
        assert agent is not None
        agent.archived_at = utcnow()
        s.add(agent)
        s.commit()
    new = client.post('/api/pair', json={'code': code, 'device_name': 'iPad'}).json()
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        device = s.get(Device, new['device_id'])
        assert device is not None and device.agent_id == buddy


def test_without_a_public_address_no_pairing_code_is_made(settings: Settings) -> None:
    # Control: delete the `public_url` check in `new_pairing_code` and this goes red. Without it
    # the link would name the address the phone used, maybe plain http, where the code and the
    # new phone's token can be read on the Wi-Fi. `marcel-hub pair` refuses the same way.
    settings = dataclasses.replace(settings, public_url=None)
    with TestClient(create_app(settings)) as client:
        me = pair(client)
        res = client.post('/api/pairing-codes', headers=bearer(me['token']))
        assert res.status_code == 400 and 'marcel connect' in res.json()['message']
        validate('Error', res.json())
        with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
            assert len(s.exec(select(PairingCode)).all()) == 1, 'only the first phone code, used'


def test_every_phone_hears_when_a_new_phone_is_paired(client: TestClient) -> None:
    # Control: delete `_tell_every_phone_of` in `pair` and this goes red. Any phone can pair
    # another, so a lost phone pairing a stranger's must not go unseen.
    pushed: list[tuple[Any, str | None]] = []
    client.app.state.pusher.push = lambda n, buddy: pushed.append((n, buddy)) or True  # type: ignore[attr-defined]
    ipad = pair(client, 'Stranger iPad')
    [(n, buddy)] = pushed
    assert buddy is None, 'to every phone'
    assert n.title == 'A new phone was paired' and 'Stranger iPad' in n.body
    assert n.deep_link == 'marcel://status' and n.collapse_key == f'paired-{ipad["device_id"]}'
    client.post('/api/pair', json={'code': 'WRONGCOD', 'device_name': 'x'})
    assert len(pushed) == 1, 'a refused code tells no one'
