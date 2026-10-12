"""A fake runner behind an httpx transport, for the runner client tests. Nothing here reaches a
real runner, a socket or the network.

`FakeRunnerServer` answers the runner API (contracts/runner-api.yaml) from memory: spawn, stop,
health, usage, the `/events` stream (on every connect: the missed events after `Last-Event-ID`,
then a fresh view of one `session.state` per known session, then live events) and per-session
`tail` streams that honour `offset` (one past the end of the file streams it again from 0, as the
runner does). `start_over()` replaces a transcript file, and open tails stream the new one from 0.
`tail_refusals` answers the next tail requests with those statuses. `kill()` breaks every open
stream and refuses new connections; `revive()` brings it back.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from marcel_hub.runner_client import RunnerClient, RunnerWatch


def now() -> str:
    return datetime.now(UTC).isoformat().replace('+00:00', 'Z')


def sse(seq: int | str, data: str, event: str | None = None) -> bytes:
    head = f'event: {event}\n' if event else ''
    return f'id: {seq}\n{head}data: {data}\n\n'.encode()


class FakeRunnerServer:
    def __init__(self, cap: int = 4) -> None:
        self.up = True
        self.cap = cap
        self.sessions: dict[str, dict[str, Any]] = {}
        self.events: list[tuple[int, str]] = []  # (seq, raw data)
        self.transcripts: dict[str, list[dict[str, Any]]] = {}
        self.usage_reading: dict[str, Any] | None = None
        self.requests: list[httpx.Request] = []
        self.spawned: list[dict[str, Any]] = []
        self.stopped: list[str] = []
        self.tail_drop_after: int | None = None  # break the next tail after this many events
        self.tail_refusals: list[int] = []  # statuses for the next tail requests, in order
        self.generations: dict[str, int] = {}  # bumped when a transcript file starts over
        self.seq = 0
        self._waiters: list[asyncio.Event] = []

    # Driving the fake -------------------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client(self, **kw: Any) -> RunnerClient:
        return RunnerClient('/fake/runner.sock', transport=self.transport(), **kw)

    def push_state(self, session_id: str, **fields: Any) -> None:
        session = self.sessions[session_id]
        previous = session['state']
        session.update(fields)
        self.push_raw(
            json.dumps(
                {
                    'seq': self.seq + 1,
                    'type': 'session.state',
                    'at': now(),
                    'session': session,
                    'previous_state': previous,
                }
            )
        )

    def push_gone(self, session_id: str) -> None:
        session = self.sessions.pop(session_id)
        self.push_raw(
            json.dumps(
                {'seq': self.seq + 1, 'type': 'session.gone', 'at': now(), 'session': session}
            )
        )

    def push_raw(self, data: str) -> None:
        self.seq += 1
        self.events.append((self.seq, data))
        self._wake()

    def write(
        self,
        session_id: str,
        *events: tuple[int, int, str, dict[str, Any]],
        at: str | None = None,
    ) -> None:
        """Append transcript events: (offset, index, type, data), all at `at` when given."""
        rows = self.transcripts.setdefault(session_id, [])
        for offset, index, kind, data in events:
            rows.append(
                {
                    'session_id': session_id,
                    'offset': offset,
                    'index': index,
                    'at': at or now(),
                    'type': kind,
                    'data': data,
                }
            )
        self._wake()

    def start_over(
        self, session_id: str, *events: tuple[int, int, str, dict[str, Any]], at: str | None = None
    ) -> None:
        """Replace the transcript file (truncated or rewritten); open tails read it from 0."""
        self.transcripts[session_id] = []
        self.generations[session_id] = self.generations.get(session_id, 0) + 1
        self.write(session_id, *events, at=at)

    def write_raw(self, session_id: str, offset: int, raw: str) -> None:
        self.transcripts.setdefault(session_id, []).append({'raw': raw, 'offset': offset})
        self._wake()

    def kill(self) -> None:
        self.up = False
        self._wake()

    def revive(self) -> None:
        self.up = True

    def add_session(self, state: str = 'working', **fields: Any) -> str:
        sid = str(uuid.uuid4())
        self.sessions[sid] = {
            'id': sid,
            'short_id': sid[:8],
            'name': fields.pop('name', 'adopted'),
            'role': 'worker',
            'state': state,
            'cwd': '/home/owner/marcel/workspaces/scratch',
            **fields,
        }
        return sid

    def tail_offsets(self, session_id: str) -> list[int]:
        return [
            int(r.url.params['offset'])
            for r in self.requests
            if r.url.path == f'/sessions/{session_id}/tail'
        ]

    # Streams ----------------------------------------------------------------------------------

    def _wake(self) -> None:
        for waiter in self._waiters:
            waiter.set()
        self._waiters.clear()

    async def _wait(self) -> None:
        waiter = asyncio.Event()
        self._waiters.append(waiter)
        await waiter.wait()

    def _check_alive(self) -> None:
        if not self.up:
            raise httpx.ReadError('The fake runner was killed.')

    async def _events(self, after: int) -> AsyncIterator[bytes]:
        """The real runner's order (runner-api.yaml `/events`): with a resumable `Last-Event-ID`
        the events after it come first; then one `session.state` per known session, all carrying
        the newest seq, so the hub rebuilds its view even for sessions that did not change; then
        live events."""
        self._check_alive()
        cursor = len(self.events)
        if after:  # without a Last-Event-ID there is nothing to resume
            for seq, data in self.events[:cursor]:
                if seq > after:
                    yield sse(seq, data)
        newest = max(self.seq, 1)
        for session in list(self.sessions.values()):
            body = {'seq': newest, 'type': 'session.state', 'at': now(), 'session': session}
            yield sse(newest, json.dumps(body))
        while True:
            self._check_alive()
            while cursor < len(self.events):
                seq, data = self.events[cursor]
                cursor += 1
                if seq > after:
                    yield sse(seq, data)
            await self._wait()

    async def _tail(self, session_id: str, offset: int, follow: bool) -> AsyncIterator[bytes]:
        cursor = 0
        budget: int | None = None  # events left before this stream breaks
        generation = self.generations.get(session_id, 0)
        rows = self.transcripts.get(session_id, [])
        if rows and offset > rows[-1]['offset']:
            offset = 0  # past the end of the file: the runner streams it again from 0
        while True:
            self._check_alive()
            if self.generations.get(session_id, 0) != generation:
                generation, cursor, offset = self.generations[session_id], 0, 0
            rows = self.transcripts.get(session_id, [])
            while cursor < len(rows):
                row = rows[cursor]
                cursor += 1
                if row['offset'] <= offset:
                    continue
                if self.tail_drop_after is not None:
                    budget, self.tail_drop_after = self.tail_drop_after, None
                if budget is not None:
                    if budget == 0:
                        raise httpx.ReadError('The transcript stream broke.')
                    budget -= 1
                data = row['raw'] if 'raw' in row else json.dumps(row)
                yield sse(row['offset'], data, 'transcript')
            if not follow:
                return
            await self._wait()

    # The API ----------------------------------------------------------------------------------

    def _running(self) -> int:
        return sum(
            1
            for s in self.sessions.values()
            if s.get('role') == 'worker' and s['state'] in ('working', 'blocked')
        )

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError('No such file or directory', request=request)
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path == '/health':
            return httpx.Response(200, json={'ok': True})
        if path == '/usage':
            if self.usage_reading is None:
                return httpx.Response(503, json={'code': 'no_reading', 'message': 'No reading.'})
            return httpx.Response(200, json=self.usage_reading)
        if path == '/events':
            after = int(request.headers.get('Last-Event-ID') or 0)
            return httpx.Response(
                200, headers={'content-type': 'text/event-stream'}, content=self._events(after)
            )
        if path == '/sessions' and method == 'POST':
            body = json.loads(request.content)
            if self._running() >= self.cap:
                return httpx.Response(409, json={'code': 'cap_reached', 'message': 'Cap.'})
            self.spawned.append(body)
            sid = self.add_session('working', name=body['name'])
            self.push_state(sid)
            return httpx.Response(
                201,
                json={
                    'session_id': sid,
                    'short_id': sid[:8],
                    'name': body['name'],
                    'cwd': '/w',
                    'channel_registered': True,
                },
            )
        parts = path.strip('/').split('/')
        if len(parts) == 3 and parts[0] == 'sessions':
            sid, action = parts[1], parts[2]
            if sid not in self.sessions and sid not in self.transcripts:
                return httpx.Response(404, json={'code': 'not_found', 'message': 'No session.'})
            if action == 'stop':
                self.stopped.append(sid)
                self.push_state(sid, state='stopped')
                return httpx.Response(200, json=self.sessions[sid])
            if action == 'tail':
                if self.tail_refusals:
                    status = self.tail_refusals.pop(0)
                    codes = {400: 'bad_request', 503: 'claude_timeout'}
                    return httpx.Response(
                        status, json={'code': codes.get(status, 'error'), 'message': 'Refused.'}
                    )
                offset = int(request.url.params.get('offset', 0))
                follow = request.url.params.get('follow', 'true') == 'true'
                return httpx.Response(
                    200,
                    headers={'content-type': 'text/event-stream'},
                    content=self._tail(sid, offset, follow),
                )
        return httpx.Response(404, json={'code': 'not_found', 'message': f'No {path}.'})


class Sleeps:
    """The watcher's sleep: records each backoff delay and yields to the loop for a moment."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        await asyncio.sleep(0.002)


class RecordingAlerts:
    def __init__(self) -> None:
        self.raised: list[tuple[str, str, str]] = []
        self.cleared: list[str] = []

    def broke(self, cause: str, title: str, body: str) -> None:
        self.raised.append((cause, title, body))

    def resolved(self, cause: str) -> None:
        self.cleared.append(cause)


async def until(condition: Callable[[], bool], timeout: float = 3.0) -> None:
    """Wait until `condition()` holds; fail the test after `timeout` seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            raise AssertionError('Timed out waiting for the condition.')
        await asyncio.sleep(0.003)


async def settle() -> None:
    """Let the watcher's tasks run until nothing is left to do right now."""
    for _ in range(20):
        await asyncio.sleep(0.002)


def watch_for(
    fake: FakeRunnerServer, registry: Any, alerts: RecordingAlerts, sleeps: Sleeps, **kw: Any
) -> RunnerWatch:
    client = fake.client(worker_cwd='/home/owner/marcel/workspaces/scratch')
    registry.runner = client
    return RunnerWatch(client, registry, alerts, sleep=sleeps, **kw)
