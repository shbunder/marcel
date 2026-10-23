"""When Claude Code changes under Marcel (found live on the NUC).

Another `claude` process on the NUC can update the shared binary. Claude's supervisor then restarts
onto the new version and the sessions it ran end, and the runner's pin (S-03.10) refuses every new
start (422 `claude_version`) until the pinned version is installed again.

The runner says both on its event stream (runner-api 0.1.12, S-03.12); `RunnerWatch` hands them
here (S-04.17):

- **`runner.claude_changed`, `to` is not `expected`.** Marcel cannot start anything until the pin
  is back, so one notice in the owner's main conversation, pushed as urgent (category `broke`,
  cause `claude_updated`), once per version: "Claude Code was updated to X. New work waits until
  Marcel is back on the pinned version Y — run deploy/install-claude.sh."
- **`runner.claude_changed`, `to` is `expected`.** One short notice, not pushed: new work starts
  again.
- **`runner.supervisor_restarted`.** The tasks of exactly the sessions in `session_ids` go to
  `failed`, with why, and one notice worded from `cause` (`upgrade`: "Claude Code updated itself
  and restarted; 2 quests stopped."), pushed as a note. No notice when `session_ids` is empty.

The hub reads `GET /health` at start-up, for a version change made while it was down, and then
every `LOOK_S` seconds for the login only (runner-api 0.1.13 `Health.login`, S-04.18):

- **`lost`:** the runner's `message`, as it is, in the owner's main conversation, pushed as an
  urgent note, once per `login.since`.
- **`ok` after a told loss:** "Claude Code on the NUC is logged in again; new work can start.", not
  pushed. A missing `login` (an older runner) or `unknown` says nothing.

On the same look it reads `GET /usage`: a stale reading with a `reason` not told yet gives one
notice, not pushed ("Marcel cannot read plan usage right now: …"), unless that reason is the told
login loss's message. A good reading in between lets the same reason be told again.

What it saw is kept in `<data_dir>/claude-version.json`, so a hub restart never tells the same
change or the same login loss twice.
`GET /internal/health` shows the open change (`path_health.py`, `claude_update`).
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from marcel_hub.conversations import ensure_main_conversation, post_message
from marcel_hub.models import (
    Agent,
    MessageKind,
    MessageRole,
    Task,
    TaskLocation,
    TaskState,
)
from marcel_hub.runner_client import RunnerUnavailable
from marcel_hub.tasks.states import IllegalTransition

log = logging.getLogger(__name__)

STATE_FILE = 'claude-version.json'
LOOK_S = 60.0  # how often the login and the usage reading are looked at
OWNER = 'usr_owner'
CAUSE = 'claude_updated'  # the urgent push's cause: one per hour, replaced by the next
RUNNING = (TaskState.starting, TaskState.working, TaskState.needs_you, TaskState.silent)
TITLE = 'Claude Code was updated'
NOTICE = (
    'Claude Code was updated to {installed}. New work waits until Marcel is back on the pinned '
    'version {expected} — run deploy/install-claude.sh.'
)
BACK = (
    'Claude Code is back on {expected}, the version Marcel is tested with. New work starts again.'
)
LOGGED_IN = 'Claude Code on the NUC is logged in again; new work can start.'
USAGE_STALE = 'Marcel cannot read plan usage right now: {reason}'
RESTARTED = {
    'upgrade': 'Claude Code updated itself and restarted; {stopped}.',
}
RESTARTED_CAUSE = "Claude's supervisor restarted ({cause}); {stopped}."
RESTARTED_PLAIN = "Claude's supervisor restarted; {stopped}."
TASK_REASON = {
    'upgrade': 'Claude Code updated itself and restarted, which ended this session. Retry it.',
}
TASK_REASON_PLAIN = "Claude's supervisor restarted, which ended this session. Retry it."


def version_of(text: str) -> str:
    """`2.1.301 (Claude Code)` → `2.1.301`; '' when unknown."""
    words = str(text or '').split()
    return words[0] if words else ''


def stopped_words(n: int) -> str:
    if n == 0:
        return 'no quest stopped'
    return '1 quest stopped' if n == 1 else f'{n} quests stopped'


class ClaudeUpdates:
    def __init__(
        self,
        engine: Any,
        runner: Any,
        registry: Any,
        events: Any,
        pusher: Any,
        data_dir: Path,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.engine, self.runner, self.registry = engine, runner, registry
        self.events, self.pusher, self.clock = events, pusher, clock
        self.path = data_dir / STATE_FILE
        self.state: dict[str, Any] = self._load()
        self._task: asyncio.Task[None] | None = None

    # What it saw ---------------------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self) -> None:
        temp = self.path.with_name(f'.{self.path.name}.tmp')
        try:
            temp.write_text(json.dumps(self.state))
            temp.replace(self.path)
        except OSError:
            log.exception('Could not keep what the Claude Code check saw in %s.', self.path)

    def open_change(self) -> dict[str, Any] | None:
        """For `/internal/health`: the change not yet undone, or None."""
        change = self.state.get('change')
        return dict(change) if isinstance(change, dict) else None

    # The runner's events -------------------------------------------------------------------------

    async def on_runner_event(self, event: dict[str, Any]) -> None:
        """A checked `runner.claude_changed` or `runner.supervisor_restarted` (`RunnerWatch`)."""
        if event['type'] == 'runner.claude_changed':
            change = event['claude_change']
            self._saw(version_of(change['to']), version_of(change['expected']))
        elif event['type'] == 'runner.supervisor_restarted':
            await self._restarted(event['supervisor_restart'])

    async def check(self) -> dict[str, Any] | None:
        """At start-up: a version change made while the hub was down, and the first look. The
        runner's `/health`, or None when it could not be read."""
        health = await self.look()
        if health is not None:
            self._saw(
                version_of(health.get('claude_version', '')),
                version_of(health.get('claude_version_expected', '')),
            )
        return health

    async def look(self) -> dict[str, Any] | None:
        """Every `LOOK_S`: the login, then why usage is stale. The runner's `/health`, or None
        when it could not be read (skipped quietly; the next look tries again)."""
        try:
            health = await self.runner.health()
        except RunnerUnavailable:
            health = None
        if isinstance(health, dict):
            self._login(health.get('login'))  # before anything that needs a version
        else:
            health = None
        await self._usage()
        return health

    # The login and usage -------------------------------------------------------------------------

    def _login(self, login: Any) -> None:
        state = login.get('state') if isinstance(login, dict) else None
        told = self.state.get('login_lost')
        if state == 'lost':
            since, message = login.get('since'), login.get('message')
            if not isinstance(since, str) or not isinstance(message, str) or since == told:
                return  # told once per `since`, also across a hub restart
            log.warning('%s', message)
            self.state['login_lost'], self.state['login_message'] = since, message
            self._save()
            self._tell(message, push=True, level='urgent')
        elif state == 'ok' and told is not None:
            self.state.pop('login_lost', None)
            self.state.pop('login_message', None)
            self._save()
            log.info('%s', LOGGED_IN)
            self._tell(LOGGED_IN)
        # no `login` (a runner older than 0.1.13) or `unknown`: nothing to say

    async def _usage(self) -> None:
        try:
            reading = await self.runner.usage()
        except Exception:  # skipped; the next look tries again
            log.debug('Could not read the runner usage for its reason.', exc_info=True)
            return
        if not isinstance(reading, dict):
            return
        if not reading.get('stale'):
            if self.state.pop('usage_told', None) is not None:  # a good reading: told again later
                self._save()
            return
        reason = reading.get('reason')
        if not isinstance(reason, str) or not reason or reason == self.state.get('usage_told'):
            return
        self.state['usage_told'] = reason
        self._save()
        if reason == self.state.get('login_message'):
            return  # the login loss already said it, in the same words
        log.warning('Marcel cannot read plan usage: %s', reason)
        self._tell(USAGE_STALE.format(reason=reason))

    def _saw(self, installed: str, expected: str) -> None:
        if not installed or not expected:
            return
        change = self.open_change()
        if installed != expected and (change is None or change.get('installed') != installed):
            self._away(installed, expected)
        elif installed == expected and change is not None:
            self._back(expected)
        if self.state.get('seen') != installed:
            self.state['seen'] = installed
            self._save()

    def _away(self, installed: str, expected: str) -> None:
        text = NOTICE.format(installed=installed, expected=expected)
        log.warning('%s', text)
        self.state['change'] = {
            'installed': installed,
            'expected': expected,
            'at': self.clock().isoformat(),
            'sessions_ended': 0,
        }
        self._save()
        self._tell(text)
        if self.pusher is not None:  # Marcel cannot answer at all until the pin is back
            self.pusher.broke(CAUSE, TITLE, text)

    def _back(self, expected: str) -> None:
        self.state.pop('change', None)
        self._save()
        log.info('Claude Code is back on the pinned %s.', expected)
        self._tell(BACK.format(expected=expected))
        if self.pusher is not None:  # quiet (no all-clear); the next drift pushes again
            self.pusher.resolved(CAUSE)

    async def _restarted(self, restart: dict[str, Any]) -> None:
        session_ids = list(restart['session_ids'])
        cause = restart.get('cause')
        if not session_ids:
            log.info("Claude's supervisor restarted (%s); no session ended.", cause or 'no cause')
            return
        stopped = await self._mark_ended(
            session_ids, TASK_REASON.get(cause or '', TASK_REASON_PLAIN)
        )
        words = stopped_words(len(stopped))
        if cause in RESTARTED:
            text = RESTARTED[cause].format(stopped=words)
        elif cause:
            text = RESTARTED_CAUSE.format(cause=cause, stopped=words)
        else:
            text = RESTARTED_PLAIN.format(stopped=words)
        log.warning('%s', text)
        change = self.open_change()
        if change is not None:
            change['sessions_ended'] = int(change.get('sessions_ended', 0)) + len(stopped)
            self.state['change'] = change
            self._save()
        self._tell(text, push=bool(stopped))  # a brain's session alone is not worth a push

    async def _mark_ended(self, session_ids: list[str], reason: str) -> set[str]:
        """The running NUC tasks of exactly these sessions: failed, with why."""
        with Session(self.engine) as s:
            ended = s.exec(
                select(Task.id)
                .where(Task.location == TaskLocation.nuc)
                .where(col(Task.state).in_(list(RUNNING)))
                .where(col(Task.session_id).in_(session_ids))
            ).all()
        marked = set()
        for task_id in ended:
            with contextlib.suppress(IllegalTransition):  # moved meanwhile; the registry logged it
                await self.registry.report_state(task_id, TaskState.failed, reason)
                marked.add(task_id)
        return marked

    def _tell(self, text: str, push: bool = False, level: str = 'normal') -> None:
        """One notice in the owner's main conversation (their oldest live agent), maybe pushed
        as a note at `level`."""
        with Session(self.engine) as s:
            agent = s.exec(
                select(Agent)
                .where(Agent.owner_user_id == OWNER, col(Agent.archived_at).is_(None))
                .order_by(col(Agent.id))
            ).first()
            if agent is None or self.events is None:
                return
            message = post_message(
                s, self.events, ensure_main_conversation(s, agent), role=MessageRole.system,
                kind=MessageKind.text, body={'text': text},
            )  # fmt: skip
            message_id, agent_id, name = message.id, agent.id, agent.name
        if push and self.pusher is not None:
            try:
                self.pusher.note(agent_id, message_id, name, text, level, everyone=True)
            except Exception:  # the notice is in the chat; the push is extra
                log.exception('Could not push the Claude Code notice.')

    # The loop ------------------------------------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(), name='claude-updates')

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        version_read = False  # until one look reads it; the runner's events tell it after that
        while True:
            try:
                if version_read:
                    await self.look()
                else:
                    version_read = await self.check() is not None
            except Exception:  # the next look tries again
                log.exception('The Claude Code look failed.')
            await asyncio.sleep(LOOK_S)
