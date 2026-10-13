"""Background sessions: spawn, list, get and stop, plus the worker cap.

Every `claude` call goes through `Sessions._claude()`: an argument list (never a shell string),
the absolute path from the settings, and an environment with no inherited `CLAUDE*` or
`ANTHROPIC_*` variable (SP2 trap 2). State comes from `claude agents --json --all`; the runner
keeps its own record of the sessions it started (role, name, model, …) in a state file, so roles
and the worker cap survive a runner restart.
"""

import contextlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from marcel_runner.claude_env import scrubbed_env
from marcel_runner.health import Settings, cgroup_of, collect_health

CHANNEL = 'plugin:marcel@marcel-local'
CHANNEL_SERVER = 'plugin:marcel:marcel'  # how the debug log names the plugin's MCP server (SP1)
ALLOWED_TOOLS = ('mcp__plugin_marcel_marcel__reply', 'mcp__plugin_marcel_marcel__report')
# Brain and side sessions also get Marcel's brain tools and Harry's two always-shown tools, by
# name, never a wildcard (contracts/runner-api.yaml). Workers never do.
BRAIN_TOOLS = tuple(
    f'mcp__plugin_marcel_tools__{name}'
    for name in (
        'artifacts_publish',
        'board_note',
        'memory_read',
        'memory_write',
        'notify',
        'projects_create',
        'projects_list',
        'projects_update',
        'schedule_create',
        'schedule_delete',
        'schedule_list',
        'tasks_get',
        'tasks_list',
        'tasks_move',
        'tasks_send',
        'tasks_spawn',
        'tasks_stop',
        'usage_get',
    )
) + (
    # Harry's two always-shown tools (runner-api 0.1.8); its others come and go, so never listed.
    'mcp__plugin_marcel_harry__harry_find_tools',
    'mcp__plugin_marcel_harry__harry_mark_done',
)
REGISTERED = 'Channel notifications registered'
SKIPPED = 'Channel notifications skipped'
CHANNEL_WAIT = 30.0
SPAWN_TIMEOUT = 60.0  # `claude --bg` returned in 0.6–1.4 s in SP2

ROLES = ('brain', 'side', 'worker', 'adopted')
PERMISSION_MODES = ('auto', 'acceptEdits', 'manual', 'plan')
STATES = ('working', 'blocked', 'done', 'failed', 'stopped')
SPAWN_KEYS = {'role', 'name', 'model', 'permission_mode', 'prompt', 'cwd', 'repo', 'channel'}
NAME = re.compile(r'[A-Za-z0-9._-]{1,60}')
MODEL = re.compile(r'[A-Za-z0-9._\[\]-]{1,80}')
BACKGROUNDED = re.compile(r'backgrounded · ([0-9a-f]{8}) · (.+)')


class SessionError(Exception):
    """An HTTP error with a contract `Error` body. `message` is for a person on a phone."""

    def __init__(self, status: int, code: str, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail

    def body(self) -> dict[str, str]:
        out = {'code': self.code, 'message': self.message}
        if self.detail:
            out['detail'] = self.detail
        return out


class StateFileError(Exception):
    """The runner's state file cannot be read. The message says which file and what to do."""


def state_dir_from_env(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get('MARCEL_RUNNER_STATE'):
        return Path(env['MARCEL_RUNNER_STATE'])
    base = env.get('XDG_STATE_HOME') or str(Path.home() / '.local' / 'state')
    return Path(base) / 'marcel' / 'runner'


def allowed_tools(role: str | None) -> tuple[str, ...]:
    """What `--allowedTools` lists for a session with this role."""
    return ALLOWED_TOOLS + (BRAIN_TOOLS if role in ('brain', 'side') else ())


def spawn_argv(
    claude: Path,
    *,
    name: str,
    model: str,
    permission_mode: str,
    prompt: str,
    debug_file: Path,
    channel: bool,
    role: str,
) -> list[str]:
    """The `claude --bg` command line. The prompt goes after `--`: `--channels` and
    `--allowedTools` take several values and would swallow it (SP1)."""
    argv = [
        str(claude),
        '--bg',
        '--name',
        name,
        '--model',
        model,
        '--permission-mode',
        permission_mode,
    ]
    if channel:
        argv += ['--channels', CHANNEL]
    argv += ['--allowedTools', *allowed_tools(role), '--debug-file', str(debug_file), '--', prompt]
    return argv


@dataclass(frozen=True)
class SpawnRequest:
    role: str
    name: str
    model: str
    permission_mode: str
    prompt: str
    cwd: Path
    channel: bool


def _bad(message: str) -> SessionError:
    return SessionError(400, 'bad_request', message)


def parse_spawn(raw: bytes) -> SpawnRequest:
    """Check a spawn body against `SpawnRequest` (contracts/runner-api.yaml)."""
    try:
        body = json.loads(raw or b'null')
    except (ValueError, UnicodeDecodeError):
        raise _bad('The request body is not valid JSON.') from None
    if not isinstance(body, dict):
        raise _bad('The request body must be a JSON object.')
    unknown = sorted(set(body) - SPAWN_KEYS)
    if unknown:
        raise _bad(f'Unknown field(s): {", ".join(unknown)}.')
    missing = [k for k in ('role', 'name', 'model', 'prompt') if k not in body]
    if missing:
        raise _bad(f'Missing field(s): {", ".join(missing)}.')
    role, name, model = body['role'], body['name'], body['model']
    mode, prompt = body.get('permission_mode', 'auto'), body['prompt']
    channel = body.get('channel', True)
    if role not in ROLES:
        raise _bad(f'role must be one of {", ".join(ROLES)}.')
    # A leading dash is refused: claude could read the value as a flag of its own.
    if not isinstance(name, str) or not NAME.fullmatch(name) or name.startswith('-'):
        raise _bad(
            'name must be 1–60 letters, digits, dots, dashes or underscores, '
            'not starting with a dash.'
        )
    if not isinstance(model, str) or not MODEL.fullmatch(model) or model.startswith('-'):
        raise _bad('model must be a model name like "sonnet" or "opus".')
    if mode not in PERMISSION_MODES:
        raise _bad(f'permission_mode must be one of {", ".join(PERMISSION_MODES)}.')
    if not isinstance(prompt, str) or not prompt:
        raise _bad('prompt must be a non-empty string.')
    if not isinstance(channel, bool):
        raise _bad('channel must be true or false.')
    if ('cwd' in body) == ('repo' in body):
        raise _bad('Give exactly one of cwd and repo.')
    if 'repo' in body:
        raise SessionError(
            501,
            'not_supported',
            'This runner cannot start a session from a repo yet. Pass cwd instead.',
        )
    cwd = body['cwd']
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise _bad('cwd must be an absolute path.')
    if not Path(cwd).is_dir():
        raise _bad(f'The folder {cwd} does not exist, so no session was started.')
    return SpawnRequest(role, name, model, mode, prompt, Path(cwd), channel)


def _iso_from_ms(ms: Any) -> str | None:
    if not isinstance(ms, int | float):
        return None
    dt = datetime.fromtimestamp(ms / 1000, UTC)
    return dt.isoformat(timespec='seconds').replace('+00:00', 'Z')


def project_dir(cwd: str) -> str:
    """Claude Code's transcript folder for a cwd: every non-alphanumeric character → `-`."""
    return re.sub(r'[^A-Za-z0-9]', '-', cwd)


def running(row: Mapping[str, Any]) -> bool:
    """Alive: a process is up (`status` is set), or it is still starting (working, no pid)."""
    return bool(row.get('status')) or row.get('state') in ('working', 'blocked')


class Sessions:
    """The runner's sessions. Thread-safe: the HTTP server calls it from many threads."""

    def __init__(
        self,
        settings: Settings,
        state_dir: Path,
        *,
        home: Path | None = None,
        cgroup_of: Callable[[int], str] = cgroup_of,
        channel_wait: float = CHANNEL_WAIT,
        poll: float = 0.25,
        token: Callable[[], str] = lambda: uuid.uuid4().hex[:12],
        spawn_timeout: float = SPAWN_TIMEOUT,
    ) -> None:
        if not settings.claude.is_absolute():
            raise ValueError(f'The path to claude must be absolute, not "{settings.claude}".')
        self.settings = settings
        self.state_dir = state_dir
        self.state_file = state_dir / 'sessions.json'
        self.home = home or Path.home()
        self.cgroup_of = cgroup_of
        self.channel_wait = channel_wait
        self.poll = poll
        self.token = token
        self.spawn_timeout = spawn_timeout
        self._lock = threading.Lock()  # the cap check and its reservation
        self._write_lock = threading.Lock()  # the state file
        self._pending = 0  # worker spawns past the cap check that are not in the roster yet
        self._last_active = 0
        # Set by serve(): reads plan usage now. The contract reads usage before each worker spawn.
        self.refresh_usage: Callable[[], None] | None = None
        self.records: dict[str, dict[str, Any]] = self._load()

    # --- the state file -------------------------------------------------------------------

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.state_file.read_text('utf-8'))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            raise StateFileError(
                f'The runner cannot read its state file {self.state_file} ({e}). '
                'Fix or move it; without it the runner forgets which sessions are workers.'
            ) from None
        sessions = data.get('sessions') if isinstance(data, dict) else None
        if not isinstance(sessions, dict):
            raise StateFileError(
                f'The state file {self.state_file} has no "sessions" map. Fix or move it.'
            )
        return sessions

    def _remember(self, record: dict[str, Any]) -> None:
        """Keep a session the runner started. A session whose full id is unknown is kept under
        `short:<short-id>`, so it still counts toward the cap once it shows up."""
        key = record['id'] or f'short:{record["short_id"]}'
        with self._write_lock:
            # Copy, then swap: readers on other threads never see a dict being changed. Memory
            # first, so the cap holds in this process even when the disk write fails.
            self.records = records = {**self.records, key: record}
            try:
                self._write(records)
            except OSError as e:
                print(
                    f'marcel-runner: could not write the state file {self.state_file}: '
                    f'{e.strerror or e}. After a restart the runner would forget session '
                    f'{record["short_id"]}.',
                    file=sys.stderr,
                    flush=True,
                )

    def _write(self, records: dict[str, dict[str, Any]]) -> None:
        """Write to a temp file, flush it to disk, then swap it in: a crash leaves the old file
        or the new one, never half of one."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_name(f'.{self.state_file.name}.{os.getpid()}.tmp')
        with tmp.open('w', encoding='utf-8') as f:
            f.write(json.dumps({'sessions': records}, indent=2) + '\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.state_file)

    def _record_for(self, row: Mapping[str, Any]) -> dict[str, Any] | None:
        return self.records.get(str(row.get('sessionId'))) or self.records.get(
            f'short:{row.get("id")}'
        )

    # --- running claude -------------------------------------------------------------------

    def _claude(
        self, *args: str, cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        cmd = f'`claude {args[0]}`'
        try:
            return subprocess.run(
                [str(self.settings.claude), *args],
                cwd=cwd,
                env=scrubbed_env(os.environ, self.home),
                capture_output=True,
                text=True,
                timeout=timeout or self.settings.timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise SessionError(
                503, 'claude_timeout', f'Claude did not answer {cmd} in time. Try again.'
            ) from None
        except OSError as e:
            raise SessionError(
                503,
                'claude_unavailable',
                f'The runner could not run {self.settings.claude}: {e.strerror or e}.',
            ) from None

    def roster(self, all: bool = True) -> list[dict[str, Any]]:
        """Background rows from `claude agents --json [--all]`, as Claude prints them."""
        p = self._claude('agents', '--json', *(['--all'] if all else []))
        if p.returncode != 0:
            said = (p.stderr or p.stdout).strip() or f'exit code {p.returncode}'
            raise SessionError(
                503, 'roster_unavailable', 'Claude could not list its sessions.', said
            )
        try:
            rows = json.loads(p.stdout)
        except ValueError:
            raise SessionError(
                503,
                'roster_unavailable',
                'Claude listed its sessions in a form the runner cannot read.',
            ) from None
        return [r for r in rows if isinstance(r, dict) and r.get('kind') == 'background']

    # --- the worker cap -------------------------------------------------------------------

    def _running_workers(self) -> int:
        count = sum(
            1
            for row in self.roster()
            if running(row) and (self._record_for(row) or {}).get('role') == 'worker'
        )
        self._last_active = count
        return count

    def active_workers(self) -> int:
        """Running workers this runner started, for `/health`. Falls back to the last count
        when Claude cannot list its sessions (`/health` then says why anyway)."""
        try:
            return self._running_workers()
        except SessionError:
            return self._last_active

    # --- spawn ----------------------------------------------------------------------------

    def _supervisor_reason(self) -> str | None:
        health = collect_health(self.settings, lambda: 0, self.cgroup_of)
        return None if health['ok'] else health.get('reason', 'The runner cannot start sessions.')

    def spawn(self, req: SpawnRequest) -> dict[str, Any]:
        # The supervisor is checked before every spawn: the runner may have started without it.
        reason = self._supervisor_reason()
        if reason:
            raise SessionError(422, 'no_supervisor', f'No session was started. {reason}')
        worker = req.role == 'worker'
        if worker:
            with self._lock:
                try:
                    active = self._running_workers()
                except SessionError as e:
                    raise SessionError(
                        422,
                        'no_supervisor',
                        'No session was started: the runner could not count the running workers.',
                        e.detail or e.message,
                    ) from None
                if active + self._pending >= self.settings.cap:
                    raise SessionError(
                        409,
                        'cap_reached',
                        f'{self.settings.cap} workers are already running, the most allowed. '
                        'Wait for one to finish or stop one, then try again.',
                    )
                self._pending += 1
            self._refresh_usage_in_background()
        try:
            return self._launch(req)
        finally:
            if worker:
                with self._lock:
                    self._pending -= 1

    def _refresh_usage_in_background(self) -> None:
        """Best effort, never in the spawn's way: a slow or failed usage read cannot delay or
        fail the spawn. The probe keeps the last values and marks them stale on a failure."""
        refresh = self.refresh_usage
        if refresh is None:
            return

        def run() -> None:
            with contextlib.suppress(Exception):
                refresh()

        threading.Thread(target=run, name='usage-before-spawn', daemon=True).start()

    def _launch(self, req: SpawnRequest) -> dict[str, Any]:
        debug_file = self.state_dir / 'debug' / f'{req.name}-{self.token()}.log'
        debug_file.parent.mkdir(parents=True, exist_ok=True)
        argv = spawn_argv(
            self.settings.claude,
            name=req.name,
            model=req.model,
            permission_mode=req.permission_mode,
            prompt=req.prompt,
            debug_file=debug_file,
            channel=req.channel,
            role=req.role,
        )
        since = time.time() * 1000 - 1000  # rows started from here on may be this spawn's
        try:
            p = self._claude(*argv[1:], cwd=req.cwd, timeout=self.spawn_timeout)
        except SessionError as e:
            if e.code == 'claude_timeout':  # the session may have started all the same
                raise self._contain(req, None, since, e) from None
            raise
        said = (p.stderr or p.stdout).strip()
        if p.returncode != 0:
            if 'not trusted' in said.lower():
                raise SessionError(
                    422,
                    'not_trusted',
                    f'The workspace {req.cwd} is not trusted yet, so no session was started. '
                    'Run claude there once and accept the trust prompt.',
                    said,
                )
            raise SessionError(422, 'exited', 'Claude refused to start the session.', said or None)
        m = BACKGROUNDED.search(p.stdout)
        if m is None:
            error = SessionError(
                422, 'exited', 'Claude did not say which session it started.', said or None
            )
            raise self._contain(req, None, since, error)
        short = m.group(1)
        try:
            row = self._resolve(short)
        except SessionError as e:
            raise self._contain(req, short, since, e) from None
        record = self._record(req, row['sessionId'], short)
        self._remember(record)
        registered = False
        if req.channel:
            registered = self._wait_channel(short, debug_file)
        return {
            'session_id': record['id'],
            'short_id': short,
            'name': req.name,
            'cwd': str(req.cwd),
            'channel_registered': registered,
        }

    @staticmethod
    def _record(req: SpawnRequest, session_id: str | None, short: str) -> dict[str, Any]:
        return {
            'id': session_id,
            'short_id': short,
            'role': req.role,
            'name': req.name,
            'task': None,
            'permission_mode': req.permission_mode,
            'model': req.model,
            'cwd': str(req.cwd),
            'channel': req.channel,
            'started_at': datetime.now(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z'),
        }

    def _contain(
        self, req: SpawnRequest, short: str | None, since: float, error: SessionError
    ) -> SessionError:
        """The spawn failed after claude may have started a session. Record what can be found,
        so it counts toward the cap, then stop it. `error` comes back saying what happened.

        With the short id, that session. Without it, sessions with the name this spawn gave,
        started since the spawn began, that the runner does not know yet."""
        try:
            rows = self.roster()
        except SessionError:
            rows = []
        if short is not None:
            found = [r for r in rows if r.get('id') == short]
        else:
            found = [
                r
                for r in rows
                if r.get('name') == req.name
                and running(r)
                and (r.get('startedAt') or 0) >= since
                and self._record_for(r) is None
            ]
        for r in found:
            self._remember(self._record(req, r.get('sessionId'), str(r.get('id'))))
        if short is not None and not found:
            self._remember(self._record(req, None, short))
        targets = [str(r.get('id')) for r in found] or ([short] if short else [])
        if not targets:
            return error
        said = [w for w in (self._stop(t) for t in targets) if w is not None]
        if not said:
            return SessionError(
                error.status,
                error.code,
                f'{error.message} The runner stopped the session it had started.',
                error.detail,
            )
        detail = '\n'.join(filter(None, [error.detail, f'claude stop said: {"; ".join(said)}']))
        return SessionError(
            error.status,
            error.code,
            f'{error.message} A session may still be running and the runner could not stop it, '
            'so it counts toward the worker cap.',
            detail,
        )

    def _resolve(self, short: str) -> dict[str, Any]:
        """The full session id for a short id (SP2: the row is there as soon as `--bg` returns)."""
        for attempt in range(5):
            for row in self.roster():
                if row.get('id') == short and isinstance(row.get('sessionId'), str):
                    return row
            time.sleep(0.1 * (attempt + 1))
        raise SessionError(
            422,
            'exited',
            f'Claude started session {short}, but it is not in its own list. '
            'It may have exited at once.',
        )

    def _wait_channel(self, short: str, debug_file: Path) -> bool:
        """True once the debug log says the marcel channel is registered. A `skipped` line or
        no line within the wait stops the session and fails the spawn."""
        deadline = time.monotonic() + self.channel_wait
        while True:
            for line in _read_lines(debug_file):
                if CHANNEL_SERVER not in line:
                    continue
                if REGISTERED in line:
                    return True
                if SKIPPED in line:
                    said = self._stop(short)
                    raise SessionError(
                        422,
                        'channel_skipped',
                        'The session started without its marcel channel, so it could not hear '
                        f'you. {_stopped(said)}',
                        _join(
                            line[line.find('MCP server') :] if 'MCP server' in line else line, said
                        ),
                    )
            if time.monotonic() >= deadline:
                said = self._stop(short)
                raise SessionError(
                    422,
                    'channel_timeout',
                    f'The session did not connect its marcel channel within '
                    f'{self.channel_wait:g} s. {_stopped(said)}',
                    _join(None, said),
                )
            time.sleep(self.poll)

    def _stop(self, short: str) -> str | None:
        """`claude stop`. None when it worked, else what went wrong, in claude's words."""
        try:
            p = self._claude('stop', short)
        except SessionError as e:
            return e.message
        if p.returncode != 0:
            return (p.stderr or p.stdout).strip() or f'exit code {p.returncode}'
        return None

    # --- list, get, stop ------------------------------------------------------------------

    def _job(self, short: str, session_id: str) -> dict[str, Any]:
        """Best effort: Claude Code's undocumented job file (`needs`, `detail`, `output`)."""
        try:
            job = json.loads((self.home / '.claude' / 'jobs' / short / 'state.json').read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(job, dict) or job.get('sessionId') not in (None, session_id):
            return {}
        return job

    def _remote_url(self, session_id: str, cwd: str) -> str | None:
        """The claude.ai page Remote Control bridged the session to: the last
        `remote_session_change` attachment in its transcript (SP2). None when there is none."""
        path = self.home / '.claude' / 'projects' / project_dir(cwd) / f'{session_id}.jsonl'
        url: str | None = None
        for line in _read_lines(path):
            if 'remote_session_change' not in line:
                continue
            try:
                attachment = json.loads(line).get('attachment') or {}
            except (ValueError, AttributeError):
                continue
            if attachment.get('type') == 'remote_session_change':
                value = attachment.get('url')
                url = value if isinstance(value, str) and value.startswith('https://') else None
        return url

    def normalise(self, row: Mapping[str, Any]) -> dict[str, Any]:
        """One `agents --json` row → the contract's `Session`."""
        sid = str(row.get('sessionId'))
        short = str(row.get('id') or sid[:8])
        out: dict[str, Any] = {
            'id': sid,
            'short_id': short,
            'name': str(row.get('name') or ''),
            'state': row.get('state'),
            'cwd': str(row.get('cwd') or ''),
        }
        record = self._record_for(row)
        if record:
            out['role'] = record['role']
        if row.get('status'):
            out['status'] = row['status']
        if row.get('waitingFor'):
            out['waiting_for'] = row['waitingFor']
        if isinstance(row.get('pid'), int):
            out['pid'] = row['pid']
        started = _iso_from_ms(row.get('startedAt'))
        if started:
            out['started_at'] = started
        job = self._job(short, sid)
        for key in ('needs', 'detail'):
            if isinstance(job.get(key), str) and job[key]:
                out[key] = job[key]
        result = (
            (job.get('output') or {}).get('result') if isinstance(job.get('output'), dict) else None
        )
        if isinstance(result, str) and result:
            out['result'] = result
        return out

    def list_sessions(self, all: bool = True) -> list[dict[str, Any]]:
        rows = [r for r in self.roster(all) if r.get('state') in STATES and r.get('sessionId')]
        rows.sort(key=lambda r: r.get('startedAt') or 0, reverse=True)
        return [self.normalise(r) for r in rows]

    def _find(self, ref: str) -> dict[str, Any]:
        for row in self.roster():
            if ref and ref in (row.get('sessionId'), row.get('id')):
                return row
        raise SessionError(404, 'not_found', f'There is no session {ref} on this machine.')

    def get(self, ref: str) -> dict[str, Any]:
        row = self._find(ref)
        session = self.normalise(row)
        url = self._remote_url(session['id'], session['cwd'])
        if url:
            session['remote_url'] = url
        return session

    def stop(self, ref: str) -> dict[str, Any]:
        row = self._find(ref)
        p = self._claude('stop', str(row.get('id')))
        if p.returncode != 0:
            said = (p.stderr or p.stdout).strip() or f'exit code {p.returncode}'
            raise SessionError(502, 'stop_failed', 'Claude could not stop the session.', said)
        return self.get(str(row['sessionId']))

    # --- HTTP -----------------------------------------------------------------------------

    def route(
        self, method: str, path: str, query: Mapping[str, list[str]], body: bytes
    ) -> tuple[int, Any]:
        """Answer one request under `/sessions`. Returns (status, JSON body)."""
        try:
            return self._route(method, path, query, body)
        except SessionError as e:
            return e.status, e.body()

    def _route(
        self, method: str, path: str, query: Mapping[str, list[str]], body: bytes
    ) -> tuple[int, Any]:
        parts = [unquote(p) for p in path.strip('/').split('/')]
        if parts == ['sessions']:
            if method == 'GET':
                flag = (query.get('all') or ['true'])[-1].lower()
                return 200, self.list_sessions(all=flag not in ('false', '0', 'no'))
            if method == 'POST':
                return 201, self.spawn(parse_spawn(body))
            raise _not_allowed(path, 'GET or POST', method)
        if len(parts) == 2:
            if method == 'GET':
                return 200, self.get(parts[1])
            raise _not_allowed(path, 'GET', method)
        if len(parts) == 3 and parts[2] == 'stop':
            if method == 'POST':
                return 200, self.stop(parts[1])
            raise _not_allowed(path, 'POST', method)
        raise SessionError(404, 'not_found', f'The runner has no {path}.')


def _stopped(said: str | None) -> str:
    if said is None:
        return 'The runner stopped it.'
    return 'The runner could not stop it, so it still runs and counts toward the worker cap.'


def _join(detail: str | None, said: str | None) -> str | None:
    if said is None:
        return detail
    return '\n'.join(filter(None, [detail, f'claude stop said: {said}']))


def _not_allowed(path: str, allowed: str, method: str) -> SessionError:
    return SessionError(405, 'method_not_allowed', f'{path} only answers {allowed}, not {method}.')


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text('utf-8', errors='replace').splitlines()
    except OSError:
        return []
