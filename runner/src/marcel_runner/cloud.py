"""Cloud sessions on claude.ai/code: create one, and queue a follow-up for it (SP3).

- **Create** (`POST /cloud/sessions`): `claude --cloud -- "<prompt>"`. It refuses without a
  terminal and cannot be combined with `-p`, so it runs on a pty. It prints
  `Created cloud session: <title> View: https://claude.ai/code/session_<id>?… Resume with: …` and
  exits. The runner reads the id, the title and the URL from that line.
- **Send** (`POST /cloud/sessions/{id}/messages`): `claude -p --cloud <id> --output-format json
  -- "<text>"`, headless. It answers `{"ok":true,"session_id":…,"url":…}`: an ack, not an answer.

The prompt and the text come from users, so they only ever reach `claude` as one argv entry after
`--`, never through a shell. The session id is checked against `^session_[A-Za-z0-9]+$` before it
goes into argv. The URL is only handed back when it is `https://claude.ai/code/<that id>`.

When Claude's output cannot be read, the error holds it, with terminal escapes stripped, cut to
`MAX_OUTPUT` characters, and every long secret-looking word of the prompt or text (the task's own
token travels in the prompt) replaced by `[redacted]`.
"""

import contextlib
import json
import os
import pty
import re
import select
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from marcel_runner.claude_env import scrubbed_env
from marcel_runner.sessions import SessionError

SPAWN_TIMEOUT = 60.0  # SP3: the create call came back within seconds
SEND_TIMEOUT = 60.0
MAX_OUTPUT = 2000  # characters of Claude's output kept in an error
MAX_READ = 1 << 20  # bytes read from the pty before the runner stops listening

SESSION_ID = re.compile(r'^session_[A-Za-z0-9]+$')
CREATED = re.compile(
    r'Created cloud session:\s*(?P<title>.*?)\s*View:\s*(?P<url>https://\S+)', re.DOTALL
)
FOUND_ID = re.compile(r'session_[A-Za-z0-9]+')
# CSI and OSC sequences, two-byte escapes (`ESC 7`, `ESC 8`), then any other control character
# except newline and tab (SP3's output ends in `\x0f\x1b7\x1b8`).
ESCAPES = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_7-9=>]')
CONTROLS = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')
SECRETISH = re.compile(r'[A-Za-z0-9_\-.~+/=]{16,}')

Runner = Callable[[list[str], dict[str, str], Path, float], tuple[int | None, str]]


def clean(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Claude's output fit for an error: no escapes, no secrets, at most `MAX_OUTPUT` chars."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = CONTROLS.sub('', ESCAPES.sub('', text))
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, '[redacted]')
    text = text.strip()
    if len(text) > MAX_OUTPUT:
        text = text[: MAX_OUTPUT - 1] + '…'
    return text


def secrets_in(text: str) -> tuple[str, ...]:
    """The words of a prompt that could be a token: 16 or more token-like characters in a row."""
    return tuple(set(SECRETISH.findall(text)))


def check_id(session_id: str) -> str:
    if not SESSION_ID.match(session_id):
        raise SessionError(
            400, 'bad_request', 'That is not a cloud session id. It looks like session_01ABC….'
        )
    return session_id


def page_url(url: Any, session_id: str) -> str | None:
    """`https://claude.ai/code/<session_id>` when `url` is that page (any query dropped), else
    None. A link to anywhere else is never handed on to the app."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if (
        parts.scheme != 'https'
        or parts.hostname != 'claude.ai'
        or parts.username is not None
        or parts.password is not None
        or port is not None
        or parts.path != f'/code/{session_id}'
    ):
        return None
    return f'https://claude.ai/code/{session_id}'


def run_on_pty(
    argv: list[str], env: dict[str, str], cwd: Path, timeout: float
) -> tuple[int | None, str]:
    """Run `argv` with a pty as its terminal; return (exit code, output). The exit code is None
    when it ran past `timeout` and was killed. The child and both pty ends are always cleaned up."""
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env={**env, 'TERM': 'xterm-256color'},
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,  # its own process group, killed as one
        )
    except OSError:
        os.close(master)
        os.close(slave)
        raise
    os.close(slave)
    buf = bytearray()
    timed_out = True
    try:
        end = time.monotonic() + timeout
        while (left := end - time.monotonic()) > 0:
            ready, _, _ = select.select([master], [], [], min(left, 0.2))
            if not ready:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:  # Linux: EIO once the child has closed its end
                chunk = b''
            if not chunk:
                timed_out = False
                break
            if len(buf) < MAX_READ:
                buf += chunk[: MAX_READ - len(buf)]
        if not timed_out:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=max(0.0, min(5.0, end - time.monotonic())))
    finally:
        if proc.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, 9)
            proc.wait()
            timed_out = True
        os.close(master)
    return (None if timed_out else proc.returncode), buf.decode('utf-8', 'replace')


def run_piped(
    argv: list[str], env: dict[str, str], cwd: Path, timeout: float
) -> tuple[int | None, str]:
    """Run `argv` without a terminal; return (exit code, stdout + stderr). None: timed out."""
    try:
        done = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as e:
        out = e.stdout or b''
        return None, out.decode('utf-8', 'replace') if isinstance(out, bytes) else out
    return done.returncode, done.stdout + done.stderr


def parse_body(raw: bytes, key: str) -> str:
    """A JSON object holding exactly one non-empty string `key`. 400 otherwise."""
    try:
        body = json.loads(raw or b'null')
    except (ValueError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict) or set(body) != {key} or not isinstance(body[key], str):
        raise SessionError(400, 'bad_request', f'Send a JSON object with only "{key}" in it.')
    if not body[key].strip():
        raise SessionError(400, 'bad_request', f'"{key}" is empty.')
    return body[key]


class Cloud:
    """Creates cloud sessions and sends them follow-ups. Keeps no state: a cloud session's state
    comes from the worker's own reports to the hub (SP3)."""

    def __init__(
        self,
        claude: Path,
        home: Path,
        *,
        cwd: Path | None = None,
        spawn_timeout: float = SPAWN_TIMEOUT,
        send_timeout: float = SEND_TIMEOUT,
        on_pty: Runner = run_on_pty,
        piped: Runner = run_piped,
    ) -> None:
        self.claude = claude
        self.home = home
        self.cwd = cwd or home
        self.spawn_timeout = spawn_timeout
        self.send_timeout = send_timeout
        self.on_pty = on_pty
        self.piped = piped

    def _env(self) -> dict[str, str]:
        return scrubbed_env(os.environ, self.home)

    def spawn(self, raw: bytes) -> dict[str, Any]:
        prompt = parse_body(raw, 'prompt')
        argv = [str(self.claude), '--cloud', '--', prompt]
        try:
            code, out = self.on_pty(argv, self._env(), self.cwd, self.spawn_timeout)
        except OSError as e:
            raise SessionError(
                422, 'cloud_unparsed', f'The runner could not run {self.claude}: {e.strerror or e}.'
            ) from None
        said = clean(out, secrets_in(prompt))
        if code is None:
            raise SessionError(
                422,
                'cloud_unparsed',
                f'Claude did not create the cloud session within {self.spawn_timeout:g} s. '
                'It may still appear at claude.ai/code: look there before trying again.'
                + (f' Claude said: {said}' if said else ''),
                said or None,
            )
        found = CREATED.search(ESCAPES.sub('', out))
        ids = FOUND_ID.findall(found.group('url')) if found else []
        session_id = ids[0] if ids else None
        url = page_url(found.group('url'), session_id) if found and session_id else None
        if found is None or session_id is None or url is None:
            raise SessionError(
                422,
                'cloud_unparsed',
                'Claude did not print a cloud session id and a claude.ai link. '
                f'Claude said: {said or "nothing"}',
                said or None,
            )
        title = clean(found.group('title'))
        return {'session_id': session_id, 'url': url, 'title': title}

    def send(self, session_id: str, raw: bytes) -> dict[str, Any]:
        check_id(session_id)
        text = parse_body(raw, 'text')
        argv = [
            str(self.claude),
            '-p',
            '--cloud',
            session_id,
            '--output-format',
            'json',
            '--',
            text,
        ]
        try:
            code, out = self.piped(argv, self._env(), self.cwd, self.send_timeout)
        except OSError as e:
            raise SessionError(
                422,
                'cloud_send_failed',
                f'The runner could not run {self.claude}: {e.strerror or e}.',
            ) from None
        said = clean(out, secrets_in(text))
        if code is None:
            raise SessionError(
                422,
                'cloud_send_failed',
                f'Claude did not pass the message on within {self.send_timeout:g} s. '
                'It may still arrive; check the session at claude.ai/code before sending it again.',
                said or None,
            )
        ack = _last_json(out)
        url = page_url(ack.get('url'), session_id) if ack else None
        if code != 0 or ack is None or ack.get('ok') is not True or url is None:
            refused = ack is not None and ack.get('ok') is False
            raise SessionError(
                422,
                'cloud_send_failed',
                (
                    'claude.ai refused the message.'
                    if refused
                    else 'Claude could not send the message.'
                )
                + f' Claude said: {said or "nothing"}',
                said or None,
            )
        if ack.get('session_id') != session_id:
            raise SessionError(
                422,
                'cloud_send_failed',
                f'Claude acknowledged a different session than {session_id}. Claude said: {said}',
                said,
            )
        return {'ok': True, 'session_id': session_id, 'url': url}

    def route(self, method: str, path: str, body: bytes) -> tuple[int, Any]:
        """Answer one request under `/cloud/sessions`."""
        parts = path.strip('/').split('/')
        try:
            if parts == ['cloud', 'sessions']:
                if method != 'POST':
                    return 405, _only_post(path)
                return 201, self.spawn(body)
            if len(parts) == 4 and parts[:2] == ['cloud', 'sessions'] and parts[3] == 'messages':
                if method != 'POST':
                    return 405, _only_post(path)
                return 202, self.send(parts[2], body)
        except SessionError as e:
            return e.status, e.body()
        return 404, {'code': 'not_found', 'message': f'The runner has no {path}.'}


def _only_post(path: str) -> dict[str, str]:
    return {'code': 'method_not_allowed', 'message': f'{path} only answers POST.'}


def _last_json(out: str) -> dict[str, Any] | None:
    """The last line of `out` that is a JSON object (claude may print warnings before it)."""
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith('{'):
            with contextlib.suppress(ValueError):
                obj = json.loads(line)
                if isinstance(obj, dict):
                    return obj
    return None
