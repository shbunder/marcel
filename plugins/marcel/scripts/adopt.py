#!/usr/bin/env python3
"""`/marcel-adopt`: hand the Claude Code session this runs in to Marcel (B-14).

Posts the session's id, folder and machine (or, in the cloud, its `session_…` id) to the hub's
`POST /api/session/adopt` (contracts/session-api.yaml) with the owner's adopt token. Marcel then
tracks the session as one of its own threads.

It runs inside any Claude Code session on any of the owner's computers, so it uses only the
Python standard library. It never prints or logs the token, talks only HTTPS to the configured
hub, follows no redirects, and gives every failure as one plain sentence that says what to do.

Settings, all optional:

- `MARCEL_HUB_URL`: the hub's API (default `https://marcel-bot.com/api`). Must be https.
- `MARCEL_ADOPT_TOKEN_FILE`: where the token is (default `~/.marcel/adopt-token`). The file must
  be readable by its owner only.
- `MARCEL_ADOPT_TOKEN`: the token itself, for a cloud session, where there is no token file
  (set it as a secret of the cloud environment). The file wins when both exist.

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

DEFAULT_HUB = 'https://marcel-bot.com/api'
DEFAULT_TOKEN_FILE = '~/.marcel/adopt-token'
TIMEOUT = 15.0  # seconds
MAX_TITLE = 120

LOCAL_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
CLOUD_ID = re.compile(r'^session_[A-Za-z0-9]{8,64}$')
HOSTNAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$')

GET_A_TOKEN = (
    'On the NUC, run `docker compose exec hub marcel-hub adopt-token` to make one, then save it '
    'on this computer: `mkdir -p ~/.marcel && (umask 077 && cat > ~/.marcel/adopt-token)`, '
    'paste the token, press Enter and then Ctrl-D. Then run /marcel-adopt again.'
)

Poster = Callable[[str, str, dict[str, Any], float], tuple[int, dict[str, Any]]]


class AdoptError(Exception):
    """Something stopped the hand-over. The message says what to do, in plain words."""


# What this session is -------------------------------------------------------------------------


def is_cloud(env: Mapping[str, str]) -> bool:
    """Claude Code on the web sets `CLAUDE_CODE_REMOTE`."""
    return env.get('CLAUDE_CODE_REMOTE', '').strip().lower() in ('1', 'true', 'yes')


def clean_title(title: str, cwd: str) -> str:
    title = ' '.join(title.split())
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


def read_token(env: Mapping[str, str]) -> str:
    """The adopt token, from the owner-only file or (cloud) the environment. Never printed."""
    path = Path(env.get('MARCEL_ADOPT_TOKEN_FILE') or DEFAULT_TOKEN_FILE).expanduser()
    try:
        info = path.stat()
    except FileNotFoundError:
        token = env.get('MARCEL_ADOPT_TOKEN', '').strip()
        if token:
            return token
        raise AdoptError(f'Marcel has no adopt token on this computer yet. {GET_A_TOKEN}') from None
    except OSError as exc:
        raise AdoptError(f'Could not read {path}: {exc.strerror}.') from None
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise AdoptError(
            f'Your adopt token in {path} can be read by other people on this computer, so '
            f'/marcel-adopt will not use it. Make it private with `chmod 600 {path}`, '
            'then run /marcel-adopt again.'
        )
    if hasattr(os, 'getuid') and info.st_uid != os.getuid():
        raise AdoptError(
            f'{path} belongs to another user, so /marcel-adopt will not use it. '
            'Save your own copy of the token there.'
        )
    try:
        token = path.read_text().strip()
    except OSError as exc:
        raise AdoptError(f'Could not read {path}: {exc.strerror}.') from None
    if not token:
        raise AdoptError(f'{path} is empty. {GET_A_TOKEN}')
    return token


# Talking to the hub ---------------------------------------------------------------------------


def hub_url(env: Mapping[str, str]) -> str:
    url = (env.get('MARCEL_HUB_URL') or DEFAULT_HUB).strip().rstrip('/')
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname:
        raise AdoptError(
            f'MARCEL_HUB_URL must be an https:// address, such as {DEFAULT_HUB}. '
            'Fix it or unset it.'
        )
    return url


class NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect could carry the token somewhere else: refuse it."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str):  # type: ignore[override]
        raise AdoptError(
            'The hub address answered with a redirect, so the token was not sent on. '
            'Check MARCEL_HUB_URL.'
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
        url = hub_url(env) + '/session/adopt'
        token = read_token(env)
        status, answer = send(url, token, body, TIMEOUT)
        print(outcome(status, answer, body['title']))
    except AdoptError as exc:
        print(f'Not adopted. {exc}')
        return 1
    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(run(sys.argv[1:]))
