"""The APNs backend (S-08.2): token-based pushes to Apple over HTTP/2.

- **Settings.** `ApnsConfig.from_values` reads `apns_key` (the `.p8` key's text), `apns_key_id`,
  `apns_team_id`, `apns_topic` (the app's bundle id) and `apns_sandbox` from the hub's settings,
  normally `MARCEL_APNS_*` in `deploy/.env.local`. None of them set: no APNs backend, `GET /me`
  reports `push.apns: false` and the app registers an ntfy topic instead.
- **The provider token.** An ES256 JWT signed with the key, cached and made again every
  `TOKEN_SECONDS` (Apple refuses one older than an hour, and one made more often than every
  20 minutes). Apple saying it expired drops it, so the next try makes a fresh one.
- **The payload.** `title` and `body` in `aps.alert`, the category as `aps.category` (the app gives
  the `approval` category its `approve` and `deny` actions), `collapse_key` as `apns-collapse-id`,
  and `deep_link`, `level`, `task_id` and `approval_id` beside `aps`. Over `PAYLOAD_MAX` bytes the
  body is shortened with `…`; the ids are kept.
- **A token Apple no longer knows** (410 Unregistered, 400 BadDeviceToken) is removed from the
  device. The device stays paired; the app registers a new token on its next launch.
- **Failures.** 429, 5xx and an expired provider token raise `PushFailed(retry=True)`: the pusher
  tries again later, per device, in the background, so other pushes go out meanwhile.
- **Secrets.** The key, its id and the team id are never in a log line, an error or a repr. The
  device token sits in the request path, so httpx's request log line is redacted here and the
  HTTP/2 header encoder's debug log is turned off.
"""

import base64
import hashlib
import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlmodel import Session, col

from marcel_hub.models import Device
from marcel_hub.notify.backends import SEND_TIMEOUT, Kind, PushFailed
from marcel_hub.notify.message import PushNotification

PRODUCTION = 'https://api.push.apple.com'
SANDBOX = 'https://api.sandbox.push.apple.com'
TOKEN_SECONDS = 50 * 60  # Apple: older than 60 min is refused; new more often than 20 min is too
PAYLOAD_MAX = 4096  # bytes, Apple's limit for an alert push
COLLAPSE_ID_MAX = 64  # bytes
# A token Apple will never accept again: drop it from the device.
GONE_REASONS = frozenset({'Unregistered', 'BadDeviceToken'})

_DEVICE_PATH = re.compile(r'/3/device/[^\s"\'?]+')


class _RedactDeviceTokens(logging.Filter):
    """httpx logs every request's URL at INFO; an APNs URL carries the device token."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if '/3/device/' in message:
            record.msg = _DEVICE_PATH.sub('/3/device/<token>', message)
            record.args = ()
        return True


_REDACT = _RedactDeviceTokens()


def _quiet_secret_loggers() -> None:
    """Keep the device token and the provider token out of the hub's log."""
    httpx_log = logging.getLogger('httpx')
    if _REDACT not in httpx_log.filters:
        httpx_log.addFilter(_REDACT)
    # hpack logs every HTTP/2 header it encodes (the path, the authorization) at DEBUG.
    logging.getLogger('hpack').setLevel(logging.WARNING)


class ApnsSettingsError(ValueError):
    """The APNs settings are incomplete or the key cannot be read. Never quotes a value."""


@dataclass(frozen=True)
class ApnsConfig:
    key: str = field(repr=False)
    key_id: str = field(repr=False)
    team_id: str = field(repr=False)
    topic: str
    sandbox: bool = False

    NAMES: ClassVar[tuple[str, ...]] = ('apns_key', 'apns_key_id', 'apns_team_id', 'apns_topic')

    @classmethod
    def from_values(cls, values: Mapping[str, Any]) -> 'ApnsConfig | None':
        """The APNs settings, or None when none is set. Half set raises `ApnsSettingsError`."""
        given = {n: str(values.get(n) or '').strip() for n in cls.NAMES}
        if not any(given.values()):
            return None
        missing = [n for n, v in given.items() if not v]
        if missing:
            names = ', '.join(f'MARCEL_{n.upper()}' for n in missing)
            raise ApnsSettingsError(
                f'APNs is half set up: {names} is missing. Set all four, or none to send '
                'pushes through ntfy only.'
            )
        config = cls(
            key=given['apns_key'],
            key_id=given['apns_key_id'],
            team_id=given['apns_team_id'],
            topic=given['apns_topic'],
            sandbox=_flag(values.get('apns_sandbox', False)),
        )
        load_key(config.key)  # a key that cannot be read stops the hub at start, not at a push
        return config

    @property
    def server(self) -> str:
        return SANDBOX if self.sandbox else PRODUCTION


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ('1', 'true', 'yes', 'on'):
        return True
    if text in ('', '0', 'false', 'no', 'off'):
        return False
    raise ApnsSettingsError('MARCEL_APNS_SANDBOX must be true or false.')


def load_key(text: str) -> ec.EllipticCurvePrivateKey:
    """Read a `.p8` key: the whole file, the file with `\\n` for line breaks, or only its body."""
    pem = text.strip().replace('\\n', '\n')
    if 'BEGIN' not in pem:
        body = ''.join(pem.split())
        lines = [body[i : i + 64] for i in range(0, len(body), 64)]
        pem = '\n'.join(['-----BEGIN PRIVATE KEY-----', *lines, '-----END PRIVATE KEY-----'])
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError):
        key = None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != 'secp256r1':
        raise ApnsSettingsError(
            'MARCEL_APNS_KEY is not an APNs .p8 key. Paste the whole AuthKey_….p8 file from '
            'the Apple developer site.'
        ) from None
    return key


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


class ProviderToken:
    """The ES256 JWT Apple wants in `authorization`, made again every `TOKEN_SECONDS`."""

    def __init__(
        self,
        key: ec.EllipticCurvePrivateKey,
        key_id: str,
        team_id: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._key = key
        self._key_id = key_id
        self._team_id = team_id
        self._clock = clock
        self._token: str | None = None
        self._made_at = 0.0

    def __repr__(self) -> str:
        return 'ProviderToken(<secret>)'

    def get(self) -> str:
        now = self._clock()
        if self._token is None or now - self._made_at >= TOKEN_SECONDS:
            self._token = self._sign(int(now))
            self._made_at = now
        return self._token

    def expire(self) -> None:
        """Apple said the token expired: the next `get` makes a new one."""
        self._token = None

    def _sign(self, issued_at: int) -> str:
        header = {'alg': 'ES256', 'kid': self._key_id}
        claims = {'iss': self._team_id, 'iat': issued_at}
        signing_input = '.'.join(
            _b64(json.dumps(part, separators=(',', ':')).encode()) for part in (header, claims)
        )
        der = self._key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return f'{signing_input}.{_b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))}'


def payload(n: PushNotification) -> dict[str, Any]:
    """The APNs JSON for `n`, shortened to `PAYLOAD_MAX` bytes by cutting the body."""
    body = n.body
    while True:
        data: dict[str, Any] = {
            'aps': {
                'alert': {'title': n.title, 'body': body},
                'category': n.category,
                'sound': 'default',
            },
            'deep_link': n.deep_link,
            'level': n.level,
        }
        if n.task_id:
            data['task_id'] = n.task_id
        if n.approval_id:
            data['approval_id'] = n.approval_id
        over = len(encode(data)) - PAYLOAD_MAX
        if over <= 0:
            return data
        if not body:
            raise PushFailed(
                f'The "{n.category}" push is over {PAYLOAD_MAX} bytes even without a body.',
                retry=False,
            )
        # Cut at least as many characters as there are bytes too many (a character is ≥ 1 byte).
        keep = max(len(body.rstrip('…')) - over - 1, 0)
        body = body[:keep].rstrip() + '…' if keep else ''


def encode(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()


def collapse_id(key: str) -> str:
    """Apple allows 64 bytes; a longer key is replaced by its hash (still the same per key)."""
    if len(key.encode()) <= COLLAPSE_ID_MAX:
        return key
    return hashlib.sha256(key.encode()).hexdigest()[:COLLAPSE_ID_MAX]


class ApnsNotifier:
    """POSTs to `/3/device/<token>` on Apple's push server, production or sandbox."""

    kind: ClassVar[Kind] = 'apns'

    def __init__(
        self,
        config: ApnsConfig,
        engine: Engine,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        _quiet_secret_loggers()
        self.server = config.server
        self.topic = config.topic
        self.engine = engine
        self.token = ProviderToken(load_key(config.key), config.key_id, config.team_id, clock)
        self.client = client or httpx.AsyncClient(http2=True, timeout=SEND_TIMEOUT)

    def __repr__(self) -> str:
        return f'ApnsNotifier({self.server}, topic={self.topic})'

    def headers(self, n: PushNotification) -> dict[str, str]:
        headers = {
            'authorization': f'bearer {self.token.get()}',
            'apns-topic': self.topic,
            'apns-push-type': 'alert',
            'apns-priority': '10',
        }
        if n.collapse_key:
            headers['apns-collapse-id'] = collapse_id(n.collapse_key)
        return headers

    async def send(
        self, device: Device, notification: PushNotification, agent_id: str | None = None
    ) -> None:
        token = device.apns_token
        if not token:
            raise PushFailed(f'Device {device.id} has no APNs token.', retry=False)
        content = encode(payload(notification))
        try:
            res = await self.client.post(
                f'{self.server}/3/device/{token}',
                content=content,
                headers=self.headers(notification),
            )
        except httpx.TimeoutException:
            raise PushFailed(f'Apple did not answer within {SEND_TIMEOUT:g} s.') from None
        except httpx.HTTPError as exc:  # no network, or Apple unreachable
            raise PushFailed(f'Apple could not be reached ({type(exc).__name__}).') from None
        if res.status_code == 200:
            return
        reason = _reason(res)
        if res.status_code == 410 or reason in GONE_REASONS:
            self._forget(device.id, token)
            raise PushFailed(
                f'Apple no longer knows device {device.id} ({reason or res.status_code}). Its '
                'APNs token was removed; the app registers a new one when it next opens.',
                retry=False,
            )
        if reason == 'ExpiredProviderToken':
            self.token.expire()
            raise PushFailed('Apple said the provider token expired; a new one is made.')
        retry = res.status_code == 429 or res.status_code >= 500
        why = f'{res.status_code} {reason}' if reason else str(res.status_code)
        raise PushFailed(f'Apple refused the push ({why}).', retry)

    def _forget(self, device_id: str, token: str) -> None:
        """Drop the token, unless the app registered a new one meanwhile.

        One conditional UPDATE, so a token the app registers between Apple's answer and here is
        never wiped (no read, compare, then write)."""
        with Session(self.engine) as s:
            s.exec(
                update(Device)
                .where(col(Device.id) == device_id)
                .where(col(Device.apns_token) == token)
                .values(apns_token=None)
            )
            s.commit()

    async def aclose(self) -> None:
        await self.client.aclose()


def _reason(res: httpx.Response) -> str:
    try:
        reason = res.json().get('reason', '')
    except (ValueError, AttributeError):
        return ''
    # Only Apple's short reason codes, so nothing else from the answer reaches a log.
    return reason if isinstance(reason, str) and reason.isalnum() else ''
