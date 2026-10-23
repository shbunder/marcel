"""The relay backend (S-22.15): pushes through the Marcel push relay (`contracts/push-relay.yaml`).

- **Always on.** No hub setting: the relay's address comes from the phone, with the rest of its
  target (`PUT /devices/{id}/push` `kind: relay`: `relay_url`, `handle`, `send_secret`,
  `device_key`). `GET /me` says `push.relay: true`.
- **Sealed.** Each push is a `SealedPushContent` (`title`, `body`, `link`, the buddy as `agent_id`,
  `category`) sealed for the phone's X25519 key exactly as app-api.yaml `SealedPush` says
  (`seal`). The relay gets the handle, the sealed bytes and `urgent`; it has no key that opens
  them, and never sees the text, the link, the buddy or the ids.
- **Gone.** The relay answering 410 (`handle_gone`: Apple no longer knows the phone) clears the
  phone's relay target, unless the phone registered a new one meanwhile. `Device.push` then reads
  `none`, and the app registers again.
- **Failures.** No answer, a timeout and 5xx (502 `apns_unavailable`) raise
  `PushFailed(retry=True)`: the pusher tries again as for ntfy and APNs. 400, 401, 413 and 429
  (the day's 500 pushes are used) are not retried: trying again within seconds cannot help.
- **Secrets.** The send secret travels only as the bearer header to the relay, which is the
  phone's own choice of server. Logs and errors name the device id, never the handle, the secret
  or the key; nothing logs a push's text.
"""

import base64
import json
import os
from typing import Any, ClassVar

import httpx
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlmodel import Session, col

from marcel_hub.models import Device
from marcel_hub.notify.backends import SEND_TIMEOUT, Kind, PushFailed
from marcel_hub.notify.message import PushNotification

INFO = b'marcel-push-v1'  # the HKDF info and the AEAD associated data (app-api.yaml SealedPush)
VERSION = 1
NONCE_BYTES = 12


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def sealed_content(n: PushNotification, agent_id: str | None) -> dict[str, str]:
    """The contract's `SealedPushContent` for `n`, in its order; `agent_id` left out when None."""
    content = {'title': n.title, 'body': n.body, 'link': n.deep_link}
    if agent_id is not None:
        content['agent_id'] = agent_id
    content['category'] = n.category
    return content


def seal(
    content: dict[str, Any],
    device_key: str,
    *,
    eph: X25519PrivateKey | None = None,
    nonce: bytes | None = None,
) -> dict[str, Any]:
    """Seal `content` for the phone whose X25519 public key is `device_key` (base64url), as
    app-api.yaml `SealedPush` says. `eph` and `nonce` are fresh unless a test fixes them."""
    eph = eph or X25519PrivateKey.generate()
    nonce = nonce if nonce is not None else os.urandom(NONCE_BYTES)
    phone = _unb64(device_key)
    eph_public = eph.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    shared = eph.exchange(X25519PublicKey.from_public_bytes(phone))
    key = HKDF(algorithm=SHA256(), length=32, salt=eph_public + phone, info=INFO).derive(shared)
    plain = json.dumps(content, separators=(',', ':'), ensure_ascii=False).encode()
    box = ChaCha20Poly1305(key).encrypt(nonce, plain, INFO)
    return {'v': VERSION, 'eph': _b64(eph_public), 'nonce': _b64(nonce), 'box': _b64(box)}


class RelayNotifier:
    """POSTs `{handle, sealed, urgent}` to `<relay_url>/v1/push` with the handle's send secret."""

    kind: ClassVar[Kind] = 'relay'

    def __init__(self, engine: Engine, client: httpx.AsyncClient | None = None) -> None:
        self.engine = engine
        self.client = client or httpx.AsyncClient(timeout=SEND_TIMEOUT)

    def __repr__(self) -> str:
        return 'RelayNotifier()'

    async def send(
        self, device: Device, notification: PushNotification, agent_id: str | None = None
    ) -> None:
        url, handle, secret, key = (device.relay_url, device.relay_handle, device.relay_secret,
                                    device.relay_device_key)  # fmt: skip
        if not (url and handle and secret and key):
            raise PushFailed(f'Device {device.id} has no push relay target.', retry=False)
        try:
            sealed = seal(sealed_content(notification, agent_id), key)
        except ValueError:  # not a usable X25519 key (e.g. all zeros): no push can open
            raise PushFailed(
                f'Device {device.id} gave a public key no push can be sealed for; the app must '
                'register again.',
                retry=False,
            ) from None
        body = {'handle': handle, 'sealed': sealed, 'urgent': notification.level == 'urgent'}
        try:
            res = await self.client.post(
                url.rstrip('/') + '/v1/push',
                json=body,
                headers={'authorization': f'Bearer {secret}'},
            )
        except httpx.TimeoutException:
            raise PushFailed(
                f'The push relay did not answer within {SEND_TIMEOUT:g} s (device {device.id}).'
            ) from None
        except httpx.HTTPError as exc:  # the relay is down or unreachable
            raise PushFailed(
                f'The push relay could not be reached ({type(exc).__name__}, device {device.id}).'
            ) from None
        if res.status_code < 300:
            return
        if res.status_code == 410:
            self._forget(device.id, handle)
            raise PushFailed(
                f'The push relay says Apple no longer knows device {device.id}. Its relay target '
                'was removed; the app registers again when it next opens.',
                retry=False,
            )
        raise PushFailed(
            f'The push relay refused the push to device {device.id} ({res.status_code}'
            f'{_code(res)}).',
            retry=res.status_code >= 500,
        )

    def _forget(self, device_id: str, handle: str) -> None:
        """Clear the relay target, unless the phone registered a new handle meanwhile: one
        conditional UPDATE, as `ApnsNotifier._forget`."""
        with Session(self.engine) as s:
            s.exec(
                update(Device)
                .where(col(Device.id) == device_id)
                .where(col(Device.relay_handle) == handle)
                .values(relay_url=None, relay_handle=None, relay_secret=None, relay_device_key=None)
            )
            s.commit()

    async def aclose(self) -> None:
        await self.client.aclose()


def _code(res: httpx.Response) -> str:
    """The relay's error code (`Error.code`), when it is a plain word: nothing else of the answer
    reaches a log."""
    try:
        code = res.json().get('code', '')
    except (ValueError, AttributeError):
        return ''
    return f' {code}' if isinstance(code, str) and code.replace('_', '').isalnum() else ''
