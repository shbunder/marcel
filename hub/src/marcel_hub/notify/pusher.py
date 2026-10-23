"""The push side of the hub: turns events into pushes and gets them to the phones (B-16, B-18).

- **Which phones.** A buddy's push (its tasks, its approvals, its notices and digests) goes to the
  phones showing that buddy (`Device.agent_id`, through `auth.routes.buddy_of`), so switching a
  phone's buddy moves that buddy's next push to it (B-49). An approval for a buddy no phone shows
  goes to every phone, so it never waits silently (S-22.15). A push about no buddy (a redeploy, a
  backup, usage, a logged-out Claude, a self-change, something broken) goes to every phone.

- **Where events come from.** Task state changes arrive through `TaskPushes`, the task registry's
  publisher: every `state` task event is one push at most. The runner watcher raises and clears
  "something broke" alerts through `broke` / `resolved` (the `Alerts` interface). The channel
  gateway can call `approval_opened`; a scheduler calls `digest`; the brain's `notify` tool calls
  `note`. A push about no one task (the runner) opens the app's status screen, `marcel://status`.
- **Quiet duplicates.** Every push about a task carries the task id as its collapse key, and the
  same push for the same task is not sent twice within `COLLAPSE_SECONDS`. An urgent "broke" push
  goes out once per cause per `BROKE_SECONDS` (an hour), however often the cause repeats. Marcel's
  own notices (`note`) go out at most `NOTE_LIMIT` per agent per `NOTE_SECONDS`; the rest are
  dropped and logged, so a looping brain cannot flood the phone (the message is still posted).
- **Never breaking the hub.** Sending happens in the background. A push that fails is retried after
  `RETRY_DELAYS` and then given up and logged; nothing is ever raised into the caller. When an
  urgent push could not be delivered, one "a notification did not arrive" push goes to every
  device, in case another one can still be reached.
- **Secrets.** Logs name the device id, never its ntfy topic, APNs token or relay target.
"""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, or_, select

from marcel_hub.auth.routes import buddy_of
from marcel_hub.auth.tokens import OWNER_USER_ID
from marcel_hub.models import Agent, Approval, ApprovalState, Device, Task, TaskState
from marcel_hub.notify import routing
from marcel_hub.notify.backends import Notifier, PushFailed, target_kind
from marcel_hub.notify.message import STATUS_LINK, PushNotification, clip, task_link
from marcel_hub.tasks.ports import NullPublisher, Publisher

log = logging.getLogger(__name__)

COLLAPSE_SECONDS = 600.0
BROKE_SECONDS = 3600.0
RETRY_DELAYS = (2.0, 10.0)  # three tries in all, about 12 s
NOTE_LIMIT = 6  # Marcel's own notices per agent per NOTE_SECONDS (app-api.yaml PushNotification)
NOTE_SECONDS = 3600.0
REMEMBER_TASKS = 1000  # how many tasks' last push are kept for collapsing repeats

# The all-clear for a cause, when there is one to give.
FIXED = {
    'runner_unreachable': (
        'The NUC is back',
        'Marcel reaches the NUC again. Its tasks show their real state.',
    ),
    'runner_warning': (
        'The NUC is fine again',
        'The problem the NUC reported is gone.',
    ),
}

# Causes whose resolve starts their hour over, so the next outage is pushed even within the hour:
# those with an all-clear, and Claude Code back on its pin (S-04.17). Not `brain_down`: it clears on
# an accepted start, before the brain says hello, so a flaky brain would push every minute or two.
RESET_ON_RESOLVE = frozenset(FIXED) | {'claude_updated'}


class Pusher:
    def __init__(
        self,
        engine: Engine,
        notifiers: Iterable[Notifier] = (),
        *,
        ntfy_server: str | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        retry_delays: tuple[float, ...] = RETRY_DELAYS,
    ) -> None:
        self.engine = engine
        self.notifiers: dict[str, Notifier] = {n.kind: n for n in notifiers}
        self.ntfy_server = ntfy_server
        self.sleep = sleep
        self.clock = clock
        self.retry_delays = retry_delays
        self._jobs: set[asyncio.Task[None]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_task_push: dict[str, tuple[tuple[str, str, str], float]] = {}
        self._broke_sent: dict[str, float] = {}
        self._notes_sent: dict[str, deque[float]] = {}  # agent id → times of its recent notes
        self._open: dict[str, str] = {}  # cause → deep link of the "broke" push that went out

    def capabilities(self) -> dict[str, Any]:
        """`Me.push`: which backends this hub can send through."""
        body: dict[str, Any] = {'apns': 'apns' in self.notifiers, 'ntfy': 'ntfy' in self.notifiers}
        if body['ntfy']:
            body['ntfy_server'] = self.ntfy_server
        body['relay'] = 'relay' in self.notifiers
        return body

    def task_publisher(self, inner: Publisher | None = None) -> 'TaskPushes':
        """A publisher for the task registry that passes every event on to `inner`, and pushes."""
        return TaskPushes(self, inner or NullPublisher())

    # Events. None of these raise: a push is never worth breaking the caller. ------------------

    def on_state(self, task_id: str, to: str, reason: str = '') -> None:
        """A task moved to `to` (a `state` task event)."""
        try:
            state = TaskState(to)
            if state not in routing.PUSHING_STATES:
                return
            with Session(self.engine) as s:
                task = s.get(Task, task_id)
                if task is None:
                    return
                agent = s.get(Agent, task.agent_id)
                approval = (
                    s.exec(
                        select(Approval)
                        .where(Approval.task_id == task_id)
                        .where(Approval.state == ApprovalState.open)
                        .order_by(col(Approval.created_at).desc())
                    ).first()
                    if state == TaskState.needs_you
                    else None
                )
            name = agent.name if agent else 'Marcel'
            if approval is not None:
                n = routing.for_approval(task, approval, name)
            else:
                n = routing.for_state(task, state, reason, name)
            if n is not None:
                self.push(n, agent.id if agent else None)
        except Exception:
            log.exception('Could not work out the push for task %s. Nothing was sent.', task_id)

    def approval_opened(self, approval_id: str) -> None:
        """An approval was opened (the channel gateway, S-04.6)."""
        try:
            with Session(self.engine) as s:
                approval = s.get(Approval, approval_id)
                task = s.get(Task, approval.task_id) if approval else None
                agent = s.get(Agent, task.agent_id) if task else None
            if approval is None or task is None or approval.state != ApprovalState.open:
                return
            n = routing.for_approval(task, approval, agent.name if agent else 'Marcel')
            from marcel_hub.channel.approvals import is_self_change  # channel/ builds on notify/

            # A change to Marcel itself goes to every phone, whichever buddy opened it.
            buddy = None if is_self_change(approval) else agent.id if agent else None
            self.push(n, buddy)
        except Exception:
            log.exception('Could not push approval %s. Nothing was sent.', approval_id)

    def broke(self, cause: str, title: str, body: str, task_id: str | None = None) -> None:
        """Something broke (`Alerts.broke`): one urgent push per cause per hour."""
        try:
            link = task_link(task_id) if task_id else STATUS_LINK
            if self.push(routing.for_broke(cause, title, body, link, task_id), None):
                self._open[cause] = link
        except Exception:
            log.exception('Could not push "%s". Nothing was sent.', title)

    def resolved(self, cause: str) -> None:
        """A cause is over (`Alerts.resolved`). Says so if its "broke" push went out and the cause
        has an all-clear; a cause in `RESET_ON_RESOLVE` is pushed again on its next outage, even
        within the hour."""
        try:
            link = self._open.pop(cause, None)
            if link is None:
                return
            if cause in RESET_ON_RESOLVE:  # over: a new outage is news, not a repeat
                self._broke_sent.pop(cause, None)
            if cause not in FIXED:
                return
            title, body = FIXED[cause]
            self.push(routing.for_fixed(cause, title, body, link), None)
        except Exception:
            log.exception('Could not push the all-clear for %s.', cause)

    def note(
        self,
        agent_id: str,
        message_id: str,
        title: str,
        body: str,
        level: str = 'normal',
        everyone: bool = False,
    ) -> bool:
        """Marcel's own notice, posted as message `message_id` (the brain's `notify` tool): to the
        phones showing `agent_id`, or with `everyone` (a notice about Marcel itself, such as a
        Claude Code update) to every phone.

        At most `NOTE_LIMIT` per agent per `NOTE_SECONDS`; False when this one was dropped.
        """
        now = self.clock()
        sent = self._notes_sent.setdefault(agent_id, deque())
        while sent and now - sent[0] >= NOTE_SECONDS:
            sent.popleft()
        if len(sent) >= NOTE_LIMIT:
            log.warning(
                'Marcel (agent %s) tried to send more than %d notices this hour. "%s" was posted '
                'in the chat but not pushed to the phone.',
                agent_id,
                NOTE_LIMIT,
                clip(body, 60),
            )
            return False
        try:
            n = routing.for_note(message_id, title, body, level)
        except Exception:
            log.exception('Could not build the push for notice %s. Nothing was sent.', message_id)
            return False
        sent.append(now)
        return self.push(n, None if everyone else agent_id)

    def digest(self, message_id: str, title: str, body: str, agent_id: str | None = None) -> None:
        """A scheduled digest of `agent_id` was posted as message `message_id`."""
        try:
            self.push(routing.for_digest(message_id, title, body), agent_id)
        except Exception:
            log.exception('Could not push the digest %s.', message_id)

    # Sending ----------------------------------------------------------------------------------

    def push(self, n: PushNotification, buddy: str | None) -> bool:
        """Send `n` in the background to the phones showing `buddy` (an agent id); when None (usage,
        a broken service, a self-change), to every phone. The one person's id (`OWNER_USER_ID`,
        as the watchdog names the owner) is every phone too: they are all that person's.

        False when it was held back as a repeat.
        """
        now = self.clock()
        if n.category == 'broke' and n.level == 'urgent':
            key = n.collapse_key or n.title
            last = self._broke_sent.get(key)
            if last is not None and now - last < BROKE_SECONDS:
                log.info('Held back a repeat "broke" push for %s (one per hour).', key)
                return False
            self._broke_sent[key] = now
        elif n.task_id:
            signature = (n.category, n.title, n.body)
            last_push = self._last_task_push.pop(n.task_id, None)
            if last_push and last_push[0] == signature and now - last_push[1] < COLLAPSE_SECONDS:
                self._last_task_push[n.task_id] = last_push
                log.info('Held back a repeat push for task %s.', n.task_id)
                return False
            self._last_task_push[n.task_id] = (signature, now)
            while len(self._last_task_push) > REMEMBER_TASKS:
                del self._last_task_push[next(iter(self._last_task_push))]
        self._spawn(self.deliver(n, buddy))
        return True

    async def deliver(self, n: PushNotification, buddy: str | None) -> None:
        """Send `n` to every phone that takes pushes and shows `buddy` (every phone when None).
        Logs failures; never raises."""
        try:
            devices = self._devices(buddy, fallback=n.category == 'approval')
            about = None if buddy == OWNER_USER_ID else buddy
            sends = []
            for device in devices:
                kind = target_kind(device)
                notifier = self.notifiers.get(kind) if kind else None
                if notifier is None:
                    log.info(
                        'Device %s takes %s pushes, and this hub has no %s backend set up.',
                        device.id,
                        kind,
                        kind,
                    )
                    continue
                sends.append(self._send(notifier, device, n, about))
            if not sends:
                log.info('No phone that takes pushes is to get "%s", so it was not sent.', n.title)
                return
            results = await asyncio.gather(*sends)
            if not all(results) and n.level == 'urgent' and n.category != 'broke':
                self.push(routing.for_failed_push(n), None)
        except Exception:
            log.exception('Sending "%s" failed. Nothing more is tried.', n.title)

    async def _send(
        self, notifier: Notifier, device: Device, n: PushNotification, buddy: str | None
    ) -> bool:
        tries = len(self.retry_delays) + 1
        for attempt in range(tries):
            try:
                await notifier.send(device, n, agent_id=buddy)
            except PushFailed as exc:
                if exc.retry and attempt + 1 < tries:
                    log.info('A push to device %s failed: %s Trying again.', device.id, exc)
                    await self.sleep(self.retry_delays[attempt])
                    continue
                log.warning(
                    'A %s push to device %s did not go out after %d tr%s: %s',
                    n.category,
                    device.id,
                    attempt + 1,
                    'y' if attempt == 0 else 'ies',
                    exc,
                )
                return False
            log.info('Sent a %s push to device %s: %s', n.category, device.id, n.title)
            return True
        return False  # pragma: no cover - the loop always returns

    def _devices(self, buddy: str | None, fallback: bool = False) -> list[Device]:
        """The phones that take pushes; with a buddy, those showing it, and any phone that shows
        no buddy at all (none is live yet), so it misses nothing. With `fallback` (an approval),
        every phone when none shows that buddy: an approval never waits silently."""
        query = select(Device).where(
            or_(
                col(Device.apns_token).is_not(None),
                col(Device.ntfy_topic).is_not(None),
                col(Device.relay_handle).is_not(None),
            )
        )
        with Session(self.engine) as s:
            devices = list(s.exec(query.order_by(col(Device.id))))
            if buddy is None or buddy == OWNER_USER_ID:
                return devices
            showing = [d for d in devices if buddy_of(s, d) in (buddy, None)]
            return devices if fallback and not showing else showing

    # Background jobs --------------------------------------------------------------------------

    def _spawn(self, job: Coroutine[Any, Any, None]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # called from a worker thread (a sync route)
            if self._loop is None or self._loop.is_closed():
                job.close()
                log.warning('A push was dropped: the hub is not running its event loop.')
                return
            asyncio.run_coroutine_threadsafe(job, self._loop)
            return
        self._loop = loop
        task = loop.create_task(job, name='push')
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    def start(self) -> None:
        """Remember the hub's event loop, so pushes raised from worker threads reach it."""
        self._loop = asyncio.get_running_loop()

    async def wait(self) -> None:
        """Wait until every push in flight has been sent or given up."""
        while self._jobs:
            await asyncio.gather(*list(self._jobs), return_exceptions=True)

    async def aclose(self) -> None:
        for job in list(self._jobs):
            job.cancel()
        await asyncio.gather(*list(self._jobs), return_exceptions=True)
        for notifier in self.notifiers.values():
            await notifier.aclose()


class TaskPushes:
    """The task registry's publisher: passes every event on, and pushes on state changes."""

    def __init__(self, pusher: Pusher, inner: Publisher) -> None:
        self.pusher = pusher
        self.inner = inner

    def publish(self, type: str, data: dict[str, Any]) -> None:
        self.inner.publish(type, data)
        if type != 'task.event' or data.get('type') != 'state':
            return
        try:
            event = data['data']
            self.pusher.on_state(data['task_id'], event['to'], event.get('reason', ''))
        except Exception:
            log.exception('A task state event had an unexpected shape. Nothing was pushed.')
