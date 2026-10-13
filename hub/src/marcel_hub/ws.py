"""The app WebSocket, `GET /api/ws?since=<event id>` (`contracts/events.schema.json`).

Every event is stored in `app_event` before it is sent, with an `id` one higher than the user's
last. Each paired device keeps one socket open; `EventLog.publish` sends the event to every socket
of that user. A phone that reconnects with `?since=` gets the events it missed, in order. If the
hub no longer has them (only the latest `keep` are stored), or never sent that id, the first frame
is `resync.required` and the app re-fetches over REST.

A phone that stays connected but stops reading cannot make the hub hold events for it forever: once
`MAX_PENDING` events wait for one socket, the hub closes it (code 1013, try again later) and the
phone reconnects with `?since=`, getting the replay from the database or a resync.

Other stories publish through `request.app.state.events.publish(session, user_id, type, data)`.
"""

import asyncio
import contextlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.routing import APIRouter
from sqlalchemy import delete, func
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from marcel_hub.auth.tokens import OWNER_USER_ID, hash_secret
from marcel_hub.models import Agent, AppEvent, Device, Project, Task, utcnow

# Enough for a phone that was offline for a day or two; older gaps ask for a resync.
KEEP_EVENTS = 2000

# Close code for a refused or revoked device. Before `accept()` the server answers 403.
POLICY_VIOLATION = 1008
# Events waiting for one socket before the hub gives up on it and closes it.
MAX_PENDING = 2000
# Close code for a socket that fell too far behind: reconnect with `?since=`.
TRY_AGAIN_LATER = 1013

Frame = dict[str, Any]


def frame_of(event: AppEvent) -> Frame:
    return {'id': event.id, 'type': event.type, 'at': event.at.isoformat(), 'data': event.data_json}


def control(type_: str, data: dict[str, Any]) -> Frame:
    """`hello` and `resync.required`: id 0, not part of the event sequence."""
    return {'id': 0, 'type': type_, 'at': utcnow().isoformat(), 'data': data}


@dataclass(eq=False)
class Listener:
    """One open socket. Frames are handed to its own event loop, from any thread.

    At most `MAX_PENDING` frames wait; one more sets `overflowed`, and the socket is closed."""

    device_id: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[Frame] = field(default_factory=lambda: asyncio.Queue(MAX_PENDING))
    overflowed: asyncio.Event = field(default_factory=asyncio.Event)

    def push(self, frame: Frame) -> bool:
        try:
            self.loop.call_soon_threadsafe(self._deliver, frame)
        except RuntimeError:  # the loop is closed: the socket is gone
            return False
        return True

    def _deliver(self, frame: Frame) -> None:
        if self.overflowed.is_set():
            return
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            self.overflowed.set()


class EventLog:
    """Stores app events and fans them out to every open socket of the user.

    One lock covers numbering, storing and handing out, so ids never collide, and every socket
    gets events in id order with no gap between its replay and the live feed. (SQLite on its own
    does not give that: two writers can read the same last id, and a later event can be handed
    out before an earlier one.) `pause` is called at each step, holding the lock; tests use it
    to hold one step open and prove that nothing slips in.
    """

    def __init__(self, engine: Engine, keep: int = KEEP_EVENTS) -> None:
        self.engine = engine
        self.keep = keep
        # Re-entrant: a caller may hold it around a read-then-write (client_id checks).
        self.lock = threading.RLock()
        self._listeners: dict[str, set[Listener]] = {}
        self.pause: Callable[[str], None] = lambda _step: None
        # Called with (user_id, type, data) after each event is stored and sent, still under the
        # lock. The channel gateway uses it to hand new user messages to the brain's session.
        self.observers: list[Callable[[str, str, dict[str, Any]], None]] = []

    def publish(self, session: Session, user_id: str, type_: str, data: dict[str, Any]) -> Frame:
        """Store the event and commit `session` (with whatever the caller added to it), then send
        it to every socket of `user_id`."""
        with self.lock:
            last = session.exec(
                select(func.max(AppEvent.id)).where(AppEvent.user_id == user_id)
            ).one()
            event = AppEvent(user_id=user_id, id=(last or 0) + 1, type=type_, data_json=data)
            self.pause('numbered')
            session.add(event)
            session.exec(  # type: ignore[call-overload]
                delete(AppEvent).where(
                    col(AppEvent.user_id) == user_id, col(AppEvent.id) <= event.id - self.keep
                )
            )
            session.commit()
            frame = frame_of(event)
            self.pause('stored')
            listeners = self._listeners.get(user_id, set())
            for listener in list(listeners):
                if not listener.push(frame):
                    listeners.discard(listener)
            for observe in self.observers:
                observe(user_id, type_, data)
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
            self.pause('replayed')
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
    # isdigit() alone accepts '²' and '١٢', which int() then refuses or reads as another number.
    return int(raw) if raw.isascii() and raw.isdigit() else -1


router = APIRouter(prefix='/api')


async def send_frame(ws: WebSocket, frame: Frame) -> None:
    """Every frame leaves through here (tests replace it with a phone that stops reading)."""
    await ws.send_json(frame)


async def pump(
    ws: WebSocket, engine: Engine, device_id: str, listener: Listener, head: list[Frame]
) -> None:
    for frame in head:
        await send_frame(ws, frame)
    while True:
        frame = await listener.queue.get()
        # A device removed while connected stops getting events at once, like its token.
        if not still_paired(engine, device_id):
            await ws.close(code=POLICY_VIOLATION)
            return
        await send_frame(ws, frame)


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
    sending = asyncio.ensure_future(pump(ws, engine, device.id, listener, head))
    behind = asyncio.ensure_future(listener.overflowed.wait())
    # The app sends nothing but pings; reading is how a closed socket is noticed.
    incoming = asyncio.ensure_future(ws.receive())
    try:
        while True:
            await asyncio.wait({sending, behind, incoming}, return_when=asyncio.FIRST_COMPLETED)
            if behind.done():
                # Too far behind: stop sending and let the phone catch up with `?since=`.
                sending.cancel()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(ws.close(code=TRY_AGAIN_LATER), timeout=1)
                return
            if sending.done():
                # Closed for a revoked device, or the phone went away mid-send.
                with contextlib.suppress(WebSocketDisconnect):
                    sending.result()
                return
            if incoming.result()['type'] == 'websocket.disconnect':
                return
            incoming = asyncio.ensure_future(ws.receive())
    except WebSocketDisconnect:
        return
    finally:
        for task in (sending, behind, incoming):
            task.cancel()
        events.unsubscribe(device.user_id, listener)


class OwnerPublisher:
    """Carries events from code that publishes without a database session of its own (the task
    registry, and through it the runner client; the project store; the usage guard) into the
    event log, for the person they belong to (B-38): a task's or project's agent's owner. An
    event that names no task or agent (usage) goes to the hub's owner, `user_id`."""

    def __init__(self, events: EventLog, user_id: str = OWNER_USER_ID) -> None:
        self.events = events
        self.user_id = user_id

    def person(self, session: Session, type: str, data: dict[str, Any]) -> str:
        """Whose agent the event is about: its own `agent_id`, else its task's, else its project's
        (also inside a nested `task`, `artifact` or `project` object, as `artifact.updated` has
        them; `artifact.moved` names `to_project_id`). None of these: the owner."""
        places = [data] + [
            data[k] for k in ('artifact', 'task', 'project') if isinstance(data.get(k), dict)
        ]
        agent_id: Any = next((p['agent_id'] for p in places if p.get('agent_id')), None)
        if agent_id is None:
            task_id = next((p['task_id'] for p in places if p.get('task_id')), None)
            if task_id is None and type.startswith('task.'):
                task_id = data.get('id')
            task = session.get(Task, task_id) if isinstance(task_id, str) else None
            agent_id = task.agent_id if task is not None else None
        if agent_id is None:
            project_id = next(
                (
                    p.get('project_id') or p.get('to_project_id')
                    for p in places
                    if p.get('project_id') or p.get('to_project_id')
                ),
                None,
            )
            project = session.get(Project, project_id) if isinstance(project_id, str) else None
            agent_id = project.agent_id if project is not None else None
        agent = session.get(Agent, agent_id) if isinstance(agent_id, str) else None
        return agent.owner_user_id if agent is not None else self.user_id

    def publish(self, type: str, data: dict[str, Any]) -> None:
        with Session(self.events.engine) as session:
            self.events.publish(session, self.person(session, type, data), type, data)


def install(app: FastAPI) -> None:
    """Mount `/ws`. Reuses `app.state.events` when the app made it earlier for other publishers."""
    if getattr(app.state, 'events', None) is None:
        app.state.events = EventLog(app.state.engine)
    app.include_router(router)
