"""When Claude Code changes under Marcel (found live on the NUC).

Another `claude` process on the NUC can update the shared binary. Claude's supervisor then restarts
onto the new version and the sessions it ran end, and the runner's pin (S-03.10) refuses every new
start (422 `claude_version`) until the pinned version is installed again. Nothing told the owner.

The runner's event stream has no event for either (contract request
`project/contract-requests/claude-auto-update.md`), so the hub reads the runner's `GET /health`
(`claude_version`, `claude_version_expected`) every `POLL_S` seconds:

- **The version moves away from the pin.** Every NUC task still running whose session the runner
  no longer lists as live goes to `failed`, reason "Claude Code was updated to X …". Tasks the
  runner itself already reported `failed` in the last `ENDED_WINDOW_S` are counted with them. Then
  one plain notice in the owner's main conversation, pushed (category `note`), once per version:
  "Claude Code was updated to X; N sessions ended. New work waits until Marcel is back on the
  pinned version Y — run deploy/install-claude.sh."
- **The version is back on the pin.** One short notice, not pushed: new work starts again.

What it saw is kept in `<data_dir>/claude-version.json`, so a hub restart never notices the same
change twice, and a change made while the hub was down is noticed on its first look.
`GET /internal/health` shows the open change (`path_health.py`, `claude_update`).
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from marcel_hub.conversations import ensure_main_conversation, post_message
from marcel_hub.models import (
    Agent,
    MessageKind,
    MessageRole,
    Task,
    TaskEvent,
    TaskEventType,
    TaskLocation,
    TaskState,
)
from marcel_hub.runner_client import RunnerUnavailable
from marcel_hub.tasks.states import IllegalTransition

log = logging.getLogger(__name__)

POLL_S = 60.0
ENDED_WINDOW_S = 600.0  # a task the runner reported failed this recently ended with the change
STATE_FILE = 'claude-version.json'
OWNER = 'usr_owner'
RUNNING = (TaskState.starting, TaskState.working, TaskState.needs_you, TaskState.silent)
LIVE_SESSION = frozenset({'working', 'blocked'})  # runner-api SessionState
NOTICE = (
    'Claude Code was updated to {installed}; {ended}. New work waits until Marcel is back on '
    'the pinned version {expected} — run deploy/install-claude.sh.'
)
BACK = (
    'Claude Code is back on {expected}, the version Marcel is tested with. New work starts again.'
)
TASK_REASON = (
    'Claude Code was updated to {installed} and its supervisor restarted, which ended this '
    'session. Retry it once Marcel is back on {expected}.'
)


def version_of(text: str) -> str:
    """`2.1.301 (Claude Code)` → `2.1.301`; '' when unknown."""
    words = str(text or '').split()
    return words[0] if words else ''


def ended_words(n: int) -> str:
    if n == 0:
        return 'no running session ended'
    return '1 session ended' if n == 1 else f'{n} sessions ended'


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

    # The check -----------------------------------------------------------------------------------

    async def check(self) -> None:
        try:
            health = await self.runner.health()
        except RunnerUnavailable:
            return  # the runner watch already says when the runner is away
        installed = version_of(health.get('claude_version', ''))
        expected = version_of(health.get('claude_version_expected', ''))
        if not installed or not expected:
            return
        seen = self.state.get('seen')
        change = self.open_change()
        if installed != expected and (change is None or change.get('installed') != installed):
            await self._away(installed, expected)
        elif installed == expected and change is not None:
            self._back(expected)
        if seen != installed:
            self.state['seen'] = installed
            self._save()

    async def _away(self, installed: str, expected: str) -> None:
        marked = await self._mark_ended(installed, expected)
        ended = marked | self._recently_failed()
        text = NOTICE.format(installed=installed, ended=ended_words(len(ended)), expected=expected)
        log.warning('%s', text)
        self.state['change'] = {
            'installed': installed,
            'expected': expected,
            'at': self.clock().isoformat(),
            'sessions_ended': len(ended),
        }
        self._save()
        self._tell(text, push=True)

    def _back(self, expected: str) -> None:
        self.state.pop('change', None)
        self._save()
        log.info('Claude Code is back on the pinned %s.', expected)
        self._tell(BACK.format(expected=expected), push=False)

    async def _mark_ended(self, installed: str, expected: str) -> set[str]:
        """Running NUC tasks whose session the runner no longer lists as live: failed, with why."""
        try:
            sessions = await self.runner.sessions()
        except RunnerUnavailable:
            return set()
        live = {s.get('id') for s in sessions if s.get('state') in LIVE_SESSION}
        with Session(self.engine) as s:
            running = s.exec(
                select(Task)
                .where(Task.location == TaskLocation.nuc)
                .where(col(Task.state).in_(list(RUNNING)))
                .where(col(Task.session_id).is_not(None))
            ).all()
            gone = [t.id for t in running if t.session_id not in live]
        reason = TASK_REASON.format(installed=installed, expected=expected)
        marked = set()
        for task_id in gone:
            with contextlib.suppress(IllegalTransition):  # moved meanwhile; the registry logged it
                await self.registry.report_state(task_id, TaskState.failed, reason)
                marked.add(task_id)
        return marked

    def _recently_failed(self) -> set[str]:
        since = self.clock() - timedelta(seconds=ENDED_WINDOW_S)
        with Session(self.engine) as s:
            rows = s.exec(
                select(TaskEvent.task_id, TaskEvent.data_json)
                .join(Task, col(Task.id) == col(TaskEvent.task_id))
                .where(Task.location == TaskLocation.nuc)
                .where(TaskEvent.type == TaskEventType.state)
                .where(TaskEvent.at >= since)
            ).all()
        return {task_id for task_id, data in rows if (data or {}).get('to') == TaskState.failed}

    def _tell(self, text: str, push: bool) -> None:
        """One notice in the owner's main conversation (their oldest live agent), maybe pushed."""
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
                self.pusher.note(agent_id, message_id, name, text, 'normal', OWNER)
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
        while True:
            try:
                await self.check()
            except Exception:  # the next look tries again
                log.exception('The Claude Code version check failed.')
            await asyncio.sleep(POLL_S)
