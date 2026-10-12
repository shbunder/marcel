"""The state watcher and `GET /events`.

`Watcher` polls `claude agents --json --all` (every 2 s), turns each row into the contract's
`Session` and hands the list to the `Feed`. The feed compares it with what it saw last and keeps
one `RunnerEvent` per real change: `session.state` when a session appears or any field changes,
`session.gone` when it leaves the list. `runner.warning` says, in plain words, when the runner has
not been able to see Claude's sessions three polls in a row; `runner.recovered` says when it can
again.

The feed keeps the last 1000 events. On every connect a client gets, in this order: the events it
missed (when its `Last-Event-ID` can be resumed from), then one `session.state` per known session,
all carrying the newest id (the fresh view), then the live events.

Event ids start at the runner's start time in milliseconds and go up by one per event, so an id
from before a runner restart is always older than every id of the new run and is never mistaken
for one of them.
"""

import contextlib
import json
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from marcel_runner.sessions import STATES, SessionError, project_dir

INTERVAL = 2.0  # seconds between two polls (SP2: a poll costs ≈ 0.12 s)
KEEP = 1000  # events kept for resuming (contracts/runner-api.yaml)
WARN_AFTER = 3  # failed polls in a row before a runner.warning
KEEPALIVE = 15.0  # seconds between SSE comments on a quiet stream, to notice a client that left


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


class Feed:
    """The current view of every session plus a bounded history of events. Thread-safe."""

    def __init__(
        self,
        keep: int = KEEP,
        first_seq: int | None = None,
        clock: Callable[[], str] = _now,
    ) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=keep)
        self._view: dict[str, dict[str, Any]] = {}
        self._cond = threading.Condition()
        self._next = first_seq if first_seq is not None else int(time.time() * 1000)
        self._clock = clock
        self.closed = False

    @property
    def head(self) -> int:
        """The id of the newest event (one less than the first id while there is none)."""
        return self._next - 1

    def _append(self, kind: str, **fields: Any) -> dict[str, Any]:
        event = {'seq': self._next, 'type': kind, 'at': self._clock(), **fields}
        self._next += 1
        self._events.append(event)
        return event

    def update(self, sessions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """Compare a full list of sessions with the last one; keep and return one event per
        change. A session that is exactly as it was gives no event."""
        current = {s['id']: s for s in sessions}
        out: list[dict[str, Any]] = []
        with self._cond:
            for sid, session in current.items():
                before = self._view.get(sid)
                if before == session:
                    continue
                fields: dict[str, Any] = {'session': session}
                if before is not None:
                    fields['previous_state'] = before['state']
                out.append(self._append('session.state', **fields))
            for sid, before in self._view.items():
                if sid not in current:
                    out.append(self._append('session.gone', session=before))
            self._view = current
            if out:
                self._cond.notify_all()
        return out

    def warn(self, message: str) -> dict[str, Any]:
        return self._message('runner.warning', message)

    def recovered(self, message: str) -> dict[str, Any]:
        return self._message('runner.recovered', message)

    def _message(self, kind: str, message: str) -> dict[str, Any]:
        with self._cond:
            event = self._append(kind, message=message)
            self._cond.notify_all()
        return event

    def connect(self, last_event_id: str | None) -> tuple[list[dict[str, Any]], int]:
        """What a new connection gets first, and the id to follow from.

        With an id this feed can resume from, the events after it first. Then, on every connect,
        one `session.state` per known session, all carrying the newest id: the fresh view. It
        comes last, so the client ends on each session's current state (a client that marked its
        sessions unknown while it was away gets them back even when nothing changed).
        """
        with self._cond:
            last = _parse_id(last_event_id)
            oldest = self._events[0]['seq'] if self._events else self._next
            missed: list[dict[str, Any]] = []
            if last is not None and oldest - 1 <= last <= self.head:
                missed = [e for e in self._events if e['seq'] > last]
            at = self._clock()
            fresh = [
                {'seq': self.head, 'type': 'session.state', 'at': at, 'session': s}
                for s in self._view.values()
            ]
            return missed + fresh, self.head

    def after(self, cursor: int, timeout: float) -> list[dict[str, Any]] | None:
        """The events after `cursor`, waiting up to `timeout` for one. `[]` when none came.
        None when the feed is closed, or when `cursor` fell out of the history: the client must
        reconnect (and then gets a fresh view)."""
        with self._cond:
            if self.head <= cursor and not self.closed:
                self._cond.wait(timeout)
            if self.closed:
                return None
            if self._events and self._events[0]['seq'] - 1 > cursor:
                return None
            return [e for e in self._events if e['seq'] > cursor]

    def close(self) -> None:
        with self._cond:
            self.closed = True
            self._cond.notify_all()


def _parse_id(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


class RemoteUrls:
    """Each session's claude.ai Remote Control page: the last `remote_session_change` attachment
    in its transcript (as `GET /sessions/{id}` reads it). Reads only what was added since the last
    poll, so a long transcript is read once, not every 2 s."""

    def __init__(self) -> None:
        self._seen: dict[str, tuple[int, str | None]] = {}  # session id → (offset, url)

    def get(self, session_id: str, path: Path) -> str | None:
        offset, url = self._seen.get(session_id, (0, None))
        try:
            with path.open('rb') as f:
                if os.fstat(f.fileno()).st_size < offset:  # rewritten: start over
                    offset, url = 0, None
                f.seek(offset)
                for line in f:
                    if not line.endswith(b'\n'):
                        break  # half written: read it next time
                    offset += len(line)
                    if b'remote_session_change' in line:
                        url = _url_from(line, url)
        except OSError:
            return url
        self._seen[session_id] = (offset, url)
        return url

    def keep_only(self, session_ids: Iterable[str]) -> None:
        keep = set(session_ids)
        self._seen = {k: v for k, v in self._seen.items() if k in keep}


def _url_from(line: bytes, current: str | None) -> str | None:
    try:
        attachment = json.loads(line).get('attachment') or {}
    except (ValueError, AttributeError):
        return current
    if not isinstance(attachment, dict) or attachment.get('type') != 'remote_session_change':
        return current
    value = attachment.get('url')
    return value if isinstance(value, str) and value.startswith('https://') else None


class Roster(Protocol):
    home: Path

    def roster(self, all: bool = True) -> list[dict[str, Any]]: ...

    def normalise(self, row: dict[str, Any]) -> dict[str, Any]: ...


def _say(message: str) -> None:
    print(f'marcel-runner: {message}', file=sys.stderr, flush=True)


class Watcher:
    """Polls the roster on its own thread and feeds the changes into `feed`. It never dies: a
    failed poll is counted, said once in the log and, from the third in a row, as a warning."""

    def __init__(
        self,
        sessions: Roster,
        feed: Feed | None = None,
        *,
        interval: float = INTERVAL,
        warn_after: int = WARN_AFTER,
        keepalive: float = KEEPALIVE,
    ) -> None:
        self.sessions = sessions
        self.feed = feed or Feed()
        self.interval = interval
        self.warn_after = warn_after
        self.keepalive = keepalive
        self.failures = 0
        self.remote = RemoteUrls()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def read(self) -> list[dict[str, Any]]:
        """Every background session, as the contract's `Session`."""
        out = []
        for row in self.sessions.roster(all=True):
            if row.get('state') not in STATES or not row.get('sessionId'):
                continue
            session = self.sessions.normalise(row)
            transcript = (
                self.sessions.home
                / '.claude'
                / 'projects'
                / project_dir(session['cwd'])
                / f'{session["id"]}.jsonl'
            )
            url = self.remote.get(session['id'], transcript)
            if url:
                session['remote_url'] = url
            out.append(session)
        self.remote.keep_only(s['id'] for s in out)
        return out

    def poll(self) -> list[dict[str, Any]]:
        """One poll. Returns the events it made (a warning included)."""
        try:
            sessions = self.read()
        except Exception as e:  # never let the watcher die; say what went wrong
            return self._failed(e)
        recovered: list[dict[str, Any]] = []
        if self.failures >= self.warn_after:
            message = (
                f"The runner can see Claude's sessions again, after {self.failures} failed tries. "
                'Task states are up to date again.'
            )
            _say(message)
            recovered = [self.feed.recovered(message)]
        elif self.failures:
            _say(f'Claude listed its sessions again after {self.failures} failed tries.')
        self.failures = 0
        return recovered + self.feed.update(sessions)

    def _failed(self, e: Exception) -> list[dict[str, Any]]:
        self.failures += 1
        if isinstance(e, SessionError):
            why = e.message + (f' ({e.detail})' if e.detail else '')
        else:
            why = f'Something unexpected went wrong: {e!r}.'
        if self.failures == 1:
            _say(f'Could not read the session list: {why} Trying again in {self.interval:g} s.')
        if self.failures != self.warn_after:
            return []
        message = (
            f"The runner has not been able to see Claude's sessions for {self.failures} tries in "
            f'a row. {why} Task states may be out of date until it can; it keeps trying. '
            "If it does not come back, check Claude's supervisor (marcel-claude-daemon.service)."
        )
        _say(message)
        return [self.feed.warn(message)]

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name='state-watcher', daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll()
            self._stop.wait(self.interval)

    def stop(self) -> None:
        """Stop polling and end every open event stream."""
        self._stop.set()
        self.feed.close()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 15)

    # --- GET /events ----------------------------------------------------------------------

    def stream(self, handler: Any) -> None:
        """Answer `GET /events` on a `BaseHTTPRequestHandler` until the client leaves or the
        runner stops. Each message: `id: <seq>` and `data: <RunnerEvent>`."""
        first, cursor = self.feed.connect(handler.headers.get('Last-Event-ID'))
        handler.send_response(200)
        handler.send_header('content-type', 'text/event-stream')
        handler.send_header('cache-control', 'no-cache')
        handler.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            _write(handler.wfile, first)
            while True:
                events = self.feed.after(cursor, self.keepalive)
                if events is None:
                    return  # closed, or this client fell behind: it reconnects and starts afresh
                if events:
                    _write(handler.wfile, events)
                    cursor = events[-1]['seq']
                else:
                    handler.wfile.write(b': still here\n\n')


def _write(wfile: Any, events: list[dict[str, Any]]) -> None:
    if not events:
        return
    wfile.write(b''.join(f'id: {e["seq"]}\ndata: {json.dumps(e)}\n\n'.encode() for e in events))
    wfile.flush()
