"""The hub's side of the runner: a client for its unix socket, and a watcher for its streams.

- `RunnerClient` speaks HTTP over the runner's socket (`contracts/runner-api.yaml`). It is the
  task registry's `Runner`: it starts and stops sessions.
- `RunnerWatch` follows `GET /events` and one `GET /sessions/{id}/tail` per task session. It maps
  what the runner reports onto `contracts/task-states.md`, stores transcript events as task events
  (the registry publishes `task.updated` and `task.event` to the app), and copies `remote_url`.
- When the runner cannot be reached, every NUC task that was live goes to `unknown` and one
  "something broke" alert is raised. Reconnecting backs off from 0.5 s to 30 s. When the runner
  answers again the alert is cleared, the runner's first burst of `session.state` events gives each
  task its real state back, and the worker-cap queue is drained.
- Tails resume from the last transcript offset the hub stored, so a reconnect never stores an event
  twice. Offsets are kept in a small JSON file in the data folder, so a hub restart does not either.
- Anything from the runner that does not match the contract is logged and dropped, never stored.
"""

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import httpx
from sqlmodel import Session, col, select

from marcel_hub.models import Task, TaskLocation, TaskState
from marcel_hub.tasks.ports import CapReached, RunnerUnavailable, Started, StartFailed
from marcel_hub.tasks.registry import TaskRegistry
from marcel_hub.tasks.states import TRANSITIONS, IllegalTransition

log = logging.getLogger(__name__)

DEFAULT_SOCKET = Path('/run/marcel/runner.sock')
REQUEST_TIMEOUT = 15.0
# Spawns wait up to 30 s for the channel to register (runner-api.yaml), plus a clone.
SPAWN_TIMEOUT = 120.0
BASE_DELAY = 0.5
MAX_DELAY = 30.0

UNREACHABLE = 'runner_unreachable'  # the alert's cause

# Task states that only a live session holds; these go to `unknown` when the runner is lost.
LIVE = frozenset({TaskState.starting, TaskState.working, TaskState.needs_you, TaskState.silent})

# SP4: what Claude Code's job state says when a session hits a plan limit.
USAGE_LIMIT = re.compile(r'usage limit|rate limit', re.IGNORECASE)


class StreamRefused(Exception):
    """The runner answered a stream request with something other than 200."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _unreachable(exc: Exception) -> str:
    return (
        f'The runner is not answering ({type(exc).__name__}). Is marcel-runner running on the NUC?'
    )


def _message(res: httpx.Response) -> str:
    try:
        body = res.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get('message'), str):
        return body['message']
    return f'The runner answered {res.status_code}.'


def _code(res: httpx.Response) -> str | None:
    try:
        body = res.json()
    except ValueError:
        return None
    return body.get('code') if isinstance(body, dict) else None


def slug(title: str) -> str:
    """A branch-safe slug for `RepoSpec.slug`: lowercase letters, digits and dashes, 1–40 long."""
    text = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:40].strip('-')
    return text or 'task'


class RunnerClient:
    """HTTP over the runner's unix socket. Every network failure becomes `RunnerUnavailable`."""

    def __init__(
        self,
        socket_path: Path | str = DEFAULT_SOCKET,
        *,
        worker_cwd: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.worker_cwd = worker_cwd
        self._http = httpx.AsyncClient(
            transport=transport or httpx.AsyncHTTPTransport(uds=str(socket_path)),
            base_url='http://runner',
            timeout=REQUEST_TIMEOUT,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            return await self._http.request(method, path, **kw)
        except httpx.TransportError as exc:
            raise RunnerUnavailable(_unreachable(exc)) from exc

    # The registry's `Runner` -----------------------------------------------------------------

    async def start(self, task: Task, prompt: str) -> Started:
        body: dict[str, Any] = {
            'role': 'worker',
            'name': task.id,
            'model': task.model,
            'prompt': prompt,
        }
        if task.repo:
            body['repo'] = {
                'url': f'https://github.com/{task.repo}',
                'task_id': task.id,
                'slug': slug(task.title),
            }
        elif self.worker_cwd:
            body['cwd'] = self.worker_cwd
        else:
            raise StartFailed(
                'The hub has no workspace for tasks without a repository. Set MARCEL_WORKER_CWD.'
            )
        res = await self._call('POST', '/sessions', json=body, timeout=SPAWN_TIMEOUT)
        if res.status_code == 201:
            spawned = res.json()
            return Started(session_id=spawned['session_id'], branch=spawned.get('branch'))
        if res.status_code == 409 and _code(res) == 'cap_reached':
            raise CapReached()
        raise StartFailed(_message(res))

    async def stop(self, session_id: str) -> None:
        res = await self._call('POST', f'/sessions/{session_id}/stop')
        if res.status_code == 404:
            log.info('Session %s was already gone when the hub stopped it.', session_id)
            return
        if res.status_code != 200:
            raise RunnerUnavailable(f'The runner could not stop the session: {_message(res)}')

    # Reads -----------------------------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        res = await self._call('GET', '/health')
        if res.status_code != 200:
            raise RunnerUnavailable(_message(res))
        return res.json()

    async def usage(self) -> dict[str, Any] | None:
        """The runner's latest usage reading, or None when it has none (or cannot be asked)."""
        try:
            res = await self._call('GET', '/usage')
        except RunnerUnavailable:
            return None
        return res.json() if res.status_code == 200 else None

    async def open_stream(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Open an SSE stream. The caller reads it with `sse()` and closes it with `aclose()`."""
        request = self._http.build_request(
            'GET',
            path,
            params=params,
            headers=headers,
            timeout=httpx.Timeout(REQUEST_TIMEOUT, read=None),  # streams stay open
        )
        try:
            res = await self._http.send(request, stream=True)
        except httpx.TransportError as exc:
            raise RunnerUnavailable(_unreachable(exc)) from exc
        if res.status_code != 200:
            await res.aread()
            await res.aclose()
            raise StreamRefused(res.status_code, _message(res))
        return res


@dataclass(frozen=True)
class SSE:
    id: str | None
    event: str
    data: str


async def sse(lines: AsyncIterator[str]) -> AsyncIterator[SSE]:
    """Server-Sent Events from a stream of lines (the subset the runner sends)."""
    last_id: str | None = None
    event = ''
    data: list[str] = []
    async for line in lines:
        if not line:
            if data:
                yield SSE(last_id, event or 'message', '\n'.join(data))
            event, data = '', []
            continue
        if line.startswith(':'):
            continue
        name, _, value = line.partition(':')
        value = value.removeprefix(' ')
        if name == 'data':
            data.append(value)
        elif name == 'id':
            last_id = value
        elif name == 'event':
            event = value


# Checking what the runner sends ----------------------------------------------------------------

RUNNER_EVENT_TYPES = frozenset({'session.state', 'session.gone', 'runner.warning'})
SESSION_STATES = frozenset({'working', 'blocked', 'done', 'failed', 'stopped'})
# transcript.schema.json: the required keys of each event type's `data`.
TRANSCRIPT_DATA = {
    'text': ('role', 'text'),
    'thinking': ('duration_ms',),
    'tool_call': ('tool_use_id', 'tool', 'summary'),
    'tool_result': ('tool_use_id', 'is_error', 'text'),
    'diff': ('tool_use_id', 'file_path', 'hunks'),
    'subagent_start': ('tool_use_id', 'description'),
    'subagent_stop': ('agent_id', 'status'),
    'turn_end': ('duration_ms',),
    'error': ('message',),
    'raw': ('entry_type',),
}


class BadEvent(ValueError):
    """Something from the runner does not match the contract. It is logged and dropped."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_time(value: Any, what: str) -> None:
    if not isinstance(value, str):
        raise BadEvent(f'{what} has no time')
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise BadEvent(f'{what} has a time that is not ISO 8601: {value!r}') from None


def check_runner_event(raw: str) -> dict[str, Any]:
    """Parse and check one `RunnerEvent` (runner-api.yaml). Raises `BadEvent`."""
    try:
        event = json.loads(raw)
    except ValueError:
        raise BadEvent('a runner event is not JSON') from None
    if not isinstance(event, dict):
        raise BadEvent('a runner event is not an object')
    if not _is_int(event.get('seq')) or event['seq'] < 1:
        raise BadEvent('a runner event has no valid seq')
    kind = event.get('type')
    if kind not in RUNNER_EVENT_TYPES:
        raise BadEvent(f'unknown runner event type {kind!r}')
    _check_time(event.get('at'), f'runner event {event["seq"]}')
    if kind == 'runner.warning':
        if not isinstance(event.get('message'), str):
            raise BadEvent('a runner warning has no message')
        return event
    session = event.get('session')
    if not isinstance(session, dict):
        raise BadEvent(f'{kind} has no session')
    for key in ('id', 'short_id', 'name', 'cwd'):
        if not isinstance(session.get(key), str):
            raise BadEvent(f'{kind} session has no {key}')
    if session.get('state') not in SESSION_STATES:
        raise BadEvent(f'{kind} session has unknown state {session.get("state")!r}')
    return event


def check_transcript_event(raw: str, session_id: str) -> dict[str, Any]:
    """Parse and check one `TranscriptEvent` (transcript.schema.json). Raises `BadEvent`."""
    try:
        event = json.loads(raw)
    except ValueError:
        raise BadEvent('a transcript event is not JSON') from None
    if not isinstance(event, dict):
        raise BadEvent('a transcript event is not an object')
    if event.get('session_id') != session_id:
        raise BadEvent(f'a transcript event for {event.get("session_id")!r} on {session_id}')
    if not _is_int(event.get('offset')) or event['offset'] < 0:
        raise BadEvent('a transcript event has no valid offset')
    if 'index' in event and (not _is_int(event['index']) or event['index'] < 0):
        raise BadEvent('a transcript event has a bad index')
    _check_time(event.get('at'), f'transcript event at offset {event["offset"]}')
    required = TRANSCRIPT_DATA.get(event.get('type'))  # type: ignore[arg-type]
    if required is None:
        raise BadEvent(f'unknown transcript event type {event.get("type")!r}')
    data = event.get('data')
    if not isinstance(data, dict):
        raise BadEvent(f'a {event["type"]} event has no data')
    missing = [key for key in required if key not in data]
    if missing:
        raise BadEvent(f'a {event["type"]} event lacks {", ".join(missing)}')
    return event


# Tail positions --------------------------------------------------------------------------------


@dataclass
class TailPosition:
    """Where a session's transcript tail stands.

    `line` is the offset of the newest line seen and `index` the last event taken from it; one
    line can give several events. `committed` is the end of the line before it, which is known to
    be complete, so a tail resumes from `committed` and skips what it already has of `line`.
    """

    committed: int = 0
    line: int = 0
    index: int = -1

    def is_new(self, offset: int, index: int) -> bool:
        return offset > self.line or (offset == self.line and index > self.index)

    def advance(self, offset: int, index: int) -> None:
        if offset > self.line:
            self.committed, self.line = self.line, offset
        self.index = index


@dataclass
class OffsetStore:
    """Tail positions per session, kept in a JSON file (or only in memory without a path)."""

    path: Path | None = None
    positions: dict[str, TailPosition] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            stored = json.loads(self.path.read_text())
            self.positions = {sid: TailPosition(*pos) for sid, pos in stored.items()}
        except (ValueError, TypeError, OSError) as exc:
            log.warning(
                'Could not read %s (%s). Transcripts are read again from the start; '
                'events already stored may be stored twice.',
                self.path,
                exc,
            )

    def get(self, session_id: str) -> TailPosition:
        return self.positions.setdefault(session_id, TailPosition())

    def save(self) -> None:
        if self.path is None:
            return
        data = {sid: [p.committed, p.line, p.index] for sid, p in self.positions.items()}
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data))
        tmp.replace(self.path)


# Alerts (F08 stand-in) -------------------------------------------------------------------------


class Alerts(Protocol):
    """The "something broke" side of the notifier (F08). One open alert per cause."""

    def broke(self, cause: str, title: str, body: str) -> None: ...

    def resolved(self, cause: str) -> None: ...


@dataclass
class LogAlerts:
    """Stand-in until S-08.1: logs the alert and remembers which causes are open."""

    open: dict[str, str] = field(default_factory=dict)

    def broke(self, cause: str, title: str, body: str) -> None:
        self.open[cause] = body
        log.warning('Something broke: %s %s', title, body)

    def resolved(self, cause: str) -> None:
        if self.open.pop(cause, None) is not None:
            log.info('Resolved: %s', cause)


# Mapping the runner's session state onto task states --------------------------------------------


@dataclass(frozen=True)
class Report:
    state: TaskState
    reason: str
    fields: dict[str, Any] = field(default_factory=dict)


def _clock(value: str) -> str:
    when = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return when.strftime('%a %d %b, %H:%M UTC')


def usage_wait(usage: dict[str, Any] | None) -> str:
    """`waiting_for` for a usage limit, naming when the fuller window resets."""
    windows = [w for w in (usage or {}).values() if isinstance(w, dict) and 'resets_at' in w]
    if not windows:
        return 'The usage limit is reached. Marcel could not read when it resets.'
    fullest = max(windows, key=lambda w: w.get('used_pct', 0))
    try:
        return f'The usage limit is reached. It resets {_clock(fullest["resets_at"])}.'
    except (ValueError, TypeError):
        return 'The usage limit is reached. Marcel could not read when it resets.'


def is_usage_limit(session: dict[str, Any]) -> bool:
    needs = session.get('needs')
    return (
        session['state'] == 'blocked'
        and session.get('waiting_for') != 'permission prompt'
        and isinstance(needs, str)
        and bool(USAGE_LIMIT.search(needs))
    )


def map_session(session: dict[str, Any], usage: dict[str, Any] | None = None) -> Report:
    """What a runner `Session` means for its task (SP2, SP4, S-03.3's notes)."""
    state, status = session['state'], session.get('status')
    needs = session.get('needs') if isinstance(session.get('needs'), str) else None
    if state == 'working':
        if status == 'idle':  # SP2: a refused or interrupted turn, waiting for direction
            return Report(
                TaskState.needs_you,
                'The session stopped and waits for direction',
                {'waiting_for': 'Your direction: the last step was refused or interrupted.'},
            )
        return Report(TaskState.working, 'The runner reports working')
    if state == 'blocked':
        if session.get('waiting_for') == 'permission prompt':
            ask = f'Your approval: {needs}' if needs else 'Your approval to use a tool.'
            return Report(
                TaskState.needs_you, 'The runner reports a permission prompt', {'waiting_for': ask}
            )
        if is_usage_limit(session):
            return Report(
                TaskState.needs_you,
                'The usage limit is reached',
                {'waiting_for': usage_wait(usage)},
            )
        if session.get('waiting_for'):
            return Report(
                TaskState.needs_you,
                'The runner reports blocked',
                {'waiting_for': f'Claude Code waits for: {session["waiting_for"]}.'},
            )
        return Report(
            TaskState.needs_you,
            'The session asked a question',
            {'waiting_for': needs or 'An answer from you.'},
        )
    if state == 'done':
        result = session.get('result')
        return Report(
            TaskState.done,
            'The runner reports done',
            {'summary': result} if isinstance(result, str) and result else {},
        )
    if state == 'failed':
        detail = session.get('detail') or session.get('result')
        reason = f'The runner reports failed: {detail}' if detail else 'The runner reports failed'
        return Report(TaskState.failed, reason)
    return Report(TaskState.stopped, 'The session was stopped outside Marcel')


# The watcher ------------------------------------------------------------------------------------

FINISHED_SESSION = frozenset({TaskState.done, TaskState.failed, TaskState.stopped})


@dataclass
class _Tail:
    follow: bool
    job: asyncio.Task[None]


class RunnerWatch:
    """Follows the runner's event stream and the transcript of every task session."""

    def __init__(
        self,
        client: RunnerClient,
        registry: TaskRegistry,
        alerts: Alerts | None = None,
        offsets: OffsetStore | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        base_delay: float = BASE_DELAY,
        max_delay: float = MAX_DELAY,
    ) -> None:
        self.client = client
        self.registry = registry
        self.alerts = alerts or LogAlerts()
        self.offsets = offsets or OffsetStore()
        self.sleep = sleep
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.reachable: bool | None = None  # None until the first try
        self.reason = 'The hub has not reached the runner yet.'
        self.last_seq: int | None = None
        self._tails: dict[str, _Tail] = {}
        self._job: asyncio.Task[None] | None = None

    # Lifecycle --------------------------------------------------------------------------------

    def start(self) -> None:
        self._job = asyncio.create_task(self.run(), name='runner-watch')

    async def close(self) -> None:
        jobs = [t.job for t in self._tails.values()]
        if self._job is not None:
            jobs.append(self._job)
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        self._tails.clear()

    async def run(self) -> None:
        delay = self.base_delay
        while True:
            headers = {'Last-Event-ID': str(self.last_seq)} if self.last_seq else None
            try:
                res = await self.client.open_stream('/events', headers=headers)
            except (RunnerUnavailable, StreamRefused) as exc:
                await self._down(str(exc))
                await self.sleep(delay)
                delay = min(delay * 2, self.max_delay)
                continue
            delay = self.base_delay
            await self._up()
            try:
                async for message in sse(res.aiter_lines()):
                    await self._on_runner_event(message.data)
            except httpx.TransportError as exc:
                log.warning('The runner event stream broke (%s). Reconnecting.', exc)
            finally:
                await res.aclose()
            await self.sleep(delay)
            delay = min(delay * 2, self.max_delay)

    # Reachability -----------------------------------------------------------------------------

    async def _up(self) -> None:
        was = self.reachable
        self.reachable, self.reason = True, ''
        if was is False:
            log.info('The runner answers again.')
            self.alerts.resolved(UNREACHABLE)
        # Tasks that waited for a worker slot while the runner was away start now.
        await self.registry.drain()

    async def _down(self, why: str) -> None:
        if self.reachable is False:
            return  # one alert per outage, however many retries fail
        self.reachable, self.reason = False, why
        await self._stop_tails()
        lost = self._live_nuc_tasks()
        for task_id in lost:
            await self._report(task_id, Report(TaskState.unknown, 'The runner is unreachable'))
        count = f'{len(lost)} task{"s" if len(lost) != 1 else ""}'
        self.alerts.broke(
            UNREACHABLE,
            'Marcel cannot reach the NUC',
            f'{why} Marcel cannot see its work there: {count} now show as unknown. '
            'It keeps trying and will tell you when the NUC is back.',
        )

    def _live_nuc_tasks(self) -> list[str]:
        with Session(self.registry.engine) as s:
            return list(
                s.exec(
                    select(Task.id)
                    .where(Task.location == TaskLocation.nuc)
                    .where(col(Task.state).in_(list(LIVE)))
                )
            )

    # The event stream -------------------------------------------------------------------------

    async def _on_runner_event(self, raw: str) -> None:
        try:
            event = check_runner_event(raw)
        except BadEvent as exc:
            log.warning('Ignored a bad runner event: %s.', exc)
            return
        self.last_seq = event['seq']
        if event['type'] == 'runner.warning':
            log.warning('The runner warns: %s', event['message'])
            self.alerts.broke('runner_warning', 'The NUC has a problem', event['message'])
            return
        session = event['session']
        task = self._task_for(session['id'])
        if task is None:
            return  # a brain, side or foreign session: not a task
        if event['type'] == 'session.gone':
            self._tail(task.id, session['id'], follow=False)
            return
        self._copy_remote_url(task, session)
        usage = await self.client.usage() if is_usage_limit(session) else None
        task = await self._report(task.id, map_session(session, usage))
        self._tail(task.id, session['id'], follow=task.state not in FINISHED_SESSION)

    def _task_for(self, session_id: str) -> Task | None:
        with Session(self.registry.engine) as s:
            return s.exec(
                select(Task)
                .where(Task.session_id == session_id)
                .where(Task.location == TaskLocation.nuc)
            ).first()

    def _copy_remote_url(self, task: Task, session: dict[str, Any]) -> None:
        url = session.get('remote_url')
        if isinstance(url, str) and url and url != task.remote_url:
            self._set_fields(task.id, remote_url=url)

    def _set_fields(self, task_id: str, **fields: Any) -> None:
        with Session(self.registry.engine) as s:
            row = s.get(Task, task_id)
            assert row is not None
            for name, value in fields.items():
                setattr(row, name, value)
            s.add(row)
            s.commit()
        self.registry.publisher.publish(
            'task.updated', self.registry.to_api(self.registry.get(task_id))
        )

    async def _report(self, task_id: str, report: Report) -> Task:
        """Apply a state report when the state machine allows it; otherwise keep the task as is."""
        task = self.registry.get(task_id)
        if task.state == report.state:
            changed = {k: v for k, v in report.fields.items() if getattr(task, k) != v}
            if changed:
                self._set_fields(task_id, **changed)
            return self.registry.get(task_id)
        if (task.state, report.state) not in TRANSITIONS:
            # e.g. a stopped task whose session the runner still lists as done: stopped is final.
            log.info(
                'Task %s stays %s; the runner reports %s.',
                task_id,
                task.state.value,
                report.state.value,
            )
            return task
        try:
            return await self.registry.report_state(
                task_id, report.state, report.reason, **report.fields
            )
        except IllegalTransition:  # the task moved meanwhile; the registry logged it
            return self.registry.get(task_id)

    # Transcript tails -------------------------------------------------------------------------

    def _tail(self, task_id: str, session_id: str, follow: bool) -> None:
        """Make sure the session's transcript is read: followed while live, read to the end once
        it has finished."""
        current = self._tails.get(session_id)
        if current is not None and not current.job.done():
            if current.follow == follow:
                return
            current.job.cancel()  # safe: offsets are saved before any wait
        job = asyncio.create_task(self._read_tail(task_id, session_id, follow))
        self._tails[session_id] = _Tail(follow, job)

    async def _stop_tails(self) -> None:
        jobs = [t.job for t in self._tails.values()]
        self._tails.clear()
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

    async def _read_tail(self, task_id: str, session_id: str, follow: bool) -> None:
        delay = self.base_delay
        while True:
            position = self.offsets.get(session_id)
            params = {'offset': position.committed, 'follow': 'true' if follow else 'false'}
            try:
                res = await self.client.open_stream(f'/sessions/{session_id}/tail', params=params)
            except StreamRefused as exc:
                log.warning('Cannot read the transcript of task %s: %s', task_id, exc)
                return
            except RunnerUnavailable:
                return  # the event stream notices too; tails restart when the runner is back
            try:
                async for message in sse(res.aiter_lines()):
                    await self._on_transcript(task_id, session_id, message.data)
                if not follow:
                    return
            except httpx.TransportError as exc:
                log.warning('The transcript of task %s broke off (%s). Resuming.', task_id, exc)
            finally:
                await res.aclose()
            await self.sleep(delay)
            delay = min(delay * 2, self.max_delay)

    async def _on_transcript(self, task_id: str, session_id: str, raw: str) -> None:
        try:
            event = check_transcript_event(raw, session_id)
        except BadEvent as exc:
            log.warning('Ignored a bad transcript event for task %s: %s.', task_id, exc)
            return
        offset, index = event['offset'], event.get('index', 0)
        position = self.offsets.get(session_id)
        if not position.is_new(offset, index):
            return  # already stored before a reconnect
        self.registry.append_transcript(task_id, event)
        position.advance(offset, index)
        self.offsets.save()
        if event['type'] == 'error' and event['data'].get('usage_limit'):
            usage = await self.client.usage()
            await self._report(
                task_id,
                Report(
                    TaskState.needs_you,
                    'The usage limit is reached',
                    {'waiting_for': usage_wait(usage)},
                ),
            )
