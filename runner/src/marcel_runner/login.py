"""Is Claude Code on this machine still logged in? `Health.login` (runner-api 0.1.13, S-03.13).

Found live on the NUC on 2026-10-14: after a Claude Code self-update the supervisor could not
refresh its login and had no token from 02:52 to 05:02, so no session started and `/usage`
failed, while `/health` said `ok`. Three signals tell it (project/lessons/runner-built-ins.md):

- `claude auth status --json` says `loggedIn: false` (it exits 1 then, the JSON still on stdout);
- the supervisor's `~/.claude/daemon.log` says it has no token (`LOG_AUTH`, `lost`), until a line
  says it has one again (`back`);
- `/usage` shows the pay-as-you-go cost view (`usage.py` calls `usage_says`), until a good reading.

A loss holds until there is evidence it is over, never on silence: the supervisor logs its failure
once and then nothing for hours. Back to `ok` only when every signal is clean on `CLEAN_LOOKS` looks
in a row, so a loss never flaps. A lost login never touches `Health.ok`: a redeploy check reads
`ok`, and rolling back a good merge cannot log anyone in.
"""

import json
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marcel_runner.claude_env import scrubbed_env

LOOK_EVERY = 30.0  # seconds between two looks; a /health in between gets the last answer
LOOK_TIMEOUT = 3.0  # seconds `claude auth status` may take (it takes about 0.6 s)
CLEAN_LOOKS = 2  # clean looks in a row that end a loss
LOG_TAIL = 64 * 1024  # bytes of daemon.log a first look (or a rotated log) reads from its end

LOST_MESSAGE = (
    'Claude Code on the NUC is logged out. Log in again: on the NUC run `claude auth login`.'
)
UNKNOWN_MESSAGE = 'The runner could not check whether Claude Code is logged in.'

# The supervisor's auth lines in ~/.claude/daemon.log, recorded on 2026-10-14 (Claude Code 2.1.299
# and 2.1.296; tests/fixtures/login/). The last one written decides.
#   lost: "proactive refresh failed, signalling re-auth required", "headless daemon cannot complete
#         OAuth — run `claude auth login` to refresh", "no token found, will re-check keychain …"
#   back: "token found via keychain re-check", "scheduling proactive refresh in 28552s"
# "proactive refresh failed, retrying in ~60s (token still valid)" is neither: the token works.
LOG_AUTH = re.compile(
    r'\[supervisor\] auth: (?:'
    r'(?P<lost>[^\n]*(?:re-auth required|cannot complete OAuth|no token found))'
    r'|(?P<back>token found|scheduling proactive refresh))'
)


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


class LoginCheck:
    """`Health.login`, looked at at most once per `every` seconds. Thread-safe: `/health` requests
    and the usage probe call it from their own threads."""

    def __init__(
        self,
        claude: Path,
        state_dir: Path,
        daemon_log: Path,
        *,
        every: float = LOOK_EVERY,
        timeout: float = LOOK_TIMEOUT,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.claude, self.daemon_log = claude, daemon_log
        self.state_file = state_dir / 'login.json'
        self.every, self.timeout, self.clock = every, timeout, clock
        self.env = scrubbed_env(os.environ if env is None else env)
        # Set by server.serve: reads usage now, off this thread. Asked for when `auth status` says
        # logged in while /usage is still on the cost view, so the stale "logged out" reason is
        # gone before `login` says `ok`.
        self.refresh_usage: Callable[[], None] | None = None
        self._lock = threading.Lock()
        self._looked: datetime | None = None
        self._answer: dict[str, Any] = {}
        self._log_at: int | None = None  # how far daemon.log was read; None: not yet
        self._log_lost = False  # the supervisor's last auth line says it has no token
        self._usage_lost = False  # the last /usage read showed the cost view
        self._clean = 0  # clean looks in a row since the last lost one
        self._state, self._since = self._load()

    # --- what the other parts of the runner call --------------------------------------------------

    def __call__(self) -> dict[str, Any]:
        """`Health.login`: a fresh look, or the last answer when it is younger than `every`."""
        with self._lock:
            now = self.clock()
            if self._looked is None or (now - self._looked).total_seconds() >= self.every:
                self._looked = now
                self._look(now)
            return dict(self._answer)

    def usage_says(self, *, lost: bool) -> None:
        """From the usage probe: the cost view (`lost`) or a good reading, read on the next look."""
        with self._lock:
            self._usage_lost = lost

    # --- one look ---------------------------------------------------------------------------------

    def _look(self, now: datetime) -> None:
        auth = self._auth_status()
        self._read_log()
        if auth is False or self._log_lost or self._usage_lost:
            state, self._clean = 'lost', 0
        elif self._state == 'lost':  # evidence it is over, on CLEAN_LOOKS looks in a row
            self._clean = self._clean + 1 if auth else 0
            state = 'ok' if self._clean >= CLEAN_LOOKS else 'lost'
        else:
            state = 'ok' if auth else 'unknown'
        if auth and self._usage_lost and not self._log_lost and self.refresh_usage is not None:
            self.refresh_usage()
        if state != self._state or self._since is None:
            self._state, self._since = state, _iso(now)
            self._save()
        self._answer = {'state': state, 'since': self._since}
        if state == 'lost':
            self._answer['message'] = LOST_MESSAGE
        elif state == 'unknown':
            self._answer['message'] = UNKNOWN_MESSAGE

    def _auth_status(self) -> bool | None:
        """`loggedIn` from `claude auth status --json`; None when it cannot be told. Only that field
        is read: the email, org and ids it also prints never leave this function."""
        try:
            p = subprocess.run(
                [str(self.claude), 'auth', 'status', '--json'],
                capture_output=True,
                text=True,
                errors='replace',  # bytes that are not UTF-8: not that JSON, so unknown
                timeout=self.timeout,
                stdin=subprocess.DEVNULL,
                env=self.env,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        try:  # logged out it exits 1 and still prints the JSON: read stdout whatever the exit code
            logged_in = json.loads(p.stdout).get('loggedIn')
        except (ValueError, AttributeError):
            return None
        return logged_in if isinstance(logged_in, bool) else None

    def _read_log(self) -> None:
        """The supervisor's auth lines written since the last look (the first look, and a rotated
        log, read the tail). Only whole lines: a line mid-write is read on the next look."""
        try:
            with self.daemon_log.open('rb') as f:
                end = f.seek(0, os.SEEK_END)
                start = self._log_at if self._log_at is not None and self._log_at <= end else 0
                start = max(start, end - LOG_TAIL)
                f.seek(start)
                data = f.read(end - start)
        except OSError:
            return  # no log: auth status and /usage still tell
        whole = data.rfind(b'\n') + 1
        self._log_at = start + whole
        found = list(LOG_AUTH.finditer(data[:whole].decode('utf-8', errors='replace')))
        if found:
            last = found[-1]
            self._log_lost = last.group('lost') is not None

    # --- since, across a runner restart -----------------------------------------------------------

    def _load(self) -> tuple[str | None, str | None]:
        """The state and `since` the last runner saw, so a restart during a loss keeps its `since`
        and the hub does not tell the owner twice. Unreadable: start afresh."""
        try:
            stored = json.loads(self.state_file.read_text('utf-8'))
        except (OSError, ValueError):
            return None, None
        if (
            isinstance(stored, dict)
            and stored.get('state') in ('ok', 'lost', 'unknown')
            and isinstance(stored.get('since'), str)
        ):
            return stored['state'], stored['since']
        return None, None

    def _save(self) -> None:
        """Never stops the check: a state folder it cannot write only loses `since` on a restart."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix('.tmp')
            tmp.write_text(json.dumps({'state': self._state, 'since': self._since}), 'utf-8')
            tmp.replace(self.state_file)
        except OSError as e:
            print(
                f'marcel-runner: could not save {self.state_file}: {e.strerror or e}. '
                'A runner restart will tell a login loss again.',
                file=sys.stderr,
                flush=True,
            )
