"""The relay backend (S-22.15): it seals each push for the phone, exactly as app-api.yaml
`SealedPush` says, and posts only the sealed bytes to the phone's relay. Nothing here reaches the
network: the relay is an httpx mock transport."""

import base64
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Resource
from sqlalchemy.engine import Engine
from sqlmodel import Session
from test_auth import bearer, pair
from test_notify_fakes import Sleeps, pusher_for
from test_tasks_fakes import BASE, REGISTRY, add_agent, put_task, run, validator

from marcel_hub.app import Settings, create_app
from marcel_hub.models import Device, TaskState
from marcel_hub.notify import Pusher, PushFailed, PushNotification, RelayNotifier
from marcel_hub.notify.backends import target_kind
from marcel_hub.notify.relay import INFO, seal, sealed_content

ROOT = Path(__file__).resolve().parents[2]
VECTOR = json.loads((ROOT / 'contracts/tests/fixtures/sealed-push/vector.json').read_text())
SEALED = validator('app-api.yaml#/components/schemas/SealedPush')
_RELAY_API = yaml.safe_load((ROOT / 'contracts/push-relay.yaml').read_text())
_RELAY_API['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
PUSH = Draft202012Validator(
    {'$ref': BASE + 'push-relay.yaml#/components/schemas/Push'},
    registry=REGISTRY.with_resource(BASE + 'push-relay.yaml', Resource.from_contents(_RELAY_API)),
)

RELAY = 'https://relay.test'
HANDLE = 'hY3kQ9vT2mW8pL5xR1nB7c'
SECRET = 'Zk1QbXJ4V2c5dEw3cU5hYjJ5Rk1uVDhvSzZlUjRzVmQ'
DEVICE_PRIVATE = X25519PrivateKey.generate()


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


DEVICE_KEY = b64(DEVICE_PRIVATE.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))

APPROVAL = PushNotification(
    title='Pip needs your OK',
    body='Garden planner: allow `npm install`?',
    category='approval',
    level='urgent',
    deep_link='marcel://approval/apr_01J9ZR2',
    collapse_key='tsk_01J9ZQ6',
    task_id='tsk_01J9ZQ6',
    approval_id='apr_01J9ZR2',
)
DONE = PushNotification(
    title='Done: Check the disk',
    body='12 GB free on /',
    category='task',
    level='normal',
    deep_link='marcel://task/tsk_01J9ZQ6',
)


def open_box(sealed: dict[str, Any], private: X25519PrivateKey = DEVICE_PRIVATE) -> dict[str, Any]:
    """What the phone's Notification Service Extension does (app-api.yaml `SealedPush`)."""
    eph = unb64(sealed['eph'])
    mine = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    shared = private.exchange(X25519PublicKey.from_public_bytes(eph))
    key = HKDF(algorithm=SHA256(), length=32, salt=eph + mine, info=INFO).derive(shared)
    plain = ChaCha20Poly1305(key).decrypt(unb64(sealed['nonce']), unb64(sealed['box']), INFO)
    return json.loads(plain)


class FakeRelay:
    """The push relay. `mode`: 'ok', 'down', 'slow' (a timeout) or an HTTP status to answer."""

    def __init__(self, mode: str | int = 'ok') -> None:
        self.mode = mode
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.mode == 'down':
            raise httpx.ConnectError('Connection refused', request=request)
        if self.mode == 'slow':
            raise httpx.ReadTimeout('timed out', request=request)
        if isinstance(self.mode, int):
            return httpx.Response(self.mode, json={'code': 'x', 'message': 'no'})
        return httpx.Response(202, json={'ok': True})

    def notifier(self, engine: Engine) -> RelayNotifier:
        return RelayNotifier(engine, httpx.AsyncClient(transport=httpx.MockTransport(self.handle)))


def relay_device(
    engine: Engine,
    device_id: str = 'dev_relay',
    relay_url: str = RELAY,
    agent_id: str | None = None,
) -> Device:
    device = Device(id=device_id, user_id='usr_owner', name='Phone', token_hash=f'h-{device_id}',
                    relay_url=relay_url, relay_handle=HANDLE, relay_secret=SECRET,
                    relay_device_key=DEVICE_KEY, agent_id=agent_id)  # fmt: skip
    with Session(engine) as s:
        s.add(device)
        s.commit()
        s.refresh(device)
    return device


# Sealing ---------------------------------------------------------------------------------------


def test_the_sealed_bytes_match_the_contracts_known_answer_vector() -> None:
    eph = X25519PrivateKey.from_private_bytes(unb64(VECTOR['eph_private_key']))
    sealed = seal(
        VECTOR['content'], VECTOR['device_key'], eph=eph, nonce=unb64(VECTOR['sealed']['nonce'])
    )
    assert sealed == VECTOR['sealed']
    SEALED.validate(sealed)


def test_the_content_is_the_contracts_json_byte_for_byte() -> None:
    n = APPROVAL.model_copy(
        update={'title': 'Pip needs you', 'body': 'Allow `npm install` in garden-planner?'}
    )
    content = sealed_content(n, 'agt_01JA3E2')
    assert json.dumps(content, separators=(',', ':'), ensure_ascii=False) == VECTOR['plaintext']
    assert sealed_content(DONE, None) == {
        'title': 'Done: Check the disk',
        'body': '12 GB free on /',
        'link': 'marcel://task/tsk_01J9ZQ6',
        'category': 'task',
    }


def test_every_push_has_a_fresh_key_and_nonce_and_the_phone_opens_it() -> None:
    one = seal(sealed_content(DONE, None), DEVICE_KEY)
    two = seal(sealed_content(DONE, None), DEVICE_KEY)
    assert one['eph'] != two['eph'] and one['nonce'] != two['nonce']
    assert open_box(one) == open_box(two) == sealed_content(DONE, None)
    with pytest.raises(InvalidTag):  # another phone's key cannot open it
        open_box(one, X25519PrivateKey.generate())


def test_the_longest_push_fits_the_contracts_box() -> None:
    n = PushNotification(title='🦒' * 80, body='🌿' * 300, category='note', level='urgent',
                         deep_link='marcel://message/' + 'm' * 40)  # fmt: skip
    sealed = seal(sealed_content(n, 'agt_' + 'a' * 40), DEVICE_KEY)
    SEALED.validate(sealed)  # box maxLength 2700


# Sending ---------------------------------------------------------------------------------------


def test_the_relay_gets_only_the_sealed_push_never_the_text(engine: Engine) -> None:
    relay = FakeRelay()
    device = relay_device(engine)

    async def go() -> None:
        notifier = relay.notifier(engine)
        await notifier.send(device, APPROVAL, agent_id='agt_pip')
        await notifier.send(device, DONE)
        await notifier.aclose()

    run(go())
    first, second = relay.requests
    assert first.method == 'POST'
    assert str(first.url) == RELAY + '/v1/push'
    assert first.headers['authorization'] == f'Bearer {SECRET}'
    raw = first.content.decode()
    for plain in (APPROVAL.title, APPROVAL.body, APPROVAL.deep_link, 'agt_pip', 'apr_01J9ZR2',
                  'tsk_01J9ZQ6', 'approval', DEVICE_KEY, SECRET):  # fmt: skip
        assert plain not in raw, f'the relay saw {plain!r}'
    body = json.loads(first.content)
    PUSH.validate(body)
    assert set(body) == {'handle', 'sealed', 'urgent'}
    assert body['handle'] == HANDLE and body['urgent'] is True
    assert open_box(body['sealed']) == {
        'title': APPROVAL.title,
        'body': APPROVAL.body,
        'link': APPROVAL.deep_link,
        'agent_id': 'agt_pip',
        'category': 'approval',
    }
    later = json.loads(second.content)
    assert later['urgent'] is False
    assert 'agent_id' not in open_box(later['sealed'])


def test_a_relay_url_with_a_path_or_slash_is_posted_under_it(engine: Engine) -> None:
    relay = FakeRelay()
    device = relay_device(engine, relay_url='https://relay.test/marcel/')
    run(relay.notifier(engine).send(device, DONE))
    assert str(relay.requests[0].url) == 'https://relay.test/marcel/v1/push'


def test_a_410_clears_the_phones_push_target(engine: Engine) -> None:
    relay = FakeRelay(410)
    device = relay_device(engine)
    with pytest.raises(PushFailed) as err:
        run(relay.notifier(engine).send(device, DONE))
    assert err.value.retry is False
    with Session(engine) as s:
        stored = s.get(Device, device.id)
        assert stored is not None
        assert (stored.relay_url, stored.relay_handle, stored.relay_secret,
                stored.relay_device_key) == (None, None, None, None)  # fmt: skip
    assert target_kind(stored) is None  # `Device.push` reads `none`: the phone registers again


def test_a_410_keeps_a_target_the_phone_registered_meanwhile(engine: Engine) -> None:
    relay = FakeRelay(410)
    device = relay_device(engine)
    with Session(engine) as s:  # the phone registered again while the push was on its way
        fresh = s.get(Device, device.id)
        assert fresh is not None
        fresh.relay_handle = 'Nw' * 11
        s.add(fresh)
        s.commit()
    with pytest.raises(PushFailed):
        run(relay.notifier(engine).send(device, DONE))
    with Session(engine) as s:
        stored = s.get(Device, device.id)
        assert stored is not None and stored.relay_handle == 'Nw' * 11


@pytest.mark.parametrize(
    ('mode', 'retry'),
    [('down', True), ('slow', True), (500, True), (502, True), (503, True),
     (400, False), (401, False), (413, False), (429, False)],
)  # fmt: skip
def test_a_failed_push_raises_push_failed(engine: Engine, mode: str | int, retry: bool) -> None:
    relay = FakeRelay(mode)
    device = relay_device(engine)
    with pytest.raises(PushFailed) as err:
        run(relay.notifier(engine).send(device, DONE))
    assert err.value.retry is retry
    for secret in (SECRET, HANDLE, DEVICE_KEY):
        assert secret not in str(err.value)
    with Session(engine) as s:  # only a 410 clears the target
        stored = s.get(Device, device.id)
        assert stored is not None and stored.relay_handle == HANDLE


def test_a_device_without_a_relay_target_is_refused_without_a_request(engine: Engine) -> None:
    relay = FakeRelay()
    device = Device(id='dev_x', user_id='usr_owner', name='Phone', token_hash='h')
    with pytest.raises(PushFailed) as err:
        run(relay.notifier(engine).send(device, DONE))
    assert err.value.retry is False
    assert relay.requests == []


def test_a_key_no_push_can_be_sealed_for_fails_cleanly(engine: Engine) -> None:
    """A low-order key (all zeros) from before the route checked it: a clean, final
    `PushFailed`, so the pusher still tells the other phones an urgent push was lost."""
    relay = FakeRelay()
    device = relay_device(engine)
    device.relay_device_key = 'A' * 43
    with pytest.raises(PushFailed) as err:
        run(relay.notifier(engine).send(device, DONE))
    assert err.value.retry is False
    assert relay.requests == []


def test_a_5xx_is_retried_by_the_pusher_like_the_other_backends(engine: Engine) -> None:
    relay = FakeRelay(502)
    add_agent(engine, owner='usr_owner')
    relay_device(engine)
    sleeps = Sleeps()
    pusher = pusher_for(engine, relay.notifier(engine), sleep=sleeps)
    task = put_task(engine, TaskState.done, summary='12 GB free')

    async def go() -> None:
        pusher.on_state(task, 'done')
        await pusher.wait()

    run(go())
    assert len(relay.requests) == 3
    assert len(sleeps.delays) == 2


def test_the_pusher_seals_the_push_with_its_buddy(engine: Engine) -> None:
    relay = FakeRelay()
    add_agent(engine, 'agt_pip', owner='usr_owner')
    relay_device(engine, agent_id='agt_pip')
    pusher = pusher_for(engine, relay.notifier(engine))
    task = put_task(engine, TaskState.done, agent_id='agt_pip', summary='12 GB free')

    async def go() -> None:
        pusher.on_state(task, 'done')
        pusher.broke('runner_unreachable', 'Marcel cannot reach the NUC', 'Tasks may be stale.')
        await pusher.wait()

    run(go())
    opened = [open_box(json.loads(r.content)['sealed']) for r in relay.requests]
    assert [(c['title'], c.get('agent_id')) for c in opened] == [
        ('Done: A done task', 'agt_pip'),
        ('Marcel cannot reach the NUC', None),  # about Marcel as a whole: no buddy
    ]


def test_no_secret_or_text_reaches_a_log(engine: Engine, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    add_agent(engine, owner='usr_owner')
    relay_device(engine)
    relay = FakeRelay()
    pusher = pusher_for(engine, relay.notifier(engine))

    async def ask(task: str) -> None:
        pusher.on_state(task, 'needs_you')
        await pusher.wait()

    run(ask(put_task(engine, TaskState.needs_you, waiting_for='Which disk?')))
    relay.mode = 410  # then the phone is gone: the 410 path logs too
    run(ask(put_task(engine, TaskState.needs_you, waiting_for='Which disk?')))
    assert len(relay.requests) == 2
    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'dev_relay' in text  # logs name the device
    for secret in (SECRET, HANDLE, DEVICE_KEY, 'Which disk?'):
        assert secret not in text


# Through the app -------------------------------------------------------------------------------


def test_a_phone_registered_for_the_relay_gets_its_pushes_through_it(tmp_path: Path) -> None:
    """The path production takes: no setting, `PUT /devices/{id}/push` with `kind: relay`, then a
    push; the relay's 410 makes the phone read `push: none`."""
    relay = FakeRelay()
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db')
    with TestClient(create_app(settings)) as client:
        pusher: Pusher = client.app.state.pusher  # type: ignore[attr-defined]
        notifier = pusher.notifiers['relay']
        assert isinstance(notifier, RelayNotifier)
        old = notifier.client
        notifier.client = httpx.AsyncClient(transport=httpx.MockTransport(relay.handle))
        client.portal.call(old.aclose)  # type: ignore[union-attr]
        me = pair(client)
        assert client.get('/api/me', headers=bearer(me['token'])).json()['push']['relay'] is True
        target = {'kind': 'relay', 'relay_url': RELAY, 'handle': HANDLE, 'send_secret': SECRET,
                  'device_key': DEVICE_KEY}  # fmt: skip
        res = client.put(
            f'/api/devices/{me["device_id"]}/push', json=target, headers=bearer(me['token'])
        )
        assert res.json()['push'] == 'relay'

        async def broke() -> None:
            pusher.broke('runner_unreachable', 'Marcel cannot reach the NUC', 'No answer.')
            await pusher.wait()

        client.portal.call(broke)  # type: ignore[union-attr]
        assert len(relay.requests) == 1
        sent = json.loads(relay.requests[0].content)
        assert open_box(sent['sealed'])['title'] == 'Marcel cannot reach the NUC'
        relay.mode = 410

        async def again() -> None:
            pusher.broke('runner_warning', 'The NUC has a problem', 'Disk full.')
            await pusher.wait()

        client.portal.call(again)  # type: ignore[union-attr]
        devices = client.get('/api/devices', headers=bearer(me['token'])).json()
    assert [d['push'] for d in devices] == ['none']  # the app registers again
