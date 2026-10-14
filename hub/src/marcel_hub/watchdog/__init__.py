"""Tell the owner what the host's scripts did (S-11.2).

A host script that the hub cannot run itself writes its outcome as one JSON object to a file in the
state folder (`~/marcel/state`, `/state` in the container), whole (temp file, then rename). The hub
looks at start and every 15 s, posts one system message in the owner's main conversation and, when
the outcome needs the owner, one push; then it renames the file to `<name>.told`, so each outcome is
told once. `RESULTS` lists the files and how each is told.

`redeploy.result`: `deploy/redeploy.sh` (run by `marcel-redeploy.path` when the hub writes the
redeploy flag, S-11.1) deploys an approved self-change, watches Marcel's health for 120 s and
reverts it when it does not come up. A redeploy restarts the hub, so its first look tells the
outcome.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from marcel_hub.auth.tokens import OWNER_USER_ID
from marcel_hub.conversations import ensure_main_conversation, post_message
from marcel_hub.models import Agent, MessageKind, MessageRole
from marcel_hub.notify.message import BODY_MAX, PushNotification, clip, message_link
from marcel_hub.ws import EventLog

log = logging.getLogger(__name__)

TICK_S = 15.0
RESULT = 'redeploy.result'
OUTCOMES = ('deployed', 'version_drift', 'rolled_back', 'rollback_failed', 'refused')

# How a result is pushed: not at all (the message in the app is enough), as a note, or as urgent
# (Marcel may be down: the same class as a runner that is down).
Push = Literal['silent', 'note', 'urgent']
# (title, text, push) for a result file's object, written for a phone; None when the file is not
# valid JSON.
Describe = Callable[[dict[str, Any] | None], tuple[str, str, Push]]


def describe(result: dict[str, Any] | None) -> tuple[str, str, Push]:
    """How a redeploy went (`deploy/redeploy.sh`)."""
    if not isinstance(result, dict) or result.get('outcome') not in OUTCOMES:
        return (
            'An update may not have gone in',
            'A redeploy ended, but its result could not be read.',
            'note',
        )
    outcome = result.get('outcome')
    sha = str(result.get('sha') or '')[:7]
    subject = str(result.get('subject') or '').strip()
    change = f'*{subject}* ({sha})' if subject and sha else (sha or 'the change')
    reason = str(result.get('reason') or '').strip()
    if outcome == 'deployed':
        return 'Marcel updated', f'Marcel is updated with {change} and everything is up.', 'silent'
    if outcome == 'version_drift':
        return 'Marcel updated, one step left', f'Marcel is updated with {change}. {reason}', 'note'
    if outcome == 'rolled_back':
        revert = str(result.get('revert') or '')[:7]
        return (
            'An update was rolled back',
            f'{change} did not come up healthy, so Marcel reverted it'
            f'{f" ({revert})" if revert else ""}. {reason}',
            'note',
        )
    if outcome == 'rollback_failed':
        return 'Marcel may be down', f'{change}: {reason}', 'urgent'
    return 'An update was not deployed', reason or f'{change} was not deployed.', 'note'


BACKUP_RESULT = 'backup.result'
BACKUP_LOOK = 'Look at: journalctl --user -u marcel-backup on the NUC.'


def describe_backup(result: dict[str, Any] | None) -> tuple[str, str, Push]:
    """A nightly backup that failed (`deploy/backup.sh` writes the file only then, S-14.2): a note
    push, so the owner hears it on the phone. At most once a day, with the nightly timer."""
    reason = str(result.get('reason') or '').strip() if isinstance(result, dict) else ''
    if not reason:
        reason = 'a backup ended, but its result could not be read.'
    return 'The backup failed', f'The nightly backup failed: {reason} {BACKUP_LOOK}', 'note'


# Every file the watch tells, by its name in the state folder.
RESULTS: dict[str, Describe] = {RESULT: describe, BACKUP_RESULT: describe_backup}


class ResultWatch:
    """Reads the result files in the state folder and tells the owner, once per outcome."""

    def __init__(
        self,
        engine: Engine,
        events: EventLog,
        state_dir: Path,
        *,
        pusher: Any = None,
        tick: float = TICK_S,
    ) -> None:
        self.engine = engine
        self.state_dir = state_dir
        self.results = dict(RESULTS)
        self.events = events
        self.pusher = pusher
        self.tick = tick
        self._task: asyncio.Task[None] | None = None

    def check(self) -> bool:
        """Tell the owner about every new result, oldest first. True when one was told."""
        told = False
        for name, describe_it in self.results.items():
            # A script never replaces a result the hub has not told: a second one in the same run
            # goes to `<stem>.<time>.result`, told after the first, in time order.
            stem = name.removesuffix('.result')
            later = sorted(self.state_dir.glob(f'{stem}.*.result'))
            for path in [self.state_dir / name, *later]:
                told = self._check(path, describe_it) or told
        return told

    def _check(self, path: Path, describe_it: Describe) -> bool:
        name = path.name
        try:
            raw = path.read_text('utf-8')
        except FileNotFoundError:
            return False
        except OSError as exc:
            log.warning('Cannot read %s: %s', path, exc)
            return False
        try:
            result = json.loads(raw)
        except ValueError:
            log.warning('%s is not JSON; telling the owner it could not be read.', path)
            result = None
        title, text, push = describe_it(result if isinstance(result, dict) else None)
        # Raises when it cannot post: the file stays and the next look tries again.
        self._say(title, text, push, key=name.removesuffix('.result'))
        with contextlib.suppress(OSError):
            path.replace(path.with_name(f'{name}.told'))
        log.info('Told %s: %s', name, title)
        return True

    def _say(self, title: str, text: str, push: Push, key: str) -> None:
        """One system message in the owner's first agent's main conversation, and maybe one push."""
        with Session(self.engine) as s:
            agent = s.exec(
                select(Agent)
                .where(Agent.owner_user_id == OWNER_USER_ID, col(Agent.archived_at).is_(None))
                .order_by(col(Agent.id))
            ).first()
            if agent is None:
                log.warning('A result came, but the owner has no agent to tell: %s', text)
                return
            message = post_message(
                s, self.events, ensure_main_conversation(s, agent), role=MessageRole.system,
                kind=MessageKind.text, body={'text': text},
            )  # fmt: skip
            message_id = message.id
        if push != 'silent' and self.pusher is not None:
            try:
                self.pusher.push(
                    PushNotification(
                        title=title, body=clip(text, BODY_MAX),
                        category='broke' if push == 'urgent' else 'note',
                        level='urgent' if push == 'urgent' else 'normal',
                        deep_link=message_link(message_id), collapse_key=key,
                    ),
                    OWNER_USER_ID,
                )  # fmt: skip
            except Exception:
                log.exception('Could not push "%s".', title)

    async def _run(self) -> None:
        while True:
            try:
                self.check()
            except Exception:
                log.exception('The result watch failed; it looks again in %.0f s.', self.tick)
            await asyncio.sleep(self.tick)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(), name='result-watch')

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
