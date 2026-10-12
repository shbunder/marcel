"""Pairing a phone and the device-token check (B-27)."""

import hashlib
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from sqlmodel import Session, select

from marcel_hub.app import Settings, create_app
from marcel_hub.auth import current_device, tokens
from marcel_hub.models import Agent, Device, PairingCode, utcnow

CONTRACT = Path(__file__).resolve().parents[2] / 'contracts' / 'app-api.yaml'
SPEC = yaml.safe_load(CONTRACT.read_text())
REGISTRY = Registry().with_resource(
    'urn:contract',
    Resource.from_contents(SPEC | {'$schema': 'https://json-schema.org/draft/2020-12/schema'}),
)


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
    return Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db', owner_name='Shaun')


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


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


def test_another_users_device_cannot_be_seen_or_revoked(client: TestClient) -> None:
    me = pair(client)
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        other = Device(user_id='usr_other', name='theirs', token_hash='x' * 64)
        s.add(other)
        s.commit()
        other_id = other.id
    devices = client.get('/api/devices', headers=bearer(me['token'])).json()
    assert other_id not in [d['id'] for d in devices]
    res = client.delete(f'/api/devices/{other_id}', headers=bearer(me['token']))
    assert res.status_code == 404


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
                owner_user_id='usr_other',
                name='Theirs',
                animal='giraffe',
                palette='marcel',
                seed='T',
                main_conversation_id='cnv_other',
            )
        )
        s.commit()  # fmt: skip
    body = client.get('/api/me', headers=bearer(me['token'])).json()
    validate('Me', body)
    assert body['name'] == 'Shaun'
    assert body['user_id'] == tokens.OWNER_USER_ID
    assert [a['name'] for a in body['agents']] == ['Marcel']
    assert body['push'] == {'apns': False, 'ntfy': False}


def test_devices_lists_this_users_devices_newest_first(client: TestClient) -> None:
    first = pair(client, 'old')
    second = pair(client, 'new')
    body = client.get('/api/devices', headers=bearer(first['token'])).json()
    validate_list('Device', body)
    assert [d['name'] for d in body] == ['new', 'old']
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
     ({'kind': 'apns', 'apns_token': 'abc123'}, 'apns')],
)  # fmt: skip
def test_registering_push_is_saved(client: TestClient, target: dict, push: str) -> None:
    me = pair(client)
    res = client.put(
        f'/api/devices/{me["device_id"]}/push', json=target, headers=bearer(me['token'])
    )
    assert res.status_code == 200, res.text
    validate('Device', res.json())
    assert res.json()['push'] == push


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
