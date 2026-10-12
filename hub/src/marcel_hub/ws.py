"""The app WebSocket, `GET /api/ws?since=<event id>` (`contracts/events.schema.json`).

Every event is stored in `app_event` before it is sent, with an `id` one higher than the user's
last. Each paired device keeps one socket open; `EventLog.publish` sends the event to every socket
of that user. A phone that reconnects with `?since=` gets the events it missed, in order. If the
hub no longer has them (only the latest `keep` are stored), or never sent that id, the first frame
is `resync.required` and the app re-fetches over REST.

Other stories publish through `request.app.state.events.publish(session, user_id, type, data)`.
"""

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.routing import APIRouter
from sqlalchemy import delete, func
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from marcel_hub.auth.tokens import hash_secret
from marcel_hub.models import AppEvent, Device, utcnow

# Enough for a phone that was offline for a day or two; older gaps ask for a resync.
KEEP_EVENTS = 2000

# Close code for a refused or revoked device. Before `accept()` the server answers 403.
POLICY_VIOLATION = 1008

Frame = dict[str, Any]


def frame_of(event: AppEvent) -> Frame:
    return {'id': event.id, 'type': event.type, 'at': event.at.isoformat(), 'data': event.data_json}


def control(type_: str, data: dict[str, Any]) -> Frame:
    """`hello` and `resync.required`: id 0, not part of the event sequence."""
    return {'id': 0, 'type': type_, 'at': utcnow().isoformat(), 'data': data}


@dataclass(eq=False)
class Listener:
    """One open socket. Frames are handed to its own event loop, from any thread."""

    device_id: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[Frame] = field(default_factory=asyncio.Queue)

    def push(self, frame: Frame) -> bool:
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, frame)
        except RuntimeError:  # the loop is closed: the socket is gone
            return False
        return True


class EventLog:
    """Stores app events and fans them out to every open socket of the user.

    One lock covers numbering, storing and handing out, so ids never collide, and every socket
    gets events in id order with no gap between its replay and the live feed.
    """

    def __init__(self, engine: Engine, keep: int = KEEP_EVENTS) -> None:
        self.engine = engine
        self.keep = keep
        # Re-entrant: a caller may hold it around a read-then-write (client_id checks).
        self.lock = threading.RLock()
        self._listeners: dict[str, set[Listener]] = {}

    def publish(self, session: Session, user_id: str, type_: str, data: dict[str, Any]) -> Frame:
        """Store the event and commit `session` (with whatever the caller added to it), then send
        it to every socket of `user_id`."""
        with self.lock:
            last = session.exec(
                select(func.max(AppEvent.id)).where(AppEvent.user_id == user_id)
            ).one()
            event = AppEvent(user_id=user_id, id=(last or 0) + 1, type=type_, data_json=data)
            session.add(event)
            session.exec(  # type: ignore[call-overload]
                delete(AppEvent).where(
                    col(AppEvent.user_id) == user_id, col(AppEvent.id) <= event.id - self.keep
                )
            )
            session.commit()
            frame = frame_of(event)
            listeners = self._listeners.get(user_id, set())
            for listener in list(listeners):
                if not listener.push(frame):
                    listeners.discard(listener)
            return frame

    def subscribe(
        self, user_id: str, since: int | None, device_id: str = ''
    ) -> tuple[Listener, list[Frame]]:
        """Register a socket and return the frames to send first: `hello` and the replay, or
        `resync.required`. Call from the socket's event loop."""
        listener = Listener(device_id, asyncio.get_running_loop())
        with self.lock, Session(self.engine) as session:
            first, last = session.exec(
                select(func.min(AppEvent.id), func.max(AppEvent.id)).where(
                    AppEvent.user_id == user_id
                )
            ).one()
            last = last or 0
            gone = first is not None and since is not None and since < first - 1
            if since is not None and (since < 0 or since > last or gone):
                head = [control('resync.required', {'last_event_id': last})]
            else:
                replay = (
                    session.exec(
                        select(AppEvent)
                        .where(AppEvent.user_id == user_id, col(AppEvent.id) > since)
                        .order_by(col(AppEvent.id))
                    ).all()
                    if since is not None
                    else []
                )
                head = [
                    control('hello', {'last_event_id': last, 'replayed': len(replay)}),
                    *(frame_of(e) for e in replay),
                ]
            self._listeners.setdefault(user_id, set()).add(listener)
        return listener, head

    def unsubscribe(self, user_id: str, listener: Listener) -> None:
        with self.lock:
            self._listeners.get(user_id, set()).discard(listener)

    def listeners(self, user_id: str) -> int:
        return len(self._listeners.get(user_id, ()))


def paired_device(engine: Engine, authorization: str | None) -> Device | None:
    """The device behind `Authorization: Bearer <token>`, as for REST; None if not paired."""
    scheme, _, token = (authorization or '').partition(' ')
    if scheme.lower() != 'bearer' or not token.strip():
        return None
    with Session(engine) as session:
        return session.exec(
            select(Device).where(Device.token_hash == hash_secret(token.strip()))
        ).first()


def still_paired(engine: Engine, device_id: str) -> bool:
    with Session(engine) as session:
        return session.get(Device, device_id) is not None


def parse_since(raw: str | None) -> int | None:
    """None when absent. Anything that is not a whole number becomes -1, which asks for a
    resync: the app's copy is not trustworthy, but it is still paired."""
    if raw is None:
        return None
    return int(raw) if raw.isdigit() else -1


router = APIRouter(prefix='/api')


@router.websocket('/ws')
async def event_stream(ws: WebSocket) -> None:
    engine: Engine = ws.app.state.engine
    events: EventLog = ws.app.state.events
    device = paired_device(engine, ws.headers.get('authorization'))
    if device is None:
        await ws.close(code=POLICY_VIOLATION)
        return
    await ws.accept()
    listener, head = events.subscribe(
        device.user_id, parse_since(ws.query_params.get('since')), device.id
    )
    # The app sends nothing but pings; reading is how a closed socket is noticed.
    incoming = asyncio.ensure_future(ws.receive())
    try:
        for frame in head:
            await ws.send_json(frame)
        while True:
            nxt = asyncio.ensure_future(listener.queue.get())
            await asyncio.wait({nxt, incoming}, return_when=asyncio.FIRST_COMPLETED)
            if nxt.done():
                # A device removed while connected stops getting events at once, like its token.
                if not still_paired(engine, device.id):
                    await ws.close(code=POLICY_VIOLATION)
                    return
                await ws.send_json(nxt.result())
            else:
                nxt.cancel()  # nothing was taken from the queue
            if incoming.done():
                if incoming.result()['type'] == 'websocket.disconnect':
                    return
                incoming = asyncio.ensure_future(ws.receive())
    except WebSocketDisconnect:
        return
    finally:
        incoming.cancel()
        events.unsubscribe(device.user_id, listener)


def install(app: FastAPI) -> None:
    app.state.events = EventLog(app.state.engine)
    app.include_router(router)
