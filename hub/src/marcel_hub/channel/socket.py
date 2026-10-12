"""`/channel`: the WebSocket every NUC session's channel server holds to the hub.

It is mounted outside `/api` on purpose: the Cloudflare tunnel forwards only `/api/*`, so the
channel is reachable from the NUC alone. A socket has `HELLO_TIMEOUT` seconds to say hello.
Frames over `MAX_FRAME` bytes close the socket. Hub frames are read from the Outbox a page at a
time, so a session that stops reading costs the hub nothing but its wake-up flag.
"""

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from marcel_hub.channel.frames import BadFrame, parse
from marcel_hub.channel.gateway import Gateway, Peer, Rejection
from marcel_hub.channel.outbox import PAGE, Wake

log = logging.getLogger(__name__)

HELLO_TIMEOUT = 15.0
MAX_FRAME = 256 * 1024
POLICY_VIOLATION = 1008
TOO_BIG = 1009
TUNNEL_HEADER = 'cf-connecting-ip'
REPLACED = 4000  # a newer connection for the same session took over

router = APIRouter()


class Link:
    """One accepted socket: sends are serialised, the sender follows the Outbox."""

    def __init__(self, ws: WebSocket, gateway: Gateway, peer: Peer, resume_after: int) -> None:
        self.ws = ws
        self.gateway = gateway
        self.peer = peer
        self.sent = resume_after
        self.wake = Wake(asyncio.get_running_loop())
        self.replaced = asyncio.Event()
        self._send_lock = asyncio.Lock()

    async def send(self, frame: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.ws.send_json(frame)

    async def pump(self) -> None:
        """Send unacked frames after `sent`, in seq order, whenever the Outbox wakes us."""
        outbox = self.gateway.outbox
        while True:
            await self.wake.event.wait()
            self.wake.event.clear()
            while True:
                frames = await asyncio.to_thread(outbox.after, self.peer.target, self.sent)
                for frame in frames:
                    await self.send(frame)
                    self.sent = frame['seq']
                if len(frames) < PAGE:
                    break


async def _receive(ws: WebSocket) -> str | None:
    """The next text frame, or None when the socket closed. Raises `BadFrame` when too big."""
    message = await ws.receive()
    if message['type'] == 'websocket.disconnect':
        return None
    raw = message.get('text')
    if raw is None:
        raw = (message.get('bytes') or b'').decode('utf-8', errors='replace')
    if len(raw.encode()) > MAX_FRAME:
        raise BadFrame(f'a frame is larger than {MAX_FRAME} bytes')
    return raw


async def _close(ws: WebSocket, code: int) -> None:
    with contextlib.suppress(Exception):
        await ws.close(code=code)


async def _hello(ws: WebSocket, gateway: Gateway) -> tuple[Peer, int] | None:
    try:
        raw = await asyncio.wait_for(_receive(ws), HELLO_TIMEOUT)
    except TimeoutError:
        log.info('A channel socket said no hello in %.0f s. Closed it.', HELLO_TIMEOUT)
        await _close(ws, POLICY_VIOLATION)
        return None
    except BadFrame as exc:
        log.warning('Closed a channel socket before hello: %s.', exc)
        await _close(ws, TOO_BIG)
        return None
    if raw is None:
        return None
    try:
        hello = parse(raw)
        if hello['type'] != 'hello':
            raise BadFrame(f'the first frame is {hello["type"]}, not hello')
    except BadFrame as exc:
        log.warning('Closed a channel socket before hello: %s.', exc)
        await _close(ws, POLICY_VIOLATION)
        return None
    outcome = gateway.check_hello(hello)
    if isinstance(outcome, Rejection):
        log.info('Rejected channel session %s: %s.', hello['session_id'][:8], outcome.reason)
        with contextlib.suppress(Exception):
            await ws.send_json(outcome.frame())
        await _close(ws, POLICY_VIOLATION)
        return None
    return outcome, hello.get('resume_after', 0)


async def _handle(gateway: Gateway, peer: Peer, frame: dict[str, Any]) -> list[dict[str, Any]]:
    """A frame the hub fails to store is not acked: the server sends it again after a
    reconnect. The socket stays up."""
    try:
        return await gateway.handle(peer, frame)
    except Exception:
        log.exception('Could not handle a %s frame from session %s.', frame['type'],
                      peer.session_id[:8])  # fmt: skip
        return []


@router.websocket('/channel')
async def channel(ws: WebSocket) -> None:
    gateway: Gateway = ws.app.state.channel
    if TUNNEL_HEADER in ws.headers:
        # Came through Cloudflare: the channel is for the NUC's own sessions only. (A backstop:
        # the tunnel's ingress forwards only /api/*.)
        log.warning('Refused a /channel connection that came through the tunnel.')
        await ws.close(code=POLICY_VIOLATION)
        return
    await ws.accept()
    hello = await _hello(ws, gateway)
    if hello is None:
        return
    peer, resume_after = hello
    if resume_after:
        # The server delivered everything up to here: those frames count as acked.
        await asyncio.to_thread(gateway.outbox.ack, peer.target, resume_after, True)
    link = Link(ws, gateway, peer, resume_after)
    await link.send(peer.welcome())
    log.info('Channel session %s connected as %s.', peer.session_id[:8], peer.role)
    old = gateway.links.get(peer.target)
    gateway.links[peer.target] = link
    if old is not None:
        old.replaced.set()
    gateway.outbox.attach(peer.target, link.wake)
    sending = asyncio.ensure_future(link.pump())
    replaced = asyncio.ensure_future(link.replaced.wait())
    incoming: asyncio.Future[str | None] = asyncio.ensure_future(_receive(ws))
    try:
        while True:
            await asyncio.wait({sending, replaced, incoming}, return_when=asyncio.FIRST_COMPLETED)
            if replaced.done():
                await _close(ws, REPLACED)
                return
            if sending.done():
                with contextlib.suppress(WebSocketDisconnect):
                    sending.result()
                return
            raw = incoming.result()
            if raw is None:
                return
            try:
                frame = parse(raw)
            except BadFrame as exc:
                log.warning('Ignored a bad frame from session %s: %s.', peer.session_id[:8], exc)
            else:
                for answer in await _handle(gateway, peer, frame):
                    await link.send(answer)
            incoming = asyncio.ensure_future(_receive(ws))
    except BadFrame as exc:
        log.warning('Closed channel session %s: %s.', peer.session_id[:8], exc)
        await _close(ws, TOO_BIG)
    except WebSocketDisconnect:
        return
    finally:
        for job in (sending, replaced, incoming):
            job.cancel()
        gateway.outbox.detach(peer.target, link.wake)
        if gateway.links.get(peer.target) is link:
            del gateway.links[peer.target]
        log.info('Channel session %s disconnected.', peer.session_id[:8])
