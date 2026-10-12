"""How the tools server finds and calls the hub.

- **Identity:** `CLAUDE_CODE_SESSION_ID`, which Claude Code gives every plugin server. The hub lets
  a call in only when that id is the brain's (or a side thread's) session.
- **Secret:** the hub secret in `$CLAUDE_PLUGIN_DATA/hub-secret`, the same file the channel server
  reads (written at deploy). Env set on the `claude --bg` command line never reaches a plugin server
  (SP1, SP7), so nothing else is read.
- **Where:** `http://127.0.0.1:7420`, the hub's port on the NUC, under `/brain` (outside `/api`,
  so the Cloudflare tunnel never forwards it). `MARCEL_HUB_URL` exists for development.

Only the standard library is used, so the server runs with the NUC's `python3` and no install.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

DEFAULT_HUB_URL = 'http://127.0.0.1:7420'
SESSION_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
TIMEOUT = 30.0


@dataclass(frozen=True)
class Config:
    hub_url: str
    session_id: str
    secret: str


class ConfigProblem(Exception):
    """The server cannot tell who it is or find the secret. The message says what is missing."""


def load_config(env: Mapping[str, str]) -> Config:
    session_id = env.get('CLAUDE_CODE_SESSION_ID', '')
    if not SESSION_ID.fullmatch(session_id):
        raise ConfigProblem(
            'CLAUDE_CODE_SESSION_ID is missing or not a session id, so Marcel cannot tell who '
            'this session is.'
        )
    data_dir = env.get('CLAUDE_PLUGIN_DATA', '')
    if not data_dir:
        raise ConfigProblem('CLAUDE_PLUGIN_DATA is not set, so the hub secret cannot be found.')
    secret_path = Path(data_dir) / 'hub-secret'
    try:
        secret = secret_path.read_text().strip()
    except OSError as exc:
        raise ConfigProblem(
            f'The hub secret is missing: {secret_path} could not be read. Deploy writes it.'
        ) from exc
    if not secret:
        raise ConfigProblem(f'The hub secret file {secret_path} is empty.')
    hub_url = (env.get('MARCEL_HUB_URL') or DEFAULT_HUB_URL).rstrip('/')
    return Config(hub_url=hub_url, session_id=session_id, secret=secret)


class HubError(Exception):
    """The hub said no, or could not be reached. The message is for the brain, in plain words."""


class Transport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None,
        body: Any,
        headers: dict[str, str],
    ) -> tuple[int, Any]:
        """Send one request; return the status and the decoded JSON body (None when empty).
        Raises `OSError` when the hub cannot be reached."""
        ...


class UrllibTransport:
    """HTTP with the standard library."""

    def __init__(self, base_url: str, timeout: float = TIMEOUT) -> None:
        self.base_url = base_url
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None,
        body: Any,
        headers: dict[str, str],
    ) -> tuple[int, Any]:
        url = self.base_url + path
        if params:
            url += '?' + urllib.parse.urlencode(params)
        data = None
        headers = dict(headers)
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:  # noqa: S310
                return res.status, _decode(res.read())
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, _decode(exc.read())


def _decode(raw: bytes) -> Any:
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return {'message': raw.decode('utf-8', errors='replace')[:300]}


class Hub:
    """The `/brain` routes, as the brain's session. Every failure is a `HubError`."""

    def __init__(self, config: Config, transport: Transport | None = None) -> None:
        self.config = config
        self.transport = transport or UrllibTransport(config.hub_url)

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        headers = {
            'X-Marcel-Secret': self.config.secret,
            'X-Marcel-Session': self.config.session_id,
            'Accept': 'application/json',
        }
        try:
            status, answer = self.transport.request(
                method, '/brain' + path, params=params, body=body, headers=headers
            )
        except OSError as exc:
            raise HubError(
                f'Marcel’s hub is not answering at {self.config.hub_url} ({exc}). Nothing was done.'
            ) from exc
        if status >= 400:
            message = answer.get('message') if isinstance(answer, dict) else None
            raise HubError(message or f'The hub answered {status} with no explanation.')
        return answer


def segment(value: str) -> str:
    """One path segment, quoted so it can never become a different route."""
    return urllib.parse.quote(value, safe='')
