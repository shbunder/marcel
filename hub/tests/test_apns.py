"""The APNs backend: the provider token, the payload per category, a gone device token, failures,
the settings, and that no secret reaches a log. Apple is an httpx mock transport; the key is made
fresh for each test run and never written to disk."""

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session
from test_auth import bearer, pair, validate
from test_notify_fakes import NTFY, TOPIC, Clock, FakeNtfy, add_device, pusher_for
from test_tasks_fakes import add_agent, put_task, run

from marcel_hub import notify
from marcel_hub.app import Settings, SettingsError, create_app, load_settings
from marcel_hub.models import Device, TaskState
from marcel_hub.notify import ApnsConfig, ApnsNotifier, PushFailed, PushNotification, routing
from marcel_hub.notify.apns import (
    PAYLOAD_MAX,
    PRODUCTION,
    SANDBOX,
    TOKEN_SECONDS,
    ProviderToken,
    load_key,
    payload,
)
from marcel_hub.notify.message import BODY_MAX

_KEY = ec.generate_private_key(ec.SECP256R1())
KEY_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
KEY_BODY = ''.join(KEY_PEM.strip().splitlines()[1:-1])
KEY_ID = 'KID-SECRET-81'
TEAM_ID = 'TEAM-SECRET-42'
BUNDLE = 'com.example.marcel'
DEVICE_TOKEN = 'a1b2c3d4' * 8
CONFIG = ApnsConfig(key=KEY_PEM, key_id=KEY_ID, team_id=TEAM_ID, topic=BUNDLE)


def b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + '=' * (-len(part) % 4))


def verify_jwt(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Check an ES256 JWT against the test key's public half; return its header and claims."""
    header, claims, sig = token.split('.')
    raw = b64decode(sig)
    assert len(raw) == 64
    der = encode_dss_signature(int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:], 'big'))
    _KEY.public_key().verify(der, f'{header}.{claims}'.encode(), ec.ECDSA(hashes.SHA256()))
    return json.loads(b64decode(header)), json.loads(b64decode(claims))


class FakeApple:
    """Apple's push server. `answers` maps a device token to a status (and reason) or 'down'."""

    def __init__(self, **answers: Any) -> None:
        self.answers: dict[str, Any] = answers
        self.requests: list[httpx.Request] = []
        self.sent: list[str] = []  # device tokens that got a 200

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        token = request.url.path.rsplit('/', 1)[-1]
        answer = self.answers.get(token, 200)
        if answer == 'down':
            raise httpx.ConnectError('Connection refused', request=request)
        if answer == 'timeout':
            raise httpx.ReadTimeout('', request=request)
        status, reason = answer if isinstance(answer, tuple) else (answer, None)
        if status == 200:
            self.sent.append(token)
            return httpx.Response(200, headers={'apns-id': 'x'})
        return httpx.Response(status, json={'reason': reason} if reason else {})

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]

    def notifier(
        self, engine: Engine, config: ApnsConfig = CONFIG, clock: Any = None
    ) -> ApnsNotifier:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        if clock is None:
            return ApnsNotifier(config, engine, client)
        return ApnsNotifier(config, engine, client, clock=clock)


def phone(token: str = DEVICE_TOKEN, device_id: str = 'dev_phone') -> Device:
    return Device(id=device_id, user_id='usr_1', name='Phone', token_hash='h', apns_token=token)


def pushes() -> dict[str, PushNotification]:
    """One push of each category, built by the hub's own routing."""
    from marcel_hub.models import Approval, Task

    task = Task(id='tsk_1', agent_id='agt_1', title='Check the disk', prompt='p', summary='12 GB')
    approval = Approval(id='apr_1', task_id='tsk_1', tool='Bash', summary='allow `df`?')
    return {
        'approval': routing.for_approval(task, approval),
        'task': routing.for_state(task, TaskState.done),  # type: ignore[dict-item]
        'broke': routing.for_broke(
            'runner_unreachable', 'NUC down', 'No answer.', 'marcel://status'
        ),
        'digest': routing.for_digest('msg_1', 'Your morning', 'Three things today.'),
        'note': routing.for_note('msg_2', 'Heads up', 'The backup ran.', 'normal'),
    }


# The provider token ------------------------------------------------------------------------


def test_the_provider_token_is_an_es256_jwt_apple_can_check() -> None:
    clock = Clock()
    token = ProviderToken(_KEY, KEY_ID, TEAM_ID, clock).get()
    header, claims = verify_jwt(token)
    assert header == {'alg': 'ES256', 'kid': KEY_ID}
    assert claims == {'iss': TEAM_ID, 'iat': int(clock.now)}


def test_the_provider_token_is_reused_and_made_again_before_an_hour() -> None:
    clock = Clock()
    tokens = ProviderToken(_KEY, KEY_ID, TEAM_ID, clock)
    first = tokens.get()
    clock.now += TOKEN_SECONDS - 1
    assert tokens.get() == first  # not a new token per push
    clock.now += 1
    second = tokens.get()
    assert second != first
    assert verify_jwt(second)[1]['iat'] == int(clock.now)
    assert TOKEN_SECONDS < 3600  # Apple refuses a token older than an hour
    assert TOKEN_SECONDS >= 20 * 60  # and one made more often than every 20 minutes


def test_an_expired_provider_token_is_made_again_on_the_next_try(engine: Engine) -> None:
    clock = Clock()
    apple = FakeApple(**{DEVICE_TOKEN: (403, 'ExpiredProviderToken')})
    notifier = apple.notifier(engine, clock=clock)
    with pytest.raises(PushFailed) as err:
        run(notifier.send(phone(), pushes()['task']))
    assert err.value.retry is True
    clock.now += 1
    apple.answers.clear()
    run(notifier.send(phone(), pushes()['task']))
    first, second = (r.headers['authorization'] for r in apple.requests)
    assert first != second


# The payload -------------------------------------------------------------------------------


@pytest.mark.parametrize('category', ['approval', 'task', 'broke', 'digest', 'note'])
def test_each_category_is_sent_as_the_contract_maps_it(engine: Engine, category: str) -> None:
    n = pushes()[category]
    apple = FakeApple()

    async def go() -> None:
        notifier = apple.notifier(engine)
        await notifier.send(phone(), n)
        await notifier.aclose()

    run(go())
    request = apple.requests[0]
    assert request.method == 'POST'
    assert str(request.url) == f'{PRODUCTION}/3/device/{DEVICE_TOKEN}'
    assert request.headers['apns-topic'] == BUNDLE
    assert request.headers['apns-push-type'] == 'alert'
    assert request.headers['apns-collapse-id'] == n.collapse_key
    scheme, jwt = request.headers['authorization'].split(' ')
    assert scheme == 'bearer' and verify_jwt(jwt)[0]['kid'] == KEY_ID
    body = apple.bodies[0]
    assert body['aps'] == {
        'alert': {'title': n.title, 'body': n.body},
        'category': category,
        'sound': 'default',
    }
    beside = {k: v for k, v in body.items() if k != 'aps'}
    expected = {'deep_link': n.deep_link, 'level': n.level}
    expected.update({k: v for k in ('task_id', 'approval_id') if (v := getattr(n, k))})
    assert beside == expected


def test_an_approval_push_carries_the_ids_the_actions_answer_with() -> None:
    body = payload(pushes()['approval'])
    assert body['aps']['category'] == 'approval'  # the app's category with approve and deny
    assert body['approval_id'] == 'apr_1' and body['task_id'] == 'tsk_1'
    assert body['deep_link'] == 'marcel://approval/apr_1'


def test_a_push_without_a_collapse_key_has_no_collapse_id(engine: Engine) -> None:
    n = PushNotification(title='t', body='b', category='task', level='normal',
                         deep_link='marcel://status')  # fmt: skip
    assert 'apns-collapse-id' not in apple_notifier_headers(engine, n)


def test_a_long_collapse_key_is_hashed_to_apples_64_bytes(engine: Engine) -> None:
    n = pushes()['note'].model_copy(update={'collapse_key': 'k' * 100})
    other = pushes()['note'].model_copy(update={'collapse_key': 'k' * 99 + 'j'})
    first = apple_notifier_headers(engine, n)['apns-collapse-id']
    assert len(first.encode()) == 64
    assert first != apple_notifier_headers(engine, other)['apns-collapse-id']


def apple_notifier_headers(engine: Engine, n: PushNotification) -> dict[str, str]:
    return FakeApple().notifier(engine).headers(n)


def test_a_payload_over_4_kb_is_cut_in_the_body_and_keeps_its_ids() -> None:
    long_id = 'tsk_' + 'x' * 1800
    n = PushNotification(title='Done', body='é' * BODY_MAX, category='task', level='normal',
                         deep_link=f'marcel://task/{long_id}', task_id=long_id)  # fmt: skip
    body = payload(n)
    size = len(json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode())
    assert size <= PAYLOAD_MAX
    assert body['aps']['alert']['body'].endswith('…')
    assert body['aps']['alert']['body'].startswith('é')
    assert body['task_id'] == long_id and body['deep_link'] == f'marcel://task/{long_id}'


def test_a_payload_that_fits_is_not_cut() -> None:
    n = pushes()['task'].model_copy(update={'body': 'é' * BODY_MAX})
    assert payload(n)['aps']['alert']['body'] == 'é' * BODY_MAX


def test_a_payload_too_big_even_without_a_body_is_refused() -> None:
    huge = 'tsk_' + 'x' * 5000
    n = PushNotification(title='Done', body='b', category='task', level='normal',
                         deep_link=f'marcel://task/{huge}', task_id=huge)  # fmt: skip
    with pytest.raises(PushFailed) as err:
        payload(n)
    assert err.value.retry is False


def test_the_sandbox_setting_picks_apples_sandbox_server(engine: Engine) -> None:
    apple = FakeApple()
    sandbox = ApnsConfig(key=KEY_PEM, key_id=KEY_ID, team_id=TEAM_ID, topic=BUNDLE, sandbox=True)
    run(apple.notifier(engine, sandbox).send(phone(), pushes()['task']))
    assert str(apple.requests[0].url).startswith(SANDBOX + '/3/device/')
    assert CONFIG.server == PRODUCTION


def test_the_hub_talks_http2_to_apple(engine: Engine) -> None:
    notifier = ApnsNotifier(CONFIG, engine)
    assert notifier.client._transport._pool._http2 is True  # type: ignore[attr-defined]
    run(notifier.aclose())


# A device token Apple no longer knows ------------------------------------------------------


@pytest.mark.parametrize('answer', [(410, 'Unregistered'), (410, None), (400, 'BadDeviceToken')])
def test_a_gone_device_token_is_removed_and_the_device_stays_paired(
    engine: Engine, answer: tuple[int, str | None]
) -> None:
    add_device(engine, apns_token=DEVICE_TOKEN)
    apple = FakeApple(**{DEVICE_TOKEN: answer})
    with Session(engine) as s:
        device = s.get(Device, 'dev_phone')
    assert device is not None
    with pytest.raises(PushFailed) as err:
        run(apple.notifier(engine).send(device, pushes()['task']))
    assert err.value.retry is False
    assert 'dev_phone' in str(err.value) and DEVICE_TOKEN not in str(err.value)
    with Session(engine) as s:
        after = s.get(Device, 'dev_phone')
    assert after is not None, 'the device stays paired'
    assert after.apns_token is None
    assert after.ntfy_topic == TOPIC and after.token_hash == 'h-dev_phone'


def test_other_refusals_keep_the_device_token(engine: Engine) -> None:
    add_device(engine, apns_token=DEVICE_TOKEN)
    apple = FakeApple(**{DEVICE_TOKEN: (400, 'DeviceTokenNotForTopic')})
    with pytest.raises(PushFailed) as err:
        run(apple.notifier(engine).send(phone(), pushes()['task']))
    assert err.value.retry is False
    with Session(engine) as s:
        assert s.get(Device, 'dev_phone').apns_token == DEVICE_TOKEN  # type: ignore[union-attr]


def test_a_token_registered_again_meanwhile_is_kept(engine: Engine) -> None:
    add_device(engine, apns_token='new-token-from-the-app')
    apple = FakeApple(**{DEVICE_TOKEN: (410, 'Unregistered')})
    with pytest.raises(PushFailed):
        run(apple.notifier(engine).send(phone(), pushes()['task']))
    with Session(engine) as s:
        assert s.get(Device, 'dev_phone').apns_token == 'new-token-from-the-app'  # type: ignore[union-attr]


def test_a_device_without_an_apns_token_is_not_posted(engine: Engine) -> None:
    apple = FakeApple()
    with pytest.raises(PushFailed) as err:
        run(apple.notifier(engine).send(phone(token=''), pushes()['task']))
    assert err.value.retry is False and apple.requests == []


# Failures ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('answer', 'retry'),
    [
        ((429, 'TooManyRequests'), True),
        ((500, 'InternalServerError'), True),
        ((503, 'ServiceUnavailable'), True),
        ('down', True),
        ('timeout', True),
        ((403, 'InvalidProviderToken'), False),
        ((400, 'BadCollapseId'), False),
        ((413, None), False),
    ],
)
def test_a_failed_push_says_whether_to_try_again(engine: Engine, answer: Any, retry: bool) -> None:
    apple = FakeApple(**{DEVICE_TOKEN: answer})
    with pytest.raises(PushFailed) as err:
        run(apple.notifier(engine).send(phone(), pushes()['task']))
    assert err.value.retry is retry
    assert err.value.__cause__ is None
    assert err.value.__context__ is None or err.value.__suppress_context__


def test_an_odd_answer_body_does_not_reach_the_error(engine: Engine) -> None:
    def odd(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={'reason': 'not a code <script>'})

    def text(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text='<html>bad gateway</html>')

    for handler in (odd, text):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with pytest.raises(PushFailed) as err:
            run(ApnsNotifier(CONFIG, engine, client).send(phone(), pushes()['task']))
        assert '<' not in str(err.value)


def test_a_busy_apple_for_one_device_does_not_hold_up_another(engine: Engine) -> None:
    """429 for one phone is retried later in its own time; the other phone gets its push now."""
    add_agent(engine)
    add_device(engine, 'dev_a', apns_token='token-busy', ntfy_topic=None)
    add_device(engine, 'dev_b', apns_token='token-fine', ntfy_topic=None)
    apple = FakeApple(**{'token-busy': (429, 'TooManyRequests')})
    other_sent = asyncio.Event()
    waits: list[float] = []

    async def sleep(delay: float) -> None:
        waits.append(delay)
        await other_sent.wait()  # the busy device waits until the other one got its push

    def handle(request: httpx.Request) -> httpx.Response:
        res = apple.handle(request)
        if apple.sent:
            other_sent.set()
        return res

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    pusher = pusher_for(engine, ApnsNotifier(CONFIG, engine, client), sleep=sleep)
    task = put_task(engine, TaskState.needs_you)

    async def go() -> None:
        pusher.on_state(task, 'needs_you')
        await asyncio.wait_for(pusher.wait(), timeout=2)
        await pusher.aclose()

    run(go())
    # The fine phone got the push, and then "a notification did not arrive" about the busy one.
    assert apple.sent == ['token-fine', 'token-fine']
    assert apple.bodies[-1]['aps']['alert']['title'] == 'A notification did not arrive'
    order = [r.url.path.rsplit('/', 1)[-1] for r in apple.requests]
    busy = [i for i, t in enumerate(order) if t == 'token-busy']
    assert order.index('token-fine') < busy[1]  # the fine phone did not wait for the retry
    # Each push to the busy phone: three tries with a pause between, then given up.
    assert len(busy) == 6 and waits == [2.0, 10.0, 2.0, 10.0]


# No key configured -------------------------------------------------------------------------


@pytest.fixture
def ntfy_only(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db', ntfy_server=NTFY)


def test_no_key_means_no_apns_and_ntfy_keeps_working(
    ntfy_only: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[ApnsConfig] = []
    monkeypatch.setattr(notify, 'ApnsNotifier', lambda config, engine: built.append(config))
    ntfy = FakeNtfy()
    app = create_app(ntfy_only)
    with TestClient(app) as client:
        me = pair(client)
        body = client.get('/api/me', headers=bearer(me['token'])).json()
        pusher = app.state.pusher
        pusher.notifiers['ntfy'] = ntfy.notifier()
        add_device(app.state.engine, 'dev_x', apns_token=DEVICE_TOKEN, ntfy_topic=None)
        add_device(app.state.engine, 'dev_y', ntfy_topic=TOPIC)

        async def go() -> None:
            pusher.broke('runner_unreachable', 'NUC down', 'No answer.')
            await pusher.wait()

        client.portal.call(go)  # type: ignore[union-attr]
    validate('Me', body)
    assert body['push'] == {'apns': False, 'ntfy': True, 'ntfy_server': NTFY}
    assert built == [] and 'apns' not in pusher.notifiers  # nothing can reach APNs
    assert [b['topic'] for b in ntfy.bodies] == [TOPIC]  # the ntfy phone still gets it


def test_a_configured_key_turns_apns_on(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db', apns=CONFIG)
    app = create_app(settings)
    with TestClient(app) as client:
        me = pair(client)
        body = client.get('/api/me', headers=bearer(me['token'])).json()
        assert isinstance(app.state.pusher.notifiers['apns'], ApnsNotifier)
    validate('Me', body)
    assert body['push'] == {'apns': True, 'ntfy': False}


# Settings ----------------------------------------------------------------------------------


def env(tmp_path: Path, **values: str) -> dict[str, str]:
    return {'MARCEL_CONFIG': str(tmp_path / 'none.toml'), **values}


def full(**more: str) -> dict[str, str]:
    return {'MARCEL_APNS_KEY': KEY_PEM, 'MARCEL_APNS_KEY_ID': KEY_ID,
            'MARCEL_APNS_TEAM_ID': TEAM_ID, 'MARCEL_APNS_TOPIC': BUNDLE, **more}  # fmt: skip


def test_the_apns_settings_are_read_from_the_environment(tmp_path: Path) -> None:
    settings = load_settings(env(tmp_path, **full()))
    assert settings.apns == ApnsConfig(KEY_PEM.strip(), KEY_ID, TEAM_ID, BUNDLE)
    assert settings.apns is not None and settings.apns.server == PRODUCTION
    sandbox = load_settings(env(tmp_path, **full(MARCEL_APNS_SANDBOX='true')))
    assert sandbox.apns is not None and sandbox.apns.server == SANDBOX
    assert load_settings(env(tmp_path)).apns is None


@pytest.mark.parametrize(
    'key',
    [KEY_PEM.replace('\n', '\\n'), KEY_BODY, '  ' + KEY_PEM + '\n'],
    ids=['escaped', 'body', 'padded'],
)
def test_the_key_may_be_pasted_on_one_line(key: str) -> None:
    assert load_key(key).public_key() == _KEY.public_key()


def test_half_set_up_apns_stops_the_hub_naming_what_is_missing(tmp_path: Path) -> None:
    values = full()
    del values['MARCEL_APNS_TEAM_ID']
    with pytest.raises(SettingsError) as err:
        load_settings(env(tmp_path, **values))
    assert 'MARCEL_APNS_TEAM_ID' in str(err.value)
    assert KEY_ID not in str(err.value) and KEY_BODY[:20] not in str(err.value)


@pytest.mark.parametrize(
    'bad',
    [
        'not a key',
        ec.generate_private_key(ec.SECP384R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode(),
    ],
    ids=['garbage', 'wrong-curve'],
)
def test_a_key_that_is_not_a_p8_key_stops_the_hub_without_quoting_it(
    tmp_path: Path, bad: str
) -> None:
    with pytest.raises(SettingsError) as err:
        load_settings(env(tmp_path, **full(MARCEL_APNS_KEY=bad)))
    assert 'MARCEL_APNS_KEY' in str(err.value)
    assert bad not in str(err.value) and err.value.__cause__ is None
    assert err.value.__suppress_context__


def test_a_sandbox_flag_that_is_not_yes_or_no_stops_the_hub(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match='MARCEL_APNS_SANDBOX'):
        load_settings(env(tmp_path, **full(MARCEL_APNS_SANDBOX='maybe')))
    off = load_settings(env(tmp_path, **full(MARCEL_APNS_SANDBOX='0')))
    assert off.apns is not None and off.apns.sandbox is False


# Secrets -----------------------------------------------------------------------------------

SECRETS = (KEY_BODY[:40], KEY_BODY[-40:], KEY_ID, TEAM_ID, DEVICE_TOKEN)


def test_no_secret_is_in_a_repr(tmp_path: Path, engine: Engine) -> None:
    settings = load_settings(env(tmp_path, **full()))
    notifier = FakeApple().notifier(engine)
    shown = ' '.join(
        [repr(settings), repr(settings.apns), repr(notifier), repr(notifier.token), str(CONFIG)]
    )
    assert all(secret not in shown for secret in SECRETS)
    assert BUNDLE in shown  # the bundle id is not a secret


@pytest.mark.parametrize(
    'answer', [200, (410, 'Unregistered'), (500, 'InternalServerError'), 'down', 'timeout']
)
def test_no_secret_reaches_a_log(
    engine: Engine, caplog: pytest.LogCaptureFixture, answer: Any
) -> None:
    caplog.set_level(logging.DEBUG)  # every logger, httpx included
    add_agent(engine)
    add_device(engine, apns_token=DEVICE_TOKEN, ntfy_topic=None)
    apple = FakeApple(**{DEVICE_TOKEN: answer})
    notifier = apple.notifier(engine)
    pusher = pusher_for(engine, notifier)
    task = put_task(engine, TaskState.needs_you)

    async def go() -> None:
        pusher.on_state(task, 'needs_you')
        await pusher.wait()
        await pusher.aclose()

    run(go())
    assert apple.requests, 'the push was attempted'
    jwt = apple.requests[0].headers['authorization'].split(' ')[1]
    if answer == 200 or isinstance(answer, tuple):
        assert '/3/device/<token>' in caplog.text  # httpx logged the request, redacted
    for record in caplog.records:
        for secret in (*SECRETS, jwt):
            assert secret not in record.getMessage()
            assert secret not in str(record.args)
            assert secret not in str(record.exc_info)
    assert 'dev_phone' in caplog.text  # logs name the device instead


def test_the_http2_header_encoder_does_not_log_headers(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    import hpack

    caplog.set_level(logging.DEBUG)
    FakeApple().notifier(engine)  # sets the loggers up, as the hub does at start
    hpack.Encoder().encode([(':path', f'/3/device/{DEVICE_TOKEN}'), ('authorization', 'bearer x')])
    assert DEVICE_TOKEN not in caplog.text
