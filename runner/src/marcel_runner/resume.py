"""Resume: wake a stopped session with a new prompt (`POST /sessions/{id}/resume`).

A live session is never sent text by the runner: there is no `claude send`, so live steering goes
hub → channel and a resume of a running session answers 409 `running`. Between turns the runner
runs `claude --bg --resume <uuid> -- "<prompt>"`, with no other flag: since 2.1.296 a session keeps
its own saved options (`--setting-sources`, permission mode, channels, allowed tools, debug file,
model) and flags on a resume start a copy instead (recorded on the NUC, 2026-10-13). So a resumed
session still loads only project and local settings (S-03.10) without the runner passing it again.
The id must be the full session id: a short id starts a copy too. The woken session writes on in the
debug file it was started with, which is where the runner sees its channel register again.

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
    BACKGROUNDED,
    SessionError,
    Sessions,
    running,
)

ROSTER_WAIT = 10.0  # SP2: the id was gone from roster.json 3 s after `claude stop`
COPY = re.compile(r'started a copy as ([0-9a-f]{8})')


def resume_argv(claude: Path, *, session_id: str, prompt: str) -> list[str]:
    """`claude --bg --resume <uuid> -- "<prompt>"`: no flags, so Claude reuses the session's saved
    options rather than start a copy (2.1.296). The prompt goes after `--` (SP1)."""
    return [str(claude), '--bg', '--resume', session_id, '--', prompt]


def saved_debug_file(home: Path, short: str, record: dict[str, Any] | None) -> Path | None:
    """The debug file the session was started with: the runner's record, else the `--debug-file`
    Claude saved with the session (`~/.claude/jobs/<short>/state.json`, `respawnFlags`)."""
    if record and isinstance(record.get('debug_file'), str):
        return Path(record['debug_file'])
    try:
        flags = json.loads((home / '.claude' / 'jobs' / short / 'state.json').read_text('utf-8'))
        flags = flags.get('respawnFlags') if isinstance(flags, dict) else None
    except (OSError, ValueError):
        return None
    if isinstance(flags, list) and '--debug-file' in flags:
        i = flags.index('--debug-file')
        if i + 1 < len(flags) and isinstance(flags[i + 1], str):
            return Path(flags[i + 1])
    return None


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
    debug_file = saved_debug_file(sessions.home, short, record) if channel else None
    if channel and debug_file is None:
        raise SessionError(
            422,
            'exited',
            f'Session {short} was not woken: the runner cannot find the debug log it was started '
            'with, so it could not tell whether its channel came back.',
        )
    with worker_slot(sessions, role == 'worker'):
        wait_off_roster(sessions.home, (session_id, short), ROSTER_WAIT, sessions.poll)
        start = _size(debug_file)
        argv = resume_argv(sessions.settings.claude, session_id=session_id, prompt=prompt)
        p = sessions._claude(*argv[1:], cwd=cwd, timeout=sessions.spawn_timeout)
        said = '\n'.join(filter(None, [p.stdout.strip(), p.stderr.strip()]))
        if p.returncode != 0:
            raise SessionError(422, 'exited', 'Claude refused to resume the session.', said or None)
        started = BACKGROUNDED.search(p.stdout)
        copy = COPY.search(said)  # 2.1.296 says it on stderr
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
        if debug_file is not None:
            sessions._wait_channel(short, debug_file, start)
    return sessions.get(session_id)


def _size(path: Path | None) -> int:
    try:
        return path.stat().st_size if path is not None else 0
    except OSError:
        return 0
