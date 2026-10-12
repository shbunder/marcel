"""The push side of the hub: turns events into pushes and gets them to every device (B-16, B-18).

- **Where events come from.** Task state changes arrive through `TaskPushes`, the task registry's
  publisher: every `state` task event is one push at most. The runner watcher raises and clears
  "something broke" alerts through `broke` / `resolved` (the `Alerts` interface). The channel
  gateway can call `approval_opened`; a scheduler calls `digest`.
- **Quiet duplicates.** Every push about a task carries the task id as its collapse key, and the
  same push for the same task is not sent twice within `COLLAPSE_SECONDS`. An urgent "broke" push
  goes out once per cause per `BROKE_SECONDS` (an hour), however often the cause repeats.
- **Never breaking the hub.** Sending happens in the background. A push that fails is retried after
  `RETRY_DELAYS` and then given up and logged; nothing is ever raised into the caller. When an
  urgent push could not be delivered, one "a notification did not arrive" push goes to every
  device, in case another one can still be reached.
- **Secrets.** Logs name the device id, never its ntfy topic or APNs token.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, or_, select

from marcel_hub.models import Agent, Approval, ApprovalState, Device, Message, Task, TaskState
from marcel_hub.notify import routing
from marcel_hub.notify.backends import Notifier, PushFailed, target_kind
from marcel_hub.notify.message import PushNotification, message_link, task_link
from marcel_hub.tasks.ports import NullPublisher, Publisher

log = logging.getLogger(__name__)

COLLAPSE_SECONDS = 600.0
BROKE_SECONDS = 3600.0
RETRY_DELAYS = (2.0, 10.0)  # three tries in all, about 12 s
REMEMBER_TASKS = 1000  # how many tasks' last push are kept for collapsing repeats

# The all-clear for a cause, when there is one to give.
FIXED = {
    'runner_unreachable': (
        'The NUC is back',
        'Marcel reaches the NUC again. Its tasks show their real state.',
    ),
}


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
        self._open: dict[str, str] = {}  # cause → deep link of the "broke" push that went out

    def capabilities(self) -> dict[str, Any]:
        """`Me.push`: which backends this hub can send through."""
        body: dict[str, Any] = {'apns': 'apns' in self.notifiers, 'ntfy': 'ntfy' in self.notifiers}
        if body['ntfy']:
            body['ntfy_server'] = self.ntfy_server
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
                self.push(n, agent.owner_user_id if agent else None)
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
            self.push(n, agent.owner_user_id if agent else None)
        except Exception:
            log.exception('Could not push approval %s. Nothing was sent.', approval_id)

    def broke(self, cause: str, title: str, body: str, task_id: str | None = None) -> None:
        """Something broke (`Alerts.broke`): one urgent push per cause per hour."""
        try:
            link = task_link(task_id) if task_id else self._stand_in_link()
            if link is None:
                log.warning(
                    'Something broke, but no push went out: there is no task or message '
                    'to link it to yet. %s. %s',
                    title,
                    body,
                )
                return
            if self.push(routing.for_broke(cause, title, body, link, task_id), None):
                self._open[cause] = link
        except Exception:
            log.exception('Could not push "%s". Nothing was sent.', title)

    def resolved(self, cause: str) -> None:
        """A cause is over (`Alerts.resolved`). Says so if its "broke" push went out."""
        try:
            link = self._open.pop(cause, None)
            if link is None or cause not in FIXED:
                return
            title, body = FIXED[cause]
            self.push(routing.for_fixed(cause, title, body, link), None)
        except Exception:
            log.exception('Could not push the all-clear for %s.', cause)

    def digest(self, message_id: str, title: str, body: str, user_id: str | None = None) -> None:
        """A scheduled digest was posted as message `message_id`."""
        try:
            self.push(routing.for_digest(message_id, title, body), user_id)
        except Exception:
            log.exception('Could not push the digest %s.', message_id)

    # Sending ----------------------------------------------------------------------------------

    def push(self, n: PushNotification, user_id: str | None) -> bool:
        """Send `n` in the background to `user_id`'s devices (every device when None).

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
        self._spawn(self.deliver(n, user_id))
        return True

    async def deliver(self, n: PushNotification, user_id: str | None) -> None:
        """Send `n` to every device that takes pushes. Logs failures; never raises."""
        try:
            devices = self._devices(user_id)
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
                sends.append(self._send(notifier, device, n))
            if not sends:
                log.info('No device takes pushes, so "%s" was not sent.', n.title)
                return
            results = await asyncio.gather(*sends)
            if not all(results) and n.level == 'urgent' and n.category != 'broke':
                self.push(routing.for_failed_push(n), None)
        except Exception:
            log.exception('Sending "%s" failed. Nothing more is tried.', n.title)

    async def _send(self, notifier: Notifier, device: Device, n: PushNotification) -> bool:
        tries = len(self.retry_delays) + 1
        for attempt in range(tries):
            try:
                await notifier.send(device, n)
            except PushFailed as exc:
                if exc.retry and attempt + 1 < tries:
                    log.info('A push to device %s failed (%s). Trying again.', device.id, exc)
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

    def _devices(self, user_id: str | None) -> list[Device]:
        query = select(Device).where(
            or_(col(Device.apns_token).is_not(None), col(Device.ntfy_topic).is_not(None))
        )
        if user_id is not None:
            query = query.where(Device.user_id == user_id)
        with Session(self.engine) as s:
            return list(s.exec(query.order_by(col(Device.id))))

    def _stand_in_link(self) -> str | None:
        """A link for a push that is about no one task: the task that changed last (the runner
        watcher has just marked the affected ones `unknown`), else the newest message.

        The contract has no link for "the system"; see project/contract-requests/S-08.1.md.
        """
        with Session(self.engine) as s:
            task_id = s.exec(
                select(Task.id).order_by(col(Task.updated_at).desc(), col(Task.id).desc())
            ).first()
            if task_id:
                return task_link(task_id)
            message_id = s.exec(
                select(Message.id).order_by(col(Message.created_at).desc(), col(Message.id).desc())
            ).first()
        return message_link(message_id) if message_id else None

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
