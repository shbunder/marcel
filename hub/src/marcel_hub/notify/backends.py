"""Push backends: the `Notifier` interface and the ntfy backend (B-18).

A backend sends one `PushNotification` to one device and raises `PushFailed` when it could not.
Retrying, rate limits and logging live in `pusher.py`, so a backend stays a thin HTTP call. APNs
(S-08.2) is a second class with `kind = 'apns'` that reads `device.apns_token`.

An ntfy topic is a secret: anyone who knows it can read the pushes. It never goes in a log line, an
exception message or a URL. The ntfy backend publishes as JSON to the server's root, so the topic
travels in the request body and not in the URL that httpx logs.
"""

from typing import ClassVar, Literal, Protocol

import httpx

from marcel_hub.models import Device
from marcel_hub.notify.message import PushNotification

Kind = Literal['apns', 'ntfy']

SEND_TIMEOUT = 10.0
# ntfy priorities: 3 is the default, 4 ("high") pops over the screen. 5 ("urgent") also vibrates
# for a long time; Marcel does not need that.
NTFY_PRIORITY = {'normal': 3, 'urgent': 4}


class PushFailed(Exception):
    """A push did not reach the backend. `retry` is False when trying again cannot help."""

    def __init__(self, message: str, retry: bool = True) -> None:
        super().__init__(message)
        self.retry = retry


class Notifier(Protocol):
    kind: ClassVar[Kind]

    async def send(self, device: Device, notification: PushNotification) -> None:
        """Send one push to one device. Raises `PushFailed`."""
        ...

    async def aclose(self) -> None: ...


def target_kind(device: Device) -> Kind | None:
    """Which backend a device registered for, if any (`PUT /devices/{id}/push`)."""
    if device.apns_token:
        return 'apns'
    if device.ntfy_topic:
        return 'ntfy'
    return None


class NtfyNotifier:
    """POSTs to an ntfy server: Title, the message, Tags, Priority and Click = the deep link.

    Only what the contract maps goes out. No device token ever passes through the ntfy server:
    the push only opens the app at the deep link, and the app uses its own token from there.
    """

    kind: ClassVar[Kind] = 'ntfy'

    def __init__(self, server: str, client: httpx.AsyncClient | None = None) -> None:
        self.server = server.rstrip('/')
        self.client = client or httpx.AsyncClient(timeout=SEND_TIMEOUT)

    def payload(self, topic: str, n: PushNotification) -> dict[str, object]:
        return {
            'topic': topic,
            'title': n.title,
            'message': n.body,
            'tags': [n.category],
            'priority': NTFY_PRIORITY[n.level],
            'click': n.deep_link,
        }

    async def send(self, device: Device, notification: PushNotification) -> None:
        if not device.ntfy_topic:
            raise PushFailed(f'Device {device.id} has no ntfy topic.', retry=False)
        try:
            res = await self.client.post(
                self.server + '/', json=self.payload(device.ntfy_topic, notification)
            )
        except httpx.HTTPError as exc:  # the server is down, slow or unreachable
            raise PushFailed(f'The ntfy server {self.server} did not answer ({exc}).') from None
        if res.status_code >= 400:
            # 429 and 5xx may pass; any other 4xx is a request ntfy will refuse again.
            retry = res.status_code == 429 or res.status_code >= 500
            raise PushFailed(
                f'The ntfy server {self.server} refused the push ({res.status_code}).', retry
            )

    async def aclose(self) -> None:
        await self.client.aclose()
