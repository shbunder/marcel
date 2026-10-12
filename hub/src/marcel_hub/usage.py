"""The usage guard (S-10.1, B-19): keeps Marcel inside the Claude Max plan.

- **Readings.** The guard asks the runner for its latest usage reading (`GET /usage`, SP4) every
  5 minutes, every minute once a window is at 70 % or more or while it holds work. Fresh readings
  are kept as `usage_snapshot` rows (one per window, when the number or the reset changes).
- **Holding.** A fresh reading at or above the limit (90 % of the five-hour window, or 95 % of
  the week) for a window that has not reset yet starts a hold, and so does a detected limit hit
  (below), whatever the reading says. While it holds, every new spawn is queued with
  `queued_reason: usage` (the registry's `hold_new_work` hook) and the queue is not drained;
  running work carries on.
- **One push per crossing.** Starting a hold pushes once, with the reset time ("Marcel is pausing
  new work until 14:00: the subscription is at 91 %."). Polls, extensions, limit hits during a
  hold and queued spawns push nothing. A hold that ends and starts again for the same reset time
  does not push again.
- **Letting go.** A hold ends at its reset time, or when a fresh reading shows every window below
  its limit (a hold from a limit hit ignores low readings: it ends at the reset time). The queue
  is then drained oldest first, through the registry, so the worker cap still applies.
- **Fail-open.** When no fresh reading has come for 30 minutes while it holds, the guard lets go
  and logs a warning. The owner is not pushed: they already know work was paused, the tasks that
  start show it resumed, and a runner that cannot be reached raises its own "broke" push.
- **Limit hits** (SP4) come through the task registry's publisher: a transcript `error` event with
  `usage_limit`, or a task moving to `needs_you` because "The usage limit is reached" (the runner
  watcher's reason for a session `blocked` on a usage limit). A task's session report counts once
  until the task moves on. The first raw row of a hit is logged, as no live hit has been recorded.
- **Per-task attribution.** The runner's transcripts carry no token counts, so each rise of the
  five-hour window between two fresh readings is split evenly over the tasks that were live then
  (the rest, with none live, goes to `other`). Kept in the state file for a later usage report.
- **Restart.** The hold, the last push, the attribution and the last fresh reading's time are kept
  in `<data_dir>/usage-guard.json`; the queue is in the database. A restart keeps holding and does
  not push again.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Literal, Protocol

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session, col, func, select

from marcel_hub.auth import require_device
from marcel_hub.brain_api import NotSetUp
from marcel_hub.models import QueuedReason, Task, TaskState, UsageSnapshot, UsageWindow
from marcel_hub.notify.message import BODY_MAX, STATUS_LINK, PushNotification, clip
from marcel_hub.runner_client import is_usage_limit
from marcel_hub.tasks.ports import NullPublisher, Publisher
from marcel_hub.tasks.registry import TaskRegistry, iso

log = logging.getLogger(__name__)

FIVE_HOUR_LIMIT = 90.0
WEEKLY_LIMIT = 95.0
SLOW_POLL = 300.0  # seconds
FAST_POLL = 60.0
FAST_FROM = 70.0  # % of a window from which the guard reads every minute
FAIL_OPEN = timedelta(minutes=30)
# After a push for a hold whose reset is unknown, later such holds stay quiet this long (or until
# a fresh reading): each of them ends after 30 minutes, so each would otherwise push again.
QUIET_UNKNOWN = timedelta(hours=5)
STALE_AFTER = timedelta(minutes=15)  # runner-api.yaml: a reading older than this is stale
STATE_FILE = 'usage-guard.json'

# The runner watcher's reason for a task whose session hit the usage limit (runner_client.py).
LIMIT_REASON = 'The usage limit is reached'

# Tasks that spend the plan between two readings.
SPENDING = frozenset({TaskState.starting, TaskState.working, TaskState.silent})

WINDOWS = ('five_hour', 'weekly')


class Reader(Protocol):
    async def usage(self) -> dict[str, Any] | None:
        """The runner's latest `UsageReading`, or None when it has none."""
        ...


class Pushes(Protocol):
    def push(self, n: PushNotification, user_id: str | None) -> bool: ...


class NoReader:
    async def usage(self) -> dict[str, Any] | None:
        return None


class NoReading(NotSetUp):
    """No usage reading yet (no runner, or the runner has not read usage since it started)."""


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _reading_ok(reading: Any) -> bool:
    if not isinstance(reading, dict) or not isinstance(reading.get('stale'), bool):
        return False
    for name in WINDOWS:
        window = reading.get(name)
        if not isinstance(window, dict) or not isinstance(window.get('used_pct'), int | float):
            return False
    return True


@dataclass
class Hold:
    cause: Literal['reading', 'limit']
    until: str  # ISO time: when the window resets
    since: str  # ISO time of the latest evidence for it (a reading over the limit, or a hit)
    reason: str  # for the app and the queued task's event, e.g. "Plan usage is at 91 %. …"
    window: str  # the window it waits for, or '' after a limit hit


class UsageGuard:
    def __init__(
        self,
        reader: Reader | None = None,
        *,
        pusher: Pushes | None = None,
        publisher: Publisher | None = None,
        state_path: Path | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        tz: tzinfo = UTC,
        five_hour_limit: float = FIVE_HOUR_LIMIT,
        weekly_limit: float = WEEKLY_LIMIT,
    ) -> None:
        self.reader = reader or NoReader()
        self.pusher = pusher
        self.publisher = publisher or NullPublisher()
        self.state_path = state_path
        self.clock = clock
        self.sleep = sleep
        self.tz = tz  # the owner's (`Settings.tz`); never the process's own time zone
        self.limits = {'five_hour': five_hour_limit, 'weekly': weekly_limit}
        self.registry: TaskRegistry | None = None
        self.reading: dict[str, Any] | None = None
        self._job: asyncio.Task[None] | None = None
        self._published: dict[str, Any] | None = None
        self._clean()
        self._load()

    def _clean(self) -> None:
        """The state kept across restarts (`save` / `_load`), as on a first start."""
        self.hold: Hold | None = None
        self.notified_until: str | None = None
        self.quiet_until: str | None = None  # no push for an unknown reset before this time
        self.last_fresh_at: str | None = None
        self.raw_logged: set[str] = set()  # kinds of limit-hit rows already logged
        self.limited: set[str] = set()  # tasks whose limit hit counted and that have not moved on
        self.by_task: dict[str, float] = {}  # percentage points of the five-hour window
        self.previous: tuple[str, float] | None = None  # five-hour (resets_at, used_pct)

    # The registry's hook ----------------------------------------------------------------------

    def holding(self) -> str | None:
        """Why new work waits now, or None. A hold whose reset time has passed holds nothing,
        even before the next poll ends it."""
        hold = self.hold
        if hold is None or self.clock() >= _until(hold):
            return None
        return hold.reason

    # Polling ----------------------------------------------------------------------------------

    def start(self) -> None:
        self._job = asyncio.create_task(self.run(), name='usage-guard')

    async def stop(self) -> None:
        if self._job is not None:
            self._job.cancel()
            await asyncio.gather(self._job, return_exceptions=True)
            self._job = None

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # the guard must keep polling, whatever one reading did
                log.exception('The usage guard could not check plan usage. It tries again.')
            await self.sleep(self.next_wait())

    def next_wait(self) -> float:
        wait = SLOW_POLL
        if self.hold is not None or any(self._pct(w) >= FAST_FROM for w in WINDOWS):
            wait = FAST_POLL
        if self.hold is not None:
            left = (_until(self.hold) - self.clock()).total_seconds()
            wait = min(wait, max(left, 1.0))
        return wait

    async def tick(self) -> None:
        """Read usage once, then hold, extend, let go or fail open. Drains the queue when the
        hold ended."""
        now = self.clock()
        reading = await self.reader.usage()
        if reading is not None and not _reading_ok(reading):
            log.warning('Ignored a usage reading that does not match the contract: %s', reading)
            reading = None
        held = self.hold is not None
        if reading is not None:
            self.reading = reading
            if not reading['stale']:
                self._fresh(reading, now)
        released = held and self.hold is None
        hold = self.hold
        if hold is not None and now >= _until(hold):
            released = self._release('the usage window reset')
        elif hold is not None and now - self._evidence_at(hold) >= FAIL_OPEN:
            log.warning(
                'The usage guard lets new work start again: it has had no fresh usage reading for '
                '%d minutes, so it cannot tell whether the plan is still near its limit (%s).',
                FAIL_OPEN.total_seconds() // 60,
                hold.reason,
            )
            released = self._release('no fresh reading for 30 minutes')
        self.save()
        self._publish()
        if released and self.registry is not None:
            await self.registry.drain()

    def _fresh(self, reading: dict[str, Any], now: datetime) -> None:
        self.last_fresh_at = iso(now)
        self.quiet_until = None  # a fresh reading: a later unknown reset is news again
        self._snapshot(reading)
        self._attribute(reading)
        over = [
            (name, reading[name]['used_pct'], when)
            for name in WINDOWS
            if reading[name]['used_pct'] >= self.limits[name]
            and (when := _time(reading[name].get('resets_at'))) is not None
            and when > now
        ]
        if over:
            name, pct, until = max(over, key=lambda o: o[2])
            what = 'Plan usage' if name == 'five_hour' else 'Weekly plan usage'
            subscription = 'the subscription' if name == 'five_hour' else 'the weekly subscription'
            self._engage(
                Hold(
                    'reading',
                    iso(until),
                    iso(now),
                    f'{what} is at {pct:.0f} %. New tasks wait until {self._clock(until)}.',
                    name,
                ),
                f'{subscription} is at {pct:.0f} %',
            )
        elif self.hold is not None and self.hold.cause == 'reading':
            self._release('plan usage is below the limit again')

    def _evidence_at(self, hold: Hold) -> datetime:
        since = _time(hold.since)
        fresh = _time(self.last_fresh_at)
        assert since is not None
        return max(since, fresh) if fresh is not None else since

    # Limit hits -------------------------------------------------------------------------------

    def limit_hit(self, task_id: str, raw: dict[str, Any] | None = None) -> None:
        """A session hit the plan's limit (SP4). Holds new work even when the reading is stale
        or low: until the reset of the window over its limit (else the soonest reset), or for
        30 minutes when none is known. `raw` is a transcript row, logged the first time."""
        if raw is not None and 'transcript' not in self.raw_logged:
            self.raw_logged.add('transcript')
            log.warning('First usage-limit transcript row seen (task %s): %s', task_id, raw)
        now = self.clock()
        # The windows that have not reset yet: one over its limit is the one that was hit
        # (the later reset when both are); otherwise the soonest reset, as a low reading cannot
        # say which, and a hit after that reset holds again.
        windows = [
            (self.reading[name]['used_pct'] >= self.limits[name], when, name)
            for name in WINDOWS
            if self.reading is not None
            and (when := _time(self.reading[name].get('resets_at'))) is not None
            and when > now
        ]
        over = [w for w in windows if w[0]]
        if windows:
            _, until, name = max(over) if over else min(windows, key=lambda w: w[1])
            reason = (
                f"The plan's usage limit is reached. New tasks wait until {self._clock(until)}."
            )
        else:
            until, name = now + FAIL_OPEN, ''
            reason = (
                "The plan's usage limit is reached. Marcel could not read when it resets; new "
                f'tasks wait until {self._clock(until)}.'
            )
        self.limited.add(task_id)
        self._engage(
            Hold('limit', iso(until), iso(now), reason, name), "the subscription's limit is reached"
        )
        self.save()
        self._publish()

    def task_publisher(self, inner: Publisher | None = None) -> 'LimitHits':
        """A publisher for the task registry that passes every event on to `inner`, and tells
        the guard about limit hits."""
        return LimitHits(self, inner or NullPublisher())

    async def on_session(self, task_id: str, session: dict[str, Any]) -> None:
        """`SessionListener`: logs the runner's raw session row the first time a session hits
        the limit (the hit itself arrives as the task's state change)."""
        if 'session' not in self.raw_logged and is_usage_limit(session):
            self.raw_logged.add('session')
            log.warning(
                'First usage-limit session seen (task %s). The raw row: %s', task_id, session
            )
            self.save()

    # Holding and letting go -------------------------------------------------------------------

    def _engage(self, new: Hold, why: str) -> None:
        old = self.hold
        if old is not None:
            later = new if _until(new) > _until(old) else old
            cause = 'limit' if 'limit' in (old.cause, new.cause) else 'reading'
            self.hold = Hold(cause, later.until, new.since, later.reason, later.window)
            return
        self.hold = new
        log.info('The usage guard holds new work: %s', new.reason)
        if self.notified_until == new.until:
            return  # this reset was already announced (a hold that ended early started again)
        now = self.clock()
        if not new.window:  # the reset is unknown: the hold ends in 30 minutes
            quiet = _time(self.quiet_until)
            if quiet is not None and now < quiet:
                log.info('No push: a hold with an unknown reset was announced less than 5 h ago.')
                return
            self.quiet_until = iso(now + QUIET_UNKNOWN)
        self.notified_until = new.until
        if self.pusher is None:
            return
        body = (
            f'Marcel is pausing new work until {self._clock(_until(new))}: {why}. Running tasks '
            'carry on; new ones wait and start by themselves after the reset.'
        )
        self.pusher.push(
            PushNotification(
                title='Marcel is pausing new work',
                body=clip(body, BODY_MAX),
                category='note',
                level='normal',
                deep_link=STATUS_LINK,
                collapse_key='usage',
            ),
            None,
        )

    def _release(self, why: str) -> bool:
        if self.hold is None:
            return False
        log.info('The usage guard lets new work start again: %s.', why)
        self.hold = None
        return True

    # What the app and the brain read ----------------------------------------------------------

    def current(self) -> dict[str, Any]:
        """The app-api `Usage`. Raises `NoReading` before the first reading."""
        reading = self.reading
        if reading is None:
            raise NoReading(
                'Marcel has no plan usage reading yet: the runner has not read it since it '
                'started, or cannot be reached.'
            )
        fresh = _time(self.last_fresh_at)
        stale = reading['stale'] or fresh is None or self.clock() - fresh > STALE_AFTER
        body: dict[str, Any] = {
            name: {k: reading[name][k] for k in ('used_pct', 'resets_at') if k in reading[name]}
            for name in WINDOWS
        }
        body['source'] = str(reading.get('source', 'unknown'))
        body['stale'] = stale
        if isinstance(reading.get('taken_at'), str):
            body['taken_at'] = reading['taken_at']
        held = self.holding()
        guard: dict[str, Any] = {'active': held is not None, 'queued': self._queued()}
        if held is not None:
            guard['reason'] = held
        body['guard'] = guard
        return body

    def _queued(self) -> int:
        if self.registry is None:
            return 0
        with Session(self.registry.engine) as s:
            return s.exec(
                select(func.count())
                .select_from(Task)
                .where(Task.state == TaskState.queued)
                .where(Task.queued_reason == QueuedReason.usage)
            ).one()

    def _publish(self) -> None:
        """`usage.updated` to the app, when what it shows changed."""
        try:
            body = self.current()
        except NoReading:
            return
        if body != self._published:
            self._published = body
            self.publisher.publish('usage.updated', body)

    # Records ----------------------------------------------------------------------------------

    def _snapshot(self, reading: dict[str, Any]) -> None:
        if self.registry is None:
            return
        with Session(self.registry.engine) as s:
            for name in WINDOWS:
                window = UsageWindow(name)
                pct = float(reading[name]['used_pct'])
                resets = _time(reading[name].get('resets_at'))
                last = s.exec(
                    select(UsageSnapshot)
                    .where(UsageSnapshot.window == window)
                    .order_by(col(UsageSnapshot.taken_at).desc())
                ).first()
                if last is not None and last.used_pct == pct and _same(last.resets_at, resets):
                    continue
                s.add(
                    UsageSnapshot(
                        taken_at=_time(reading.get('taken_at')) or self.clock(),
                        window=window,
                        used_pct=pct,
                        resets_at=resets,
                        source=str(reading.get('source', 'unknown')),
                    )
                )
            s.commit()

    def _attribute(self, reading: dict[str, Any]) -> None:
        window = reading['five_hour']
        key, pct = str(window.get('resets_at', '')), float(window['used_pct'])
        before, self.previous = self.previous, (key, pct)
        if before is None or before[0] != key or pct <= before[1] or self.registry is None:
            return
        with Session(self.registry.engine) as s:
            live = list(s.exec(select(Task.id).where(col(Task.state).in_(list(SPENDING)))))
        share = (pct - before[1]) / max(len(live), 1)
        for task_id in live or ['other']:
            self.by_task[task_id] = round(self.by_task.get(task_id, 0.0) + share, 4)

    def usage_by_task(self) -> dict[str, float]:
        """Percentage points of the five-hour window each task used (best effort), and `other`."""
        return dict(self.by_task)

    def _pct(self, name: str) -> float:
        if self.reading is None:
            return 0.0
        return float(self.reading[name]['used_pct'])

    def _clock(self, when: datetime) -> str:
        local, today = when.astimezone(self.tz), self.clock().astimezone(self.tz)
        return local.strftime('%H:%M' if local.date() == today.date() else '%a %H:%M')

    # The state file ---------------------------------------------------------------------------

    def save(self) -> None:
        if self.state_path is None:
            return
        data = {
            'hold': asdict(self.hold) if self.hold else None,
            'notified_until': self.notified_until,
            'quiet_until': self.quiet_until,
            'last_fresh_at': self.last_fresh_at,
            'raw_logged': sorted(self.raw_logged),
            'limited': sorted(self.limited),
            'by_task': self.by_task,
            'previous': list(self.previous) if self.previous else None,
        }
        tmp = self.state_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data))
        tmp.replace(self.state_path)

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text())
            if not isinstance(data, dict):
                raise ValueError('it does not hold a JSON object')
            self.hold = Hold(**data['hold']) if data.get('hold') else None
            if self.hold is not None and None in (_time(self.hold.until), _time(self.hold.since)):
                raise ValueError('the hold has no readable times')
            self.notified_until = data.get('notified_until')
            self.quiet_until = data.get('quiet_until')
            self.last_fresh_at = data.get('last_fresh_at')
            self.raw_logged = set(data.get('raw_logged') or [])
            self.limited = set(data.get('limited') or [])
            self.by_task = {str(k): float(v) for k, v in (data.get('by_task') or {}).items()}
            previous = data.get('previous')
            self.previous = (str(previous[0]), float(previous[1])) if previous else None
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            log.warning(
                'Could not read %s (%s). The usage guard starts without a hold.',
                self.state_path,
                exc,
            )
            self._clean()


def _until(hold: Hold) -> datetime:
    until = _time(hold.until)
    assert until is not None  # checked when the hold was made or loaded
    return until


def _same(stored: datetime | None, resets: datetime | None) -> bool:
    if stored is None or resets is None:
        return stored is resets
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=UTC)
    return stored == resets


class LimitHits:
    """The task registry's publisher: passes every event on, and tells the guard about a
    transcript `error` with `usage_limit` or a task stopped by the usage limit."""

    def __init__(self, guard: UsageGuard, inner: Publisher) -> None:
        self.guard = guard
        self.inner = inner

    def publish(self, type: str, data: dict[str, Any]) -> None:
        self.inner.publish(type, data)
        if type != 'task.event':
            return
        try:
            task_id, event = data['task_id'], data['data']
            if data['type'] == 'error' and event.get('usage_limit') is True:
                self.guard.limit_hit(task_id, event)
            elif data['type'] == 'state':
                if event['to'] == TaskState.needs_you and event.get('reason') == LIMIT_REASON:
                    if task_id not in self.guard.limited:
                        self.guard.limit_hit(task_id)  # the raw session row: `on_session`
                elif event['to'] not in (TaskState.needs_you, TaskState.unknown):
                    self.guard.limited.discard(task_id)
        except Exception:
            log.exception('The usage guard could not read a task event. It was passed on.')


# Routes and wiring ------------------------------------------------------------------------------

router = APIRouter(prefix='/api', dependencies=[Depends(require_device)])


@router.get('/usage')
def get_usage(request: Request) -> Any:
    guard: UsageGuard = request.app.state.usage_guard
    try:
        return guard.current()
    except NoReading as exc:
        return JSONResponse({'code': 'no_reading', 'message': str(exc)}, status_code=503)


def create(
    reader: Reader | None,
    data_dir: Path,
    pusher: Pushes | None,
    publisher: Publisher,
    tz: tzinfo = UTC,
) -> UsageGuard:
    """The guard, before the task registry exists: its `task_publisher` goes to `tasks.install`.
    `tz` is the owner's time zone (`Settings.tz`): its pushes say times in it."""
    return UsageGuard(
        reader, pusher=pusher, publisher=publisher, state_path=data_dir / STATE_FILE, tz=tz
    )


def install(
    app: FastAPI, guard: UsageGuard, registry: TaskRegistry, watch: Any | None
) -> UsageGuard:
    """Hook the guard into the registry's spawns and drains, mount `GET /api/usage`, and, with a
    runner, give the brain's `usage_get` its readings and log a session's first raw limit hit."""
    guard.registry = registry
    registry.hold_new_work = guard.holding
    app.state.usage_guard = guard
    app.include_router(router)
    if watch is not None:
        watch.listeners.append(guard)
        app.state.brain_ports.usage = guard
    return guard
