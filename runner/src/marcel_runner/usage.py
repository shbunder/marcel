"""What `GET /usage` reports: the plan's 5-hour and weekly usage, read with `claude -p /usage`.

SP4 proved `claude -p "/usage" --no-session-persistence` makes no model call and prints the two
windows as text. That text is presentation, not an API: it is parsed with ONE regex, `USAGE_LINE`,
and a contract test runs it on the recorded output. When the wording changes, the probe says which
line it could not find, keeps the last values and marks them stale. It never crashes.
"""

import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from marcel_runner.claude_env import scrubbed_env
from marcel_runner.login import LOST_MESSAGE, LoginCheck

# SP4, Claude Code 2.1.289:  Current session: 4% used · resets Oct 4, 2:29pm (UTC)
# Unchanged in 2.1.296 and 2.1.299, which add a per-model weekly line and a "what's contributing"
# section.
USAGE_LINE = re.compile(
    r'^Current (session|week \(all models\)): (\d+)% used · resets (.+) \((\w+)\)$', re.MULTILINE
)
WINDOWS = {'session': 'five_hour', 'week (all models)': 'weekly'}
# Logged out, or its login expired, Claude Code is not on the subscription and /usage prints the
# pay-as-you-go cost view instead: "Total cost: $0.0000" (2.1.299, 2026-10-14 02:56 on the NUC).
COST_VIEW = re.compile(r'^Total cost:', re.MULTILINE)
RESET_FORMATS = ('%Y %b %d, %I:%M%p', '%Y %b %d, %I%p')  # "Oct 4, 2:29pm" and "Oct 9, 2am"

POLL_EVERY = 300.0  # seconds; contracts/runner-api.yaml getUsage
STALE_AFTER = timedelta(minutes=15)
HERE = 'runner/src/marcel_runner/usage.py'


class UsageUnreadable(Exception):
    """The /usage output could not be read. The message says what in plain words, and never repeats
    what claude printed (it can name the account): `detail` holds that, for the runner's log.
    `login_lost`: the cost view, so Claude Code is logged out."""

    def __init__(self, message: str, detail: str = '', login_lost: bool = False) -> None:
        super().__init__(message)
        self.detail = detail
        self.login_lost = login_lost


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def _excerpt(text: str, lines: int = 6) -> str:
    return '\n'.join(text.strip().splitlines()[:lines]) or '(no output)'


def _reset_time(when: str, tz: str, now: datetime) -> datetime:
    """'Oct 4, 2:29pm' in `tz` → an aware datetime. /usage gives no year: take the nearest one."""
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise UsageUnreadable(
            f'Claude Code gave usage reset times in "{tz}", a time zone the runner does not know.'
        ) from None
    for fmt in RESET_FORMATS:
        try:
            candidates = [
                datetime.strptime(f'{year} {when}', fmt).replace(tzinfo=zone)
                for year in (now.year - 1, now.year, now.year + 1)
            ]
        except ValueError:
            continue
        return min(candidates, key=lambda c: abs(c - now))
    raise UsageUnreadable(
        f'Claude Code gave a usage reset time the runner cannot read: "{when}". '
        f'Update RESET_FORMATS in {HERE}.'
    )


def parse_usage(text: str, now: datetime) -> dict[str, dict[str, Any]]:
    """`claude -p /usage` text → {'five_hour': {used_pct, resets_at}, 'weekly': {…}}."""
    found: dict[str, dict[str, Any]] = {}
    for m in USAGE_LINE.finditer(text):
        window = WINDOWS[m.group(1)]
        resets = _reset_time(m.group(3), m.group(4), now)
        found.setdefault(window, {'used_pct': min(int(m.group(2)), 100), 'resets_at': _iso(resets)})
    if not found and COST_VIEW.search(text):
        # The login's own words (Health.login.message), so the owner reads one sentence.
        raise UsageUnreadable(LOST_MESSAGE, _excerpt(text), login_lost=True)
    for label, window in WINDOWS.items():
        if window not in found:
            raise UsageUnreadable(
                f'Claude Code\'s /usage output changed: no "Current {label}" line was found. '
                f'Update USAGE_LINE in {HERE} and re-record the SP4 fixture.',
                _excerpt(text),
            )
    return {window: found[window] for window in WINDOWS.values()}


class UsageProbe:
    """Keeps the latest reading. `refresh()` reads claude now; `start()` does it every 5 minutes."""

    def __init__(
        self,
        claude: Path,
        timeout: float = 30.0,
        env: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        login: LoginCheck | None = None,
    ) -> None:
        self.claude = claude
        self.login = login  # told when /usage shows the cost view, and when a reading is good
        self.timeout = timeout
        # TZ=UTC: reset times then always print as a one-word zone, `(UTC)`, which USAGE_LINE reads.
        self.env = {**scrubbed_env(os.environ if env is None else env), 'TZ': 'UTC'}
        self.clock = clock
        self.last_error: str | None = None
        self.reason: str | None = None  # UsageReading.reason: the plain words, without `detail`
        self._detail = ''
        self._windows: dict[str, dict[str, Any]] | None = None
        self._taken_at: datetime | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def _read(self, now: datetime) -> dict[str, dict[str, Any]]:
        cmd = f'`{self.claude} -p /usage`'
        try:
            p = subprocess.run(
                [str(self.claude), '-p', '/usage', '--no-session-persistence'],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                stdin=subprocess.DEVNULL,
                env=self.env,
            )
        except FileNotFoundError:
            raise UsageUnreadable(f'Claude Code is not installed at {self.claude}.') from None
        except PermissionError:
            raise UsageUnreadable(f'The runner cannot run {self.claude}.') from None
        except OSError as e:
            raise UsageUnreadable(f'The runner could not run {cmd}: {e.strerror or e}.') from None
        except subprocess.TimeoutExpired:
            raise UsageUnreadable(f'{cmd} did not answer within {self.timeout:g} s.') from None
        if p.returncode != 0:
            said = (p.stderr or p.stdout).strip()
            raise UsageUnreadable(f'{cmd} failed with exit code {p.returncode}.', said)
        return parse_usage(p.stdout, now)

    def refresh(self) -> None:
        """Read usage now. On failure the last values stay, and the reading turns stale."""
        now = self.clock()
        try:
            windows = self._read(now)
        except UsageUnreadable as e:
            message = str(e) if not e.detail else f'{e} It said: {e.detail}'
            with self._lock:
                changed = message != self.last_error
                self.last_error, self.reason, self._detail = message, str(e), e.detail
            if e.login_lost and self.login is not None:
                self.login.usage_says(lost=True)
            if changed:
                print(
                    f'marcel-runner: could not read usage. {message}', file=sys.stderr, flush=True
                )
            return
        with self._lock:
            self._windows, self._taken_at, self.last_error, self._detail = windows, now, None, ''
            self.reason = None
        if self.login is not None:
            self.login.usage_says(lost=False)

    def reading(self) -> tuple[int, dict[str, Any]]:
        """(HTTP status, body): 200 with a `UsageReading`, or 503 `no_reading`."""
        with self._lock:
            if self._windows is None or self._taken_at is None:
                body: dict[str, Any] = {
                    'code': 'no_reading',
                    'message': 'The runner has no usage reading yet. Treat usage as unknown.',
                }
                if self.last_error:
                    body['detail'] = self.last_error
                return 503, body
            stale = self.last_error is not None or self.clock() - self._taken_at > STALE_AFTER
            reading: dict[str, Any] = {
                **{k: dict(v) for k, v in self._windows.items()},
                'source': 'cli_usage',
                'taken_at': _iso(self._taken_at),
                'stale': stale,
            }
            if self.reason is not None:  # only after a failed attempt, so only when stale
                reading['reason'] = self.reason
            return 200, reading

    def _loop(self, every: float) -> None:
        while not self._stop.is_set():
            self.refresh()
            self._stop.wait(every)

    def start(self, every: float = POLL_EVERY) -> threading.Thread:
        """Read now, then every `every` seconds, on a daemon thread, until `stop()`."""
        self._stop.clear()
        t = threading.Thread(target=self._loop, args=(every,), name='usage-probe', daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
