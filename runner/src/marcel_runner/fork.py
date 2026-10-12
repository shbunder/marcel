"""Fork: a new background session with another session's history (`POST /sessions/{id}/fork`).

SP7: `claude --bg --resume <uuid> --fork-session --name <name> --channels … -- "<prompt>"` starts
an independent session that shares the history up to the fork, gets its own channel connection and
never writes into the original. It works on a live session too, and prints no `note:` line.

The fork's role is `side`, so it never counts toward the worker cap. A worker is never forked
(409 `not_forkable`): its fork would be an uncounted session with the brain tools. It runs with
the permission mode the runner recorded for the session it forks from (`manual`, the strictest,
when the runner did not start that session), and that session's model unless the request names
one.
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marcel_runner.resume import check_supervisor, parse_body, session_cwd
from marcel_runner.sessions import (
    BACKGROUNDED,
    CHANNEL,
    MODEL,
    NAME,
    PERMISSION_MODES,
    SessionError,
    Sessions,
    SpawnRequest,
    allowed_tools,
)

ROLE = 'side'


@dataclass(frozen=True)
class ForkRequest:
    name: str
    prompt: str
    model: str | None


def parse_fork(raw: bytes) -> ForkRequest:
    """Check a fork body against `ForkRequest` (contracts/runner-api.yaml)."""
    body = parse_body(raw, {'name', 'prompt', 'model'}, {'name', 'prompt'})
    name, model = body['name'], body.get('model')
    # A leading dash is refused: claude could read the value as a flag of its own.
    if not isinstance(name, str) or not NAME.fullmatch(name) or name.startswith('-'):
        raise SessionError(
            400,
            'bad_request',
            'name must be 1–60 letters, digits, dots, dashes or underscores, '
            'not starting with a dash.',
        )
    if model is not None and (
        not isinstance(model, str) or not MODEL.fullmatch(model) or model.startswith('-')
    ):
        raise SessionError(400, 'bad_request', 'model must be a model name like "sonnet".')
    return ForkRequest(name, body['prompt'], model)


def fork_argv(
    claude: Path,
    *,
    session_id: str,
    name: str,
    model: str | None,
    permission_mode: str,
    prompt: str,
    debug_file: Path,
    channel: bool,
) -> list[str]:
    """The fork's command line (SP7). Without a known model, `--model` is left out and Claude
    keeps the forked session's own."""
    argv = [str(claude), '--bg', '--resume', session_id, '--fork-session', '--name', name]
    if model:
        argv += ['--model', model]
    argv += ['--permission-mode', permission_mode]
    if channel:
        argv += ['--channels', CHANNEL]
    argv += ['--allowedTools', *allowed_tools(ROLE), '--debug-file', str(debug_file)]
    return argv + ['--', prompt]


def fork(sessions: Sessions, ref: str, raw: bytes) -> dict[str, Any]:
    """Fork session `ref`. Returns the contract's `Spawned` for the new session."""
    row = sessions._find(ref)
    body = parse_fork(raw)
    source = sessions._record_for(row) or {}
    if source.get('role') == 'worker':
        # A fork is a side session: uncounted, with the brain tools. A worker's fork would be
        # an uncapped worker with powers the worker itself never had.
        raise SessionError(
            409,
            'not_forkable',
            f'Session {row.get("id")} is a worker, and a worker cannot be forked. '
            'Start a new task instead.',
        )
    check_supervisor(sessions)
    mode = source.get('permission_mode')
    req = SpawnRequest(
        role=ROLE,
        name=body.name,
        model=body.model or source.get('model') or '',
        permission_mode=mode if mode in PERMISSION_MODES else 'manual',
        prompt=body.prompt,
        cwd=session_cwd(row, source),
        channel=source.get('channel', True) is not False,
    )
    debug_file = sessions.state_dir / 'debug' / f'{req.name}-{sessions.token()}.log'
    debug_file.parent.mkdir(parents=True, exist_ok=True)
    argv = fork_argv(
        sessions.settings.claude,
        session_id=str(row.get('sessionId')),
        name=req.name,
        model=req.model or None,
        permission_mode=req.permission_mode,
        prompt=req.prompt,
        debug_file=debug_file,
        channel=req.channel,
    )
    since = time.time() * 1000 - 1000  # rows started from here on may be this fork's
    try:
        p = sessions._claude(*argv[1:], cwd=req.cwd, timeout=sessions.spawn_timeout)
    except SessionError as e:
        if e.code == 'claude_timeout':  # the fork may have started all the same
            raise sessions._contain(req, None, since, e) from None
        raise
    said = (p.stderr or p.stdout).strip()
    if p.returncode != 0:
        raise SessionError(422, 'exited', 'Claude refused to start the fork.', said or None)
    m = BACKGROUNDED.search(p.stdout)
    if m is None:
        error = SessionError(
            422, 'exited', 'Claude did not say which session it started.', said or None
        )
        raise sessions._contain(req, None, since, error)
    short = m.group(1)
    if short == row.get('id'):  # never stop the session it was forked from
        raise SessionError(
            422, 'exited', 'Claude resumed the session instead of forking it.', said or None
        )
    try:
        new = sessions._resolve(short)
    except SessionError as e:
        raise sessions._contain(req, short, since, e) from None
    record = sessions._record(req, new['sessionId'], short)
    sessions._remember(record)
    registered = sessions._wait_channel(short, debug_file) if req.channel else False
    return {
        'session_id': record['id'],
        'short_id': short,
        'name': req.name,
        'cwd': str(req.cwd),
        'channel_registered': registered,
    }
