#!/usr/bin/env python3
"""`/marcel-adopt`: hand the Claude Code session this runs in to Marcel (B-14).

Posts the session's id, folder and machine (or, in the cloud, its `session_…` id) to the hub's
`POST /api/session/adopt` (contracts/session-api.yaml) with the owner's adopt token. Marcel then
tracks the session as one of its own threads.

It runs inside any Claude Code session on any of the owner's computers, so it uses only the
Python standard library. It never prints or logs the token, talks only HTTPS to the configured
hub, follows no redirects, and gives every failure as one plain sentence that says what to do.

Settings live in files in `~/.marcel/`, where `~` is the account's home from the password
database, never `$HOME`. The environment cannot change where the token goes or which file it is
read from: a cloned repository's `.claude/settings.json` can set environment variables for every
command Claude runs, and must not be able to point the token at its own server.

- `~/.marcel/adopt-token`: the adopt code `marcel-hub adopt-token` printed, `mra_…@https://<hub>`:
  the token and the address of the hub that issued it, bound together (S-22.2). A bare `mra_…`
  token (from before S-22.2) still works with the address below. Must belong to you and be
  readable by you only.
- `~/.marcel/hub-url`: your Marcel's address, e.g. `https://mac.tail1234.ts.net`, written by the
  install and by `marcel connect`; used only for a bare token. Same checks; must be https. There
  is no built-in address: each install has its own.
- `MARCEL_ADOPT_TOKEN` (environment): the adopt code itself, only when there is no token file,
  for a cloud session (set it as a secret of the cloud environment). The address comes only from
  the code, never from a variable of its own: a repository that replaces the code replaces the
  token too, so the owner's token can only ever go to the hub that issued it.

Run by the command as
`python3 adopt.py --session-id "${CLAUDE_SESSION_ID}" --title '<title>'`; without
`--session-id` it reads `CLAUDE_CODE_SESSION_ID`.
"""

import argparse
import json
import os
import re
import socket
import stat
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

TOKEN_FILE = '.marcel/adopt-token'  # under the home directory
HUB_FILE = '.marcel/hub-url'
ADOPT_PATH = '/api/session/adopt'
TIMEOUT = 15.0  # seconds
MAX_TITLE = 120

LOCAL_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
CLOUD_ID = re.compile(r'^session_[A-Za-z0-9]{8,64}$')
HOSTNAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$')
# Control characters, line and paragraph separators and bidi controls: dropped from a title.
UNSAFE = re.compile(r'[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u2028-\u202e\u2066-\u2069]')

GET_A_TOKEN = (
    'On the NUC, run `docker compose exec hub marcel-hub adopt-token` to make one, then save it '
    'from a terminal on this computer: '
    '`mkdir -p ~/.marcel && (umask 077 && cat > ~/.marcel/adopt-token)`, '
    'paste the code, press Enter and then Ctrl-D. Then run /marcel-adopt again.'
)
NO_HUB = (
    'This computer does not know where your Marcel is yet. Run `marcel connect` first; it saves '
    'the address in ~/.marcel/hub-url.'
)

Poster = Callable[[str, str, dict[str, Any], float], tuple[int, dict[str, Any]]]


class AdoptError(Exception):
    """Something stopped the hand-over. The message says what to do, in plain words."""


# What this session is -------------------------------------------------------------------------


def is_cloud(env: Mapping[str, str]) -> bool:
    """Claude Code on the web sets `CLAUDE_CODE_REMOTE`."""
    return env.get('CLAUDE_CODE_REMOTE', '').strip().lower() in ('1', 'true', 'yes')


def clean_title(title: str, cwd: str) -> str:
    title = ' '.join(UNSAFE.sub(' ', title).split())
    if not title:
        title = f'Session in {Path(cwd).name or cwd}'
    return title if len(title) <= MAX_TITLE else title[: MAX_TITLE - 1].rstrip() + '…'


def request_body(
    env: Mapping[str, str], session_id: str | None, title: str, cwd: str, machine: str
) -> dict[str, Any]:
    """The contract's `AdoptRequest` for this session."""
    given = (session_id or '').strip()
    if is_cloud(env):
        sid = given if CLOUD_ID.match(given) else env.get('CLAUDE_CODE_REMOTE_SESSION_ID', '')
        if not CLOUD_ID.match(sid):
            raise AdoptError(
                'This is a cloud session, but Claude Code did not say which one, so Marcel '
                'cannot adopt it. Update Claude Code and try again.'
            )
        return {
            'session_id': sid,
            'kind': 'cloud',
            'title': clean_title(title, 'the cloud'),
            'url': f'https://claude.ai/code/{sid}',
        }
    sid = given or env.get('CLAUDE_CODE_SESSION_ID', '').strip()
    if not LOCAL_ID.match(sid):
        raise AdoptError(
            'Claude Code did not tell /marcel-adopt which session this is. Run it from inside '
            'a Claude Code session (update Claude Code with `claude update` if you are).'
        )
    body: dict[str, Any] = {
        'session_id': sid,
        'kind': 'local',
        'title': clean_title(title, cwd),
        'cwd': cwd,
    }
    if HOSTNAME.match(machine):
        body['machine'] = machine
    return body


# The token ------------------------------------------------------------------------------------


def home_dir() -> Path:
    """The account's own home, from the password database: `$HOME` can be set by a repository."""
    try:
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError):  # pragma: no cover - not a POSIX system
        return Path.home()


def read_private(path: Path, what: str) -> str | None:
    """The stripped text of an owner-only file of yours, or None when there is none."""
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AdoptError(f'Could not read {path}: {exc.strerror}.') from None
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise AdoptError(
            f'Your {what} in {path} can be read by other people on this computer, so '
            f'/marcel-adopt will not use it. Make it private with `chmod 600 {path}`, '
            'then run /marcel-adopt again.'
        )
    if hasattr(os, 'getuid') and info.st_uid != os.getuid():
        raise AdoptError(
            f'{path} belongs to another user, so /marcel-adopt will not use it. '
            'Save your own copy there.'
        )
    try:
        return path.read_text().strip()
    except OSError as exc:
        raise AdoptError(f'Could not read {path}: {exc.strerror}.') from None


def split_code(code: str) -> tuple[str, str | None]:
    """An adopt code `mra_…@https://<hub>` → (token, hub address); a bare token → (token, None)."""
    token, at, hub = code.partition('@')
    return (token.strip(), hub.strip() or None) if at else (code, None)


def read_token(env: Mapping[str, str], home: Path) -> tuple[str, str | None]:
    """The adopt token and the hub address bound to it, from the owner-only file or (cloud) the
    environment. Never printed."""
    path = home / TOKEN_FILE
    code = read_private(path, 'adopt token')
    if code is None:
        code = env.get('MARCEL_ADOPT_TOKEN', '').strip()
        if code:
            return split_code(code)
        raise AdoptError(f'Marcel has no adopt token on this computer yet. {GET_A_TOKEN}')
    if not code:
        raise AdoptError(f'{path} is empty. {GET_A_TOKEN}')
    return split_code(code)


# Talking to the hub ---------------------------------------------------------------------------


def hub_url(home: Path, bound: str | None = None) -> str:
    """The adopt address: on the hub bound to the adopt code, else the one in
    `~/.marcel/hub-url`."""
    path = home / HUB_FILE
    saved = bound or read_private(path, 'hub address')
    if not saved:
        raise AdoptError(NO_HUB)
    url = saved.rstrip('/').removesuffix('/api')
    parts = urlsplit(url)
    if (
        parts.scheme != 'https'
        or not parts.hostname
        or parts.username is not None
        or parts.query
        or parts.fragment
    ):
        where = 'Your adopt code' if bound else str(path)
        raise AdoptError(
            f'{where} must hold an https:// address, such as https://mac.tail1234.ts.net. '
            'Fix it, or make a new code with `marcel-hub adopt-token`.'
        )
    return url + ADOPT_PATH


class NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect could carry the token somewhere else: refuse it."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str):  # type: ignore[override]
        raise AdoptError(
            'The hub address answered with a redirect, so the token was not sent on. '
            'Check ~/.marcel/hub-url.'
        )


def opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(NoRedirects())


def post(url: str, token: str, body: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any]]:
    """POST `body` as JSON; returns the status and the JSON answer (empty when it has none)."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method='POST',
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'marcel-adopt',
        },
    )
    host = urlsplit(url).hostname
    try:
        with opener().open(req, timeout=timeout) as res:
            return res.status, _json(res.read())
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, _json(exc.read())
    except TimeoutError:
        raise AdoptError(
            f'Marcel at {host} did not answer within {int(timeout)} seconds. Try again in a minute.'
        ) from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError | socket.timeout):
            raise AdoptError(
                f'Marcel at {host} did not answer within {int(timeout)} seconds. '
                'Try again in a minute.'
            ) from None
        raise AdoptError(
            f'Could not reach Marcel at {host} ({reason}). Check that this computer is online, '
            'then try again.'
        ) from None


def _json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw or b'{}')
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def outcome(status: int, answer: dict[str, Any], title: str) -> str:
    """What to tell the owner about the hub's answer. Raises `AdoptError` on a failure."""
    if status in (200, 201):
        if status == 200:
            return 'Marcel already follows this session. Nothing changed.'
        if answer.get('tracked') == 'live':
            follow = 'Marcel reads along live, as with its own threads.'
        else:
            follow = (
                'It runs off the NUC, so Marcel keeps the link and its reports but cannot '
                'read along.'
            )
        return f'Marcel has taken over “{title}”. It shows in the Marcel app now. {follow}'
    if status in (401, 404):
        raise AdoptError(
            f'The hub did not accept your adopt token; it may be an old one. {GET_A_TOKEN}'
        )
    if status == 429:
        raise AdoptError(
            'The hub saw too many wrong tokens just now. Wait a minute, then try again.'
        )
    if status == 400:
        why = answer.get('message') or 'it did not say why'
        raise AdoptError(f'The hub did not accept this session: {why}')
    raise AdoptError(f'The hub had a problem (it answered {status}). Try again in a minute.')


# The command ----------------------------------------------------------------------------------


def run(
    argv: list[str],
    env: Mapping[str, str] | None = None,
    send: Poster = post,
    cwd: str | None = None,
    machine: str | None = None,
    home: Path | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog='marcel-adopt', description='Hand this Claude Code session to Marcel.'
    )
    parser.add_argument('--title', default='', help='a short name for what this session does')
    parser.add_argument('--session-id', default=None, help='default: $CLAUDE_CODE_SESSION_ID')
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    try:
        body = request_body(
            env,
            args.session_id,
            args.title,
            cwd or os.getcwd(),
            socket.gethostname() if machine is None else machine,
        )
        home = home or home_dir()
        token, bound = read_token(env, home)
        url = hub_url(home, bound)
        status, answer = send(url, token, body, TIMEOUT)
        print(outcome(status, answer, body['title']))
    except AdoptError as exc:
        print(f'Not adopted. {exc}')
        return 1
    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(run(sys.argv[1:]))
