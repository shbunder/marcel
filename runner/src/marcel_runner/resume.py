"""Resume: wake a stopped session with a new prompt (`POST /sessions/{id}/resume`).

A live session is never sent text by the runner: there is no `claude send`, so live steering goes
hub → channel and a resume of a running session answers 409 `running`. Between turns the runner
runs `claude --bg --resume <uuid> … -- "<prompt>"`, which wakes the same session (SP2).

The race (SP2): a resume issued ~1 s after `claude stop` still finds the session live in Claude's
supervisor and starts a *copy* instead (`note: … started a copy as <id>`). The runner first waits
until the id has left `~/.claude/daemon/roster.json`, and if Claude still starts a copy, it stops
the copy and answers 409 `resume_copied` rather than hand back a different session.
"""

import contextlib
import json
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from marcel_runner.sessions import (
    ALLOWED_TOOLS,
    BACKGROUNDED,
    CHANNEL,
    SessionError,
    Sessions,
    running,
)

# Brain and side sessions also get Marcel's brain tools, by name, never a wildcard
# (contracts/runner-api.yaml). Workers never do.
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
)
ROSTER_WAIT = 10.0  # SP2: the id was gone from roster.json 3 s after `claude stop`
COPY = re.compile(r'started a copy as ([0-9a-f]{8})')


def allowed_tools(role: str | None) -> tuple[str, ...]:
    """What `--allowedTools` lists for a session with this role."""
    return ALLOWED_TOOLS + (BRAIN_TOOLS if role in ('brain', 'side') else ())


def resume_argv(
    claude: Path,
    *,
    session_id: str,
    prompt: str,
    debug_file: Path,
    channel: bool,
    tools: tuple[str, ...],
) -> list[str]:
    """`claude --bg --resume <uuid>`, with the same `--channels` and `--allowedTools` as spawn.
    The prompt goes after `--` (SP1)."""
    argv = [str(claude), '--bg', '--resume', session_id]
    if channel:
        argv += ['--channels', CHANNEL]
    return argv + ['--allowedTools', *tools, '--debug-file', str(debug_file), '--', prompt]


def parse_body(raw: bytes, allowed: set[str], required: set[str]) -> dict[str, Any]:
    """A JSON object with only `allowed` keys and every `required` one. 400 otherwise."""
    try:
        body = json.loads(raw or b'null')
    except (ValueError, UnicodeDecodeError):
        raise SessionError(400, 'bad_request', 'The request body is not valid JSON.') from None
    if not isinstance(body, dict):
        raise SessionError(400, 'bad_request', 'The request body must be a JSON object.')
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise SessionError(400, 'bad_request', f'Unknown field(s): {", ".join(unknown)}.')
    missing = sorted(required - set(body))
    if missing:
        raise SessionError(400, 'bad_request', f'Missing field(s): {", ".join(missing)}.')
    prompt = body.get('prompt')
    if 'prompt' in body and (not isinstance(prompt, str) or not prompt):
        raise SessionError(400, 'bad_request', 'prompt must be a non-empty string.')
    return body


def session_cwd(row: dict[str, Any], record: dict[str, Any] | None) -> Path:
    """The folder a session runs in: `--resume` finds a conversation only from its own folder."""
    cwd = row.get('cwd') or (record or {}).get('cwd')
    if not isinstance(cwd, str) or not Path(cwd).is_dir():
        raise SessionError(
            422,
            'exited',
            f'The folder the session ran in ({cwd or "unknown"}) is gone, so it cannot go on.',
        )
    return Path(cwd)


def check_supervisor(sessions: Sessions) -> None:
    reason = sessions._supervisor_reason()
    if reason:
        raise SessionError(422, 'no_supervisor', f'Nothing was started. {reason}')


@contextlib.contextmanager
def worker_slot(sessions: Sessions, worker: bool) -> Iterator[None]:
    """Hold a place under the worker cap while a worker wakes up, as spawn does: a resumed
    worker runs again, so it counts. Brain and side sessions never count."""
    if not worker:
        yield
        return
    with sessions._lock:
        try:
            active = sessions._running_workers()
        except SessionError as e:
            raise SessionError(
                422,
                'no_supervisor',
                'Nothing was started: the runner could not count the running workers.',
                e.detail or e.message,
            ) from None
        if active + sessions._pending >= sessions.settings.cap:
            raise SessionError(
                409,
                'cap_reached',
                f'{sessions.settings.cap} workers are already running, the most allowed. '
                'Wait for one to finish or stop one, then try again.',
            )
        sessions._pending += 1
    try:
        yield
    finally:
        with sessions._lock:
            sessions._pending -= 1


def wait_off_roster(home: Path, ids: tuple[str, ...], wait: float, poll: float) -> None:
    """Wait (at most `wait` seconds) until none of `ids` is in Claude's daemon roster file. Its
    format is not recorded, so the runner looks for the ids in its text. No file: nothing to
    wait for. Past the wait the resume goes ahead; a copy is still caught afterwards."""
    path = home / '.claude' / 'daemon' / 'roster.json'
    deadline = time.monotonic() + wait
    while True:
        try:
            text = path.read_text('utf-8', errors='replace')
        except OSError:
            return
        if not any(i in text for i in ids) or time.monotonic() >= deadline:
            return
        time.sleep(poll)


def resume(sessions: Sessions, ref: str, raw: bytes) -> dict[str, Any]:
    """Wake session `ref` with the prompt in `raw`. Returns the contract's `Session`."""
    row = sessions._find(ref)
    prompt = parse_body(raw, {'prompt'}, {'prompt'})['prompt']
    session_id, short = str(row.get('sessionId')), str(row.get('id'))
    if running(row):
        raise SessionError(
            409,
            'running',
            f'Session {short} is still running. Send it the message through its channel instead.',
        )
    check_supervisor(sessions)
    record = sessions._record_for(row)
    role = (record or {}).get('role')
    channel = (record or {}).get('channel', True) is not False
    cwd = session_cwd(row, record)
    with worker_slot(sessions, role == 'worker'):
        wait_off_roster(sessions.home, (session_id, short), ROSTER_WAIT, sessions.poll)
        debug_file = sessions.state_dir / 'debug' / f'resume-{short}-{sessions.token()}.log'
        debug_file.parent.mkdir(parents=True, exist_ok=True)
        argv = resume_argv(
            sessions.settings.claude,
            session_id=session_id,
            prompt=prompt,
            debug_file=debug_file,
            channel=channel,
            tools=allowed_tools(role),
        )
        p = sessions._claude(*argv[1:], cwd=cwd, timeout=sessions.spawn_timeout)
        said = (p.stderr or p.stdout).strip()
        if p.returncode != 0:
            raise SessionError(422, 'exited', 'Claude refused to resume the session.', said or None)
        started = BACKGROUNDED.search(p.stdout)
        copy = COPY.search(p.stdout)
        other = copy.group(1) if copy else None
        if other is None and started is not None and started.group(1) != short:
            other = started.group(1)
        if other is not None:
            stop_said = sessions._stop(other)
            outcome = (
                'The runner stopped the copy.'
                if stop_said is None
                else 'The runner could not stop the copy, so it may still be running.'
            )
            raise SessionError(
                409,
                'resume_copied',
                f'Session {short} was still winding down, so Claude started a copy instead of '
                f'waking it. {outcome} Try again in a few seconds.',
                '\n'.join(filter(None, [said, stop_said and f'claude stop said: {stop_said}'])),
            )
        if started is None:
            raise SessionError(
                422, 'exited', 'Claude did not say which session it woke.', said or None
            )
        if channel:
            sessions._wait_channel(short, debug_file)
    return sessions.get(session_id)
