"""Fakes for the push tests. Nothing here reaches the network: ntfy is an httpx mock transport."""

import json
from typing import Any, ClassVar

import httpx
from sqlalchemy.engine import Engine
from sqlmodel import Session

from marcel_hub.models import Approval, Device
from marcel_hub.notify import NtfyNotifier, Pusher, PushFailed, PushNotification
from marcel_hub.notify.backends import Kind

NTFY = 'https://ntfy.test'
TOPIC = 'marcel-secret-topic-7f3a9c'


class FakeNtfy:
    """An ntfy server. `mode`: 'ok', 'down' (connection refused) or an HTTP status to answer."""

    def __init__(self, mode: str | int = 'ok') -> None:
        self.mode = mode
        self.requests: list[httpx.Request] = []

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.mode == 'down':
            raise httpx.ConnectError('Connection refused', request=request)
        if isinstance(self.mode, int):
            return httpx.Response(self.mode, json={'error': 'no'})
        return httpx.Response(200, json={'id': 'abc', 'event': 'message'})

    def notifier(self) -> NtfyNotifier:
        return NtfyNotifier(NTFY, httpx.AsyncClient(transport=httpx.MockTransport(self.handle)))


class RecordingNotifier:
    """A backend that records what it was asked to send, and fails while `fail` is set."""

    kind: ClassVar[Kind] = 'ntfy'

    def __init__(self, fail: PushFailed | Exception | None = None) -> None:
        self.sent: list[tuple[str, PushNotification]] = []
        self.tries = 0
        self.fail = fail
        self.closed = False

    async def send(self, device: Device, notification: PushNotification) -> None:
        self.tries += 1
        if self.fail is not None:
            raise self.fail
        self.sent.append((device.id, notification))

    async def aclose(self) -> None:
        self.closed = True


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def add_device(
    engine: Engine,
    device_id: str = 'dev_phone',
    user_id: str = 'usr_owner',  # one-person tests: the owner's phone (B-38)
    ntfy_topic: str | None = TOPIC,
    apns_token: str | None = None,
) -> None:
    with Session(engine) as s:
        s.add(Device(id=device_id, user_id=user_id, name='Phone', token_hash=f'h-{device_id}',
                     ntfy_topic=ntfy_topic, apns_token=apns_token))  # fmt: skip
        s.commit()


def add_approval(engine: Engine, task_id: str, approval_id: str = 'apr_1') -> str:
    with Session(engine) as s:
        s.add(Approval(id=approval_id, task_id=task_id, tool='Bash',
                       summary='allow `docker system df -v`?'))  # fmt: skip
        s.commit()
    return approval_id


def pusher_for(engine: Engine, *notifiers: Any, **kw: Any) -> Pusher:
    kw.setdefault('sleep', Sleeps())
    kw.setdefault('clock', Clock())
    return Pusher(engine, notifiers, **kw)
