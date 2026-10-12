"""The scheduler (S-09.1, B-22, B-23): recurring work fires at its time, or Marcel says it did not.

- **The job store is the `schedule` table.** Each row keeps its cron, time zone, `paused`, and
  `next_run_at` / `last_run_at` (UTC), so a restart finds every schedule and when it is next due.
  Cron fields are wall-clock time in the schedule's own `tz` (`cron.py` covers the clock changes).
- **Firing.** A loop on the hub's event loop looks every 30 seconds (sooner when a fire is due).
  A schedule whose time has come fires once, and `next_run_at` moves past now *before* the work
  is handed on, so a crash never fires it twice:
  - `brain`: a `system_event` (`schedule_fired`, the prompt in `data`) goes to the brain of the
    agent's main conversation, through the channel outbox;
  - `nuc` / `cloud`: a worker task is spawned with the prompt, through the task registry, so the
    usage guard and the worker cap apply: when either holds, the task is queued, never dropped.
  The prompt is the owner's text handed to Claude as text. Nothing here runs it as a command.
- **Missed fires are not made up.** A fire more than `GRACE` late (the hub was down) is skipped:
  the schedule moves on to its next time, and all the fires one look found missed are reported
  in **one** message in the main conversation and one push, however many there were.
- **"Did not run".** With `deadline_minutes`, a fire is watched: if the run has not started that
  long after the fire time (the task is still queued, or the brain has not taken the event), one
  message and one push say so, with the reason. Watches are kept in `<data_dir>/scheduler.json`,
  so a restart keeps watching and does not say it twice.
- **Pause, resume, delete.** A paused schedule has no `next_run_at`; resuming starts from the next
  time after now (the paused stretch is not reported as missed). Built-in schedules (the digests)
  can be paused and changed, not deleted.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from marcel_hub.channel.outbox import conversation_target
from marcel_hub.conversations import ensure_main_conversation, post_message
from marcel_hub.models import (
    Agent,
    MessageKind,
    MessageRole,
    QueuedReason,
    Schedule,
    ScheduleLocation,
    TaskLocation,
    TaskOrigin,
    TaskState,
)
from marcel_hub.notify.message import BODY_MAX, PushNotification, clip, message_link
from marcel_hub.schedule.cron import Cron, CronError, zone
from marcel_hub.tasks.registry import TaskNotFound, TaskRegistry, iso
from marcel_hub.ws import EventLog

log = logging.getLogger(__name__)

GRACE = timedelta(minutes=10)  # a fire later than this is missed, not run late
LOOK_EVERY = 30.0  # seconds between looks, at most
MISSED_COUNT_CAP = 1000  # missed fires of one schedule counted, at most
STATE_FILE = 'scheduler.json'
# The app-api `Schedule`'s fields, in order.
API_FIELDS = (
    'id', 'agent_id', 'name', 'cron', 'tz', 'location', 'prompt', 'paused', 'builtin',
    'deadline_minutes', 'last_run_at', 'next_run_at',
)  # fmt: skip
CHANGEABLE = frozenset({'name', 'cron', 'tz', 'prompt', 'paused', 'deadline_minutes'})


class ScheduleNotFound(LookupError):
    """No such schedule for this agent or owner (404 `schedule_not_found`)."""


class ScheduleBuiltin(PermissionError):
    """A built-in schedule cannot be deleted (409 `schedule_builtin`)."""


class Frames(Protocol):
    """The channel outbox, as far as the scheduler uses it (`channel.outbox.ChannelOutbox`)."""

    def put(self, target: str, frame: dict[str, Any]) -> int: ...

    def main_conversation(self, agent_id: str) -> str: ...

    def after(self, target: str, seq: int, limit: int = ...) -> list[dict[str, Any]]: ...


class Pushes(Protocol):
    def push(self, n: PushNotification, user_id: str | None) -> bool: ...


@dataclass
class Watch:
    """A fire with a deadline, until its run is seen starting or the alert is sent."""

    fired_at: str  # ISO, UTC
    deadline_at: str
    ref: str  # the task id, `<outbox target>#<seq>` for the brain, or '' when the fire failed


def _aware(value: datetime) -> datetime:
    """SQLite gives naive datetimes back; every one stored here is UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def default_brain_frame(schedule: Schedule, fired_at: datetime) -> dict[str, Any]:
    """The `system_event` a `brain` schedule sends; replaceable (`Scheduler.brain_frame`)."""
    return {
        'type': 'system_event',
        'kind': 'schedule_fired',
        'text': f'The schedule "{schedule.name}" is due. Do what its prompt asks.',
        'data': {
            'schedule_id': schedule.id,
            'name': schedule.name,
            'prompt': schedule.prompt,
            'fired_at': iso(fired_at),
        },
    }


class Scheduler:
    def __init__(
        self,
        engine: Engine,
        registry: TaskRegistry,
        frames: Frames,
        *,
        events: EventLog | None = None,
        pusher: Pushes | None = None,
        state_path: Path | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.engine = engine
        self.registry = registry
        self.frames = frames
        self.events = events or EventLog(engine)
        self.pusher = pusher
        self.state_path = state_path
        self.clock = clock
        self.brain_frame: Callable[[Schedule, datetime], dict[str, Any]] = default_brain_frame
        self.watches: dict[str, Watch] = {}
        self._job: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self._load()

    # The schedules ------------------------------------------------------------------------------

    def to_api(self, row: Schedule) -> dict[str, Any]:
        body: dict[str, Any] = {}
        for name in API_FIELDS:
            value = getattr(row, name)
            if value is None:
                continue
            body[name] = iso(value) if isinstance(value, datetime) else value
        return body

    def list(self, agent_id: str) -> list[dict[str, Any]]:
        """Every schedule of the agent, by next run; paused ones last."""
        with Session(self.engine) as s:
            rows = list(s.exec(select(Schedule).where(Schedule.agent_id == agent_id)))
        far = datetime.max.replace(tzinfo=UTC)
        rows.sort(key=lambda r: (_aware(r.next_run_at) if r.next_run_at else far, r.name, r.id))
        return [self.to_api(r) for r in rows]

    def create(self, agent_id: str, spec: dict[str, Any], builtin: bool = False) -> dict[str, Any]:
        """Store a new schedule (app-api `NewSchedule` without `agent_id`). Raises `CronError`."""
        cron = Cron.parse(spec['cron'], spec['tz'])
        row = Schedule(
            agent_id=agent_id,
            name=spec['name'],
            cron=spec['cron'],
            tz=spec['tz'],
            location=ScheduleLocation(spec['location']),
            prompt=spec['prompt'],
            deadline_minutes=spec.get('deadline_minutes'),
            builtin=builtin,
            next_run_at=cron.next_after(self.clock()),
        )
        with Session(self.engine) as s:
            s.add(row)
            s.commit()
            s.refresh(row)
        log.info('Schedule %s "%s" created: %s in %s.', row.id, row.name, row.cron, row.tz)
        return self.to_api(row)

    def owned(self, schedule_id: str, user_id: str) -> Schedule:
        """The schedule if its agent belongs to `user_id`; else `ScheduleNotFound`."""
        with Session(self.engine) as s:
            row = s.get(Schedule, schedule_id)
            agent = s.get(Agent, row.agent_id) if row else None
        if row is None or agent is None or agent.owner_user_id != user_id:
            raise ScheduleNotFound(schedule_id)
        return row

    def update(self, schedule_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Pause, resume or change a schedule. Raises `ScheduleNotFound` or `CronError`."""
        unknown = set(changes) - CHANGEABLE
        if unknown:
            raise ValueError(f'These cannot be changed: {sorted(unknown)}.')
        with Session(self.engine) as s:
            row = s.get(Schedule, schedule_id)
            if row is None:
                raise ScheduleNotFound(schedule_id)
            cron = Cron.parse(changes.get('cron', row.cron), changes.get('tz', row.tz))
            was_paused = row.paused
            for name, value in changes.items():
                setattr(row, name, value)
            if row.paused:
                row.next_run_at = None
            elif was_paused or {'cron', 'tz'} & set(changes) or row.next_run_at is None:
                row.next_run_at = cron.next_after(self.clock())
            s.add(row)
            s.commit()
            s.refresh(row)
        if 'paused' in changes and changes['paused'] != was_paused:
            log.info('Schedule %s %s.', schedule_id, 'paused' if row.paused else 'resumed')
        return self.to_api(row)

    def delete(self, agent_id: str, schedule_id: str) -> None:
        """Raises `ScheduleNotFound` for a schedule not on this agent, `ScheduleBuiltin` for a
        built-in one."""
        with Session(self.engine) as s:
            row = s.get(Schedule, schedule_id)
            if row is None or row.agent_id != agent_id:
                raise ScheduleNotFound(schedule_id)
            if row.builtin:
                raise ScheduleBuiltin(
                    f'"{row.name}" is built in: it can be paused in the app, not deleted.'
                )
            s.delete(row)
            s.commit()
        if self.watches.pop(schedule_id, None) is not None:
            self.save()
        log.info('Schedule %s deleted.', schedule_id)

    # The loop -----------------------------------------------------------------------------------

    def start(self) -> None:
        self._stop = asyncio.Event()
        self._job = asyncio.create_task(self.run(), name='scheduler')

    async def stop(self) -> None:
        """Let a look that is running finish (a spawn is never cut in half), then end the loop."""
        if self._job is None or self._stop is None:
            return
        self._stop.set()
        await asyncio.gather(self._job, return_exceptions=True)
        self._job = None

    async def run(self) -> None:
        assert self._stop is not None
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:  # the scheduler must keep looking, whatever one look did
                log.exception('The scheduler could not look at its schedules. It tries again.')
            with contextlib.suppress(TimeoutError):  # the wait ended: look again
                await asyncio.wait_for(self._stop.wait(), timeout=self.next_wait())

    def next_wait(self) -> float:
        """Seconds to the next fire or deadline, at most `LOOK_EVERY`, at least one."""
        now = self.clock()
        with Session(self.engine) as s:
            times = [
                _aware(t)
                for t in s.exec(
                    select(Schedule.next_run_at)
                    .where(col(Schedule.paused).is_(False))
                    .where(col(Schedule.next_run_at).is_not(None))
                )
                if t is not None
            ]
        times += [_time(w.deadline_at) for w in self.watches.values()]
        soonest = min(((t - now).total_seconds() for t in times), default=LOOK_EVERY)
        return min(LOOK_EVERY, max(soonest, 1.0))

    async def tick(self) -> None:
        """Fire what is due, skip and report what was missed, and check deadlines."""
        now = self.clock().astimezone(UTC)
        with Session(self.engine) as s:
            due = list(
                s.exec(
                    select(Schedule)
                    .where(col(Schedule.paused).is_(False))
                    .where(col(Schedule.next_run_at).is_not(None))
                    .where(col(Schedule.next_run_at) <= now)
                    .order_by(col(Schedule.next_run_at), col(Schedule.id))
                )
            )
        missed: dict[str, list[tuple[Schedule, int]]] = {}
        for row in due:
            try:
                cron = Cron.parse(row.cron, row.tz)
            except CronError as exc:
                log.warning('Schedule %s cannot fire: %s It is paused.', row.id, exc)
                self._advance(row.id, None, paused=True)
                continue
            at = _aware(row.next_run_at)  # pyright: ignore[reportArgumentType]
            if now - at > GRACE:
                missed.setdefault(row.agent_id, []).append((row, self._count(cron, at, now)))
                self._advance(row.id, cron.next_after(now))
            else:
                await self._fire(row, cron, at, now)
        for agent_id, rows in missed.items():
            self._say_missed(agent_id, rows)
        self._check_deadlines(now)

    @staticmethod
    def _count(cron: Cron, first: datetime, now: datetime) -> int:
        count, at = 0, first
        while at is not None and at <= now and count < MISSED_COUNT_CAP:
            count += 1
            at = cron.next_after(at)
        return count

    def _advance(
        self,
        schedule_id: str,
        next_run_at: datetime | None,
        last_run_at: datetime | None = None,
        paused: bool | None = None,
    ) -> None:
        with Session(self.engine) as s:
            row = s.get(Schedule, schedule_id)
            if row is None:
                return
            row.next_run_at = next_run_at
            if last_run_at is not None:
                row.last_run_at = last_run_at
            if paused is not None:
                row.paused = paused
            s.add(row)
            s.commit()

    async def _fire(self, row: Schedule, cron: Cron, at: datetime, now: datetime) -> None:
        # Moved on first: whatever happens next, this fire is never repeated.
        self._advance(row.id, cron.next_after(now), last_run_at=now)
        log.info('Schedule %s "%s" fires (%s).', row.id, row.name, row.location.value)
        ref = ''
        try:
            if row.location == ScheduleLocation.brain:
                target = conversation_target(self.frames.main_conversation(row.agent_id))
                ref = f'{target}#{self.frames.put(target, self.brain_frame(row, at))}'
            else:
                task = await self.registry.spawn(
                    row.agent_id,
                    row.name,
                    prompt=row.prompt,
                    location=TaskLocation(row.location.value),
                    origin=TaskOrigin.schedule,
                    schedule_id=row.id,
                )
                ref = task.id
        except Exception:  # the deadline (if any) reports it; the next fire comes as usual
            log.exception('Schedule %s "%s" could not start its run.', row.id, row.name)
        if row.deadline_minutes:
            deadline = at + timedelta(minutes=row.deadline_minutes)
            self.watches[row.id] = Watch(iso(at), iso(deadline), ref)
            self.save()

    # Deadlines --------------------------------------------------------------------------------

    def _started(self, ref: str) -> bool:
        if not ref:
            return False
        if '#' in ref:  # a brain event: started once the brain's channel took it
            target, seq = ref.rsplit('#', 1)
            waiting = self.frames.after(target, int(seq) - 1, 1)
            return not waiting or waiting[0].get('seq') != int(seq)
        try:
            return self.registry.get(ref).state != TaskState.queued
        except TaskNotFound:
            return False

    def _why(self, ref: str) -> str:
        if not ref:
            return 'Marcel could not start it (the hub log says why)'
        if '#' in ref:
            return 'Marcel’s brain has not picked it up'
        try:
            task = self.registry.get(ref)
        except TaskNotFound:
            return 'its task is gone'
        if task.queued_reason == QueuedReason.usage:
            return 'it is waiting because the plan’s usage is near its limit'
        return 'it is waiting for a free worker on the NUC'

    def _check_deadlines(self, now: datetime) -> None:
        changed = False
        for schedule_id, watch in list(self.watches.items()):
            with Session(self.engine) as s:
                row = s.get(Schedule, schedule_id)
            if row is None or self._started(watch.ref):
                del self.watches[schedule_id]
                changed = True
                continue
            if now < _time(watch.deadline_at):
                continue
            del self.watches[schedule_id]
            changed = True
            minutes = row.deadline_minutes or 0
            fired = self._wall(_time(watch.fired_at), row.tz, now)
            text = (
                f'“{row.name}” did not run. It was due at {fired} and had not started '
                f'{minutes} minutes later: {self._why(watch.ref)}.'
            )
            log.warning('Schedule %s did not run: %s', schedule_id, text)
            self._say(row.agent_id, 'A scheduled job did not run', text, f'late:{schedule_id}')
        if changed:
            self.save()

    # Saying so ----------------------------------------------------------------------------------

    def _say_missed(self, agent_id: str, rows: list[tuple[Schedule, int]]) -> None:
        def one(row: Schedule, count: int) -> str:
            more = '+' if count >= MISSED_COUNT_CAP else ''
            return f'“{row.name}”' + (f' ({count}{more} times)' if count > 1 else '')

        names = ', '.join(one(r, c) for r, c in rows)
        jobs = 'a scheduled job was' if len(rows) == 1 else f'{len(rows)} scheduled jobs were'
        text = (
            f'Marcel was off when {jobs} due, so {"it" if len(rows) == 1 else "they"} did not '
            f'run: {names}. Missed runs are not made up; each runs again at its next time.'
        )
        log.warning('%s', text)
        self._say(agent_id, 'Scheduled work did not run', text, 'schedules_missed')

    def _say(self, agent_id: str, title: str, text: str, key: str) -> None:
        """One message in the agent's main conversation, and one push linking to it."""
        try:
            with Session(self.engine) as s:
                agent = s.get(Agent, agent_id)
                if agent is None:
                    return
                main = ensure_main_conversation(s, agent)
                message = post_message(
                    s,
                    self.events,
                    main,
                    role=MessageRole.system,
                    kind=MessageKind.text,
                    body={'text': text},
                )
                message_id, user_id = message.id, agent.owner_user_id
        except Exception:
            log.exception('Could not post "%s" in the conversation.', title)
            return
        if self.pusher is None:
            return
        try:
            self.pusher.push(
                PushNotification(
                    title=title,
                    body=clip(text, BODY_MAX),
                    category='note',
                    level='normal',
                    deep_link=message_link(message_id),
                    collapse_key=key,
                ),
                user_id,
            )
        except Exception:
            log.exception('Could not push "%s".', title)

    def _wall(self, when: datetime, tz: str, now: datetime) -> str:
        try:
            tzinfo: Any = zone(tz)
        except CronError:
            tzinfo = UTC
        local, today = when.astimezone(tzinfo), now.astimezone(tzinfo)
        return local.strftime('%H:%M' if local.date() == today.date() else '%a %H:%M')

    # The state file -----------------------------------------------------------------------------

    def save(self) -> None:
        if self.state_path is None:
            return
        data = {'watches': {k: asdict(w) for k, w in self.watches.items()}}
        tmp = self.state_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data))
        tmp.replace(self.state_path)

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text())
            watches = {str(k): Watch(**v) for k, v in data['watches'].items()}
            for w in watches.values():
                for value in (w.fired_at, w.deadline_at):
                    _time(value)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            log.warning(
                'Could not read %s (%s). The scheduler starts without deadline watches.',
                self.state_path,
                exc,
            )
            return
        self.watches = watches
