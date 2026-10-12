"""Digests (S-09.2, B-17): the hub gathers the facts, the brain writes the digest.

- **Four built-in schedules** per agent, made by `Digests.ensure_builtins` (at start, then on every
  look for agents made later): *Morning digest* 07:00, *Evening digest* 21:00, *Weekly review*
  Sunday 18:00 and *Usage report* 21:00, in the owner's time zone (`Settings.tz`). They are
  `builtin` (paused or changed in the app, never deleted) and run in the `brain`. Their ids name
  the digest: `sch_digest_<digest>_<agent id>`.
- **The usage report is part of the evening digest** while both run at the same time (same cron
  and time zone) and neither is paused: the evening bundle then carries `usage`, and the usage
  report's own fire sends nothing. Moved to another time, it is sent on its own (`digest: usage`);
  paused, the evening digest leaves usage out.
- **The facts bundle** (`Facts.bundle`) is read from the database when a digest fires: open
  approvals, questions, threads in flight, done, failures, PRs ready and merged, new items, stale
  threads, the rest of today's schedules, and (evening, usage, weekly) usage by task. It is
  deterministic (one database and one clock give one bundle), bounded (every list keeps its first
  `MAX_ITEMS`, with `count` and `more` for the rest; every text is cut to `TEXT_MAX`), in the
  schedule's time zone, and carries no secrets: no settings, no approval details (the command an
  approval would run), and anything shaped like a token in a title or summary is replaced by
  `[hidden]`. The weekly review covers the last 7 days; the others cover today since midnight.
- **Delivery.** The bundle goes to the brain as a `digest_due` system event through the channel
  outbox (the scheduler's `brain_frame`), so it waits there while the brain restarts. The brain
  writes the digest with the `digest` skill and publishes it with `artifacts_publish`. The event
  names the agent's digest board (`artifact_id`) once there is one, so each digest is a new version
  of the same item; the first doc Marcel publishes in the inbox after a digest fires becomes it.
- **Announcing.** When the board has a new version, the hub posts it as a card in the main
  conversation and pushes it (category `digest`, the version's summary as the text). The brain
  adds its one headline line with `reply`.
- **When the brain does not publish** within `DEADLINE` (it is down, busy or failed), nothing
  waits on it: the hub logs a warning and posts one plain line of its own in the main
  conversation, with a push, made from the bundle's numbers ("The evening digest was not written
  … In short: 2 approvals wait for you; 3 threads are working."). It never writes the digest itself:
  only the brain thinks. A digest published after that is kept, without a second card.
- **Restart.** The board ids, the digests waiting to be published and the usage baselines (usage
  by task is counted since the last report) are kept in `<data_dir>/digests.json`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from marcel_hub.brain_api import NotSetUp
from marcel_hub.conversations import ensure_main_conversation, post_message
from marcel_hub.models import (
    INBOX_SLUG,
    Agent,
    Approval,
    ApprovalState,
    Artifact,
    ArtifactKind,
    ArtifactVersion,
    MessageKind,
    MessageRole,
    Project,
    Schedule,
    ScheduleLocation,
    Task,
    TaskState,
)
from marcel_hub.notify.message import BODY_MAX, clip
from marcel_hub.projects.store import IN_FLIGHT
from marcel_hub.schedule.cron import Cron, CronError, zone
from marcel_hub.schedule.scheduler import Scheduler, default_brain_frame
from marcel_hub.tasks.registry import iso
from marcel_hub.ws import EventLog

log = logging.getLogger(__name__)

MAX_ITEMS = 10  # per list in a bundle; the rest is counted in `more`
TEXT_MAX = 160  # characters of any title, summary or question
STALE_AFTER = timedelta(days=3)  # a thread with no activity this long is stale
WEEK = timedelta(days=7)
DEADLINE = timedelta(minutes=30)  # the brain publishes within this, or the hub says so
TICK_S = 30.0  # seconds between looks for a published digest
MAX_PER_DAY = 48  # fires of one schedule listed per day, at most
STATE_FILE = 'digests.json'
DEFAULT_TZ = 'Europe/Brussels'
MARCEL = 'Marcel'  # `Artifact.updated_by` of what the brain publishes (brain_api.py)


@dataclass(frozen=True)
class Builtin:
    digest: str
    name: str
    cron: str
    prompt: str
    label: str  # in a sentence: "the morning digest"


BUILTINS = (
    Builtin('morning', 'Morning digest', '0 7 * * *',
            'Write the morning digest (plan of the day) from the facts bundle.', 'morning digest'),
    Builtin('evening', 'Evening digest', '0 21 * * *',
            'Write the evening digest (what got done) from the facts bundle.', 'evening digest'),
    Builtin('weekly', 'Weekly review', '0 18 * * 0',
            'Write the weekly review from the facts bundle.', 'weekly review'),
    Builtin('usage', 'Usage report', '0 21 * * *',
            'Write the usage report from the facts bundle. At the evening digest’s time it is '
            'part of that digest.', 'usage report'),
)  # fmt: skip
BY_DIGEST = {b.digest: b for b in BUILTINS}

# Shapes of common secrets: GitHub, Anthropic/OpenAI-style, Slack and AWS keys, bearer tokens.
SECRET = re.compile(
    r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,}'
    r'|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})'
    r'|\b[Bb]earer\s+[A-Za-z0-9._~+/=-]{16,}'
)


def builtin_id(digest: str, agent_id: str) -> str:
    return f'sch_digest_{digest}_{agent_id}'


def digest_of(row: Schedule) -> str | None:
    """Which digest a schedule is (`morning`, …), or None for any other schedule."""
    if not row.builtin:
        return None
    for b in BUILTINS:
        if row.id == builtin_id(b.digest, row.agent_id):
            return b.digest
    return None


def safe_text(value: str | None) -> str:
    """One line, at most `TEXT_MAX` characters, with token-like words hidden."""
    return clip(SECRET.sub('[hidden]', value or ''), TEXT_MAX)


def capped(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {'count': len(items), 'items': items[:MAX_ITEMS], 'more': max(0, len(items) - MAX_ITEMS)}


def _aware(value: datetime) -> datetime:
    """SQLite gives naive datetimes back; every one stored is UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class UsageSource(Protocol):
    """The usage guard (`usage.UsageGuard`), as far as digests read it."""

    def current(self) -> dict[str, Any]: ...

    def usage_by_task(self) -> dict[str, float]: ...


# The facts bundle ---------------------------------------------------------------------------


class Facts:
    """Reads the facts bundle from the database (and the usage guard). Never writes."""

    def __init__(self, engine: Engine, usage: UsageSource | None = None) -> None:
        self.engine = engine
        self.usage = usage

    def bundle(
        self,
        agent_id: str,
        digest: str,
        tz: str,
        now: datetime,
        *,
        include_usage: bool = False,
        usage_baseline: dict[str, float] | None = None,
        usage_since: datetime | None = None,
        skip_artifact: str | None = None,
    ) -> dict[str, Any]:
        """The bundle for `digest` at `now`. `usage_baseline` is usage by task at the last report
        (counted from there); `skip_artifact` is the digest board, left out of the new items."""
        try:
            where: tzinfo = zone(tz)
        except CronError:
            where = UTC
        now = _aware(now)
        local_now = now.astimezone(where)
        midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        since = now - WEEK if digest == 'weekly' else midnight.astimezone(UTC)

        def at(value: datetime | None) -> str | None:
            return None if value is None else _aware(value).astimezone(where).isoformat()

        with Session(self.engine) as s:
            projects = {
                p.id: p.name for p in s.exec(select(Project).where(Project.agent_id == agent_id))
            }
            tasks = {
                t.id: t
                for t in s.exec(
                    select(Task).where(Task.agent_id == agent_id).order_by(col(Task.created_at))
                )
            }
            approvals = list(
                s.exec(
                    select(Approval)
                    .where(col(Approval.task_id).in_(list(tasks)))
                    .where(Approval.state == ApprovalState.open)
                    .order_by(col(Approval.created_at), col(Approval.id))
                )
            )
            artifacts = list(
                s.exec(
                    select(Artifact)
                    .where(col(Artifact.project_id).in_(list(projects)))
                    .where(Artifact.kind != ArtifactKind.board)
                    .order_by(col(Artifact.created_at), col(Artifact.id))
                )
            )
            schedules = list(
                s.exec(
                    select(Schedule)
                    .where(Schedule.agent_id == agent_id)
                    .where(col(Schedule.paused).is_(False))
                )
            )

        def thread(task_id: str | None) -> str | None:
            task = tasks.get(task_id) if task_id else None
            return safe_text(task.title) if task else None

        def by_time(rows: Iterable[Task], key: Callable[[Task], datetime]) -> list[Task]:
            return sorted(rows, key=lambda t: (_aware(key(t)), t.id))

        live = [t for t in tasks.values() if t.archived_at is None]
        asking = {a.task_id for a in approvals}
        recent = [t for t in tasks.values() if _aware(t.updated_at) >= since]

        bundle: dict[str, Any] = {
            'digest': digest,
            'tz': tz if where is not UTC else 'UTC',
            'now': at(now),
            'period': {'from': at(since), 'to': at(now)},
            'approvals': capped([
                {'approval_id': a.id, 'task_id': a.task_id, 'thread': thread(a.task_id),
                 'asks': safe_text(a.summary), 'tool': safe_text(a.tool),
                 'since': at(a.created_at)}
                for a in approvals
            ]),
            'questions': capped([
                {'task_id': t.id, 'thread': safe_text(t.title),
                 'question': safe_text(t.waiting_for), 'since': at(t.updated_at)}
                for t in by_time(live, lambda t: t.updated_at)
                if t.state == TaskState.needs_you and t.id not in asking
            ]),
            'in_flight': capped([
                {'task_id': t.id, 'thread': safe_text(t.title), 'state': t.state.value,
                 'where': t.location.value, 'project': safe_text(projects.get(t.project_id or '')),
                 'since': at(t.created_at), 'last_activity': at(t.last_activity_at)}
                for t in by_time(live, lambda t: t.created_at)
                if t.state in IN_FLIGHT
            ]),
            'done': capped([
                {'task_id': t.id, 'thread': safe_text(t.title), 'outcome': safe_text(t.summary),
                 'finished_at': at(t.updated_at)}
                for t in by_time(recent, lambda t: t.updated_at)
                if t.state == TaskState.done
            ]),
            'failures': capped([
                {'task_id': t.id, 'thread': safe_text(t.title),
                 'why': safe_text(t.summary or t.waiting_for), 'at': at(t.updated_at)}
                for t in by_time(recent, lambda t: t.updated_at)
                if t.state == TaskState.failed
            ]),
            'prs_ready': capped([
                _pr(a, thread(a.task_id)) for a in artifacts if _pr_ready(a)
            ]),
            'prs_merged': capped([
                _pr(a, thread(a.task_id))
                for a in artifacts
                if a.kind == ArtifactKind.pr
                and a.meta_json.get('pr', {}).get('state') == 'merged'
                and _aware(a.updated_at) >= since
            ]),
            'artifacts': capped([
                {'artifact_id': a.id, 'title': safe_text(a.title), 'kind': a.kind.value,
                 'thread': thread(a.task_id), 'at': at(a.created_at)}
                for a in artifacts
                if _aware(a.created_at) >= since and a.id != skip_artifact
            ]),
            'stale': capped([
                {'task_id': t.id, 'thread': safe_text(t.title), 'state': t.state.value,
                 'last_activity': at(_last(t)),
                 'idle_days': int((now - _aware(_last(t))).total_seconds() // 86400)}
                for t in sorted(live, key=lambda t: (_aware(_last(t)), t.id))
                if now - _aware(_last(t)) >= STALE_AFTER
            ]),
            'schedules_today': capped(_today(schedules, now, local_now, at)),
        }  # fmt: skip
        if include_usage:
            bundle['usage'] = self._usage(tasks, usage_baseline or {}, usage_since, at)
        return bundle

    def _usage(
        self,
        tasks: dict[str, Task],
        baseline: dict[str, float],
        since: datetime | None,
        at: Callable[[datetime | None], str | None],
    ) -> dict[str, Any]:
        plan: dict[str, Any] | None = None
        by_task: dict[str, float] = {}
        if self.usage is not None:
            try:
                reading = self.usage.current()
                plan = {
                    name: {
                        'used_pct': reading[name]['used_pct'],
                        'resets_at': _local(reading[name].get('resets_at'), at),
                    }
                    for name in ('five_hour', 'weekly')
                }
                plan['stale'] = bool(reading.get('stale'))
            except NotSetUp:
                plan = None
            by_task = self.usage.usage_by_task()
        spent: dict[str, float] = {}
        for task_id, points in by_task.items():
            before = baseline.get(task_id, 0.0)
            # The guard's count only grows; a smaller number means it started again.
            delta = points - before if points >= before else points
            if delta > 0.0001:
                spent[task_id] = delta
        total = sum(spent.values())
        rows = sorted(spent.items(), key=lambda kv: (-kv[1], kv[0]))
        items = [
            {
                'task_id': None if task_id == 'other' else task_id,
                'thread': safe_text(tasks[task_id].title)
                if task_id in tasks
                else ('Other work' if task_id == 'other' else 'Another agent’s thread'),
                'points': round(points, 1),
                'share_pct': round(points / total * 100) if total else 0,
            }
            for task_id, points in rows
        ]
        return {'plan': plan, 'by_task': capped(items), 'since': at(since)}


def _last(task: Task) -> datetime:
    return task.last_activity_at or task.updated_at


def _local(value: Any, at: Callable[[datetime | None], str | None]) -> str | None:
    if isinstance(value, datetime):
        return at(value)
    if isinstance(value, str):
        try:
            return at(datetime.fromisoformat(value.replace('Z', '+00:00')))
        except ValueError:
            return None
    return None


def _pr_ready(art: Artifact) -> bool:
    """Open (not a draft), checks passing, and no reviewer asked for changes: the owner can merge.
    An approving review is not required: the owner is usually the reviewer."""
    pr = art.meta_json.get('pr', {})
    return (
        art.kind == ArtifactKind.pr
        and pr.get('state') == 'open'
        and pr.get('checks') == 'passing'
        and pr.get('review') != 'changes_requested'
    )


def _pr(art: Artifact, thread: str | None) -> dict[str, Any]:
    pr = art.meta_json.get('pr', {})
    url = art.url if art.url and art.url.startswith('https://') else None
    return {
        'artifact_id': art.id,
        'title': safe_text(art.title),
        'url': url if url and not SECRET.search(url) else None,
        'checks': pr.get('checks'),
        'review': pr.get('review'),
        'thread': thread,
    }


def _today(
    schedules: list[Schedule],
    now: datetime,
    local_now: datetime,
    at: Callable[[datetime | None], str | None],
) -> list[dict[str, Any]]:
    """Every fire of the agent's other schedules from now to midnight, by time (not digests)."""
    end = (
        local_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    ).astimezone(UTC)
    fires: list[tuple[datetime, str, dict[str, Any]]] = []
    for row in schedules:
        if digest_of(row) is not None:
            continue
        try:
            cron = Cron.parse(row.cron, row.tz)
        except CronError:
            continue
        when, count = cron.next_after(now), 0
        while when is not None and when < end and count < MAX_PER_DAY:
            fires.append((when, row.id, {
                'schedule_id': row.id, 'name': safe_text(row.name), 'at': at(when),
                'where': row.location.value,
            }))  # fmt: skip
            when, count = cron.next_after(when), count + 1
    fires.sort(key=lambda f: (f[0], f[1]))
    return [f[2] for f in fires]


def headline(bundle: dict[str, Any]) -> str:
    """A plain line from the bundle's numbers, for when the brain wrote nothing."""
    parts: list[str] = []

    def n(key: str) -> int:
        return int(bundle.get(key, {}).get('count', 0))

    def say(count: int, one: str, many: str) -> None:
        if count:
            parts.append((one if count == 1 else many).format(count))

    say(n('approvals') + n('questions'), '1 thing waits for you', '{} things wait for you')
    say(n('failures'), '1 thread failed', '{} threads failed')
    say(n('in_flight'), '1 thread is working', '{} threads are working')
    say(n('done'), '1 finished', '{} finished')
    say(n('prs_ready'), '1 pull request is ready to merge', '{} pull requests are ready to merge')
    if bundle.get('digest') == 'weekly':
        say(n('stale'), '1 thread looks stale', '{} threads look stale')
    plan = (bundle.get('usage') or {}).get('plan')
    if plan:
        parts.append(f'the plan is at {round(plan["weekly"]["used_pct"])} % of the week')
    if not parts:
        return 'Nothing needs you.'
    line = '; '.join(parts)
    return line[0].upper() + line[1:] + '.'


# Firing and announcing ----------------------------------------------------------------------


class DigestPushes(Protocol):
    def digest(
        self, message_id: str, title: str, body: str, user_id: str | None = None
    ) -> None: ...


@dataclass
class Pending:
    """A digest sent to the brain and not published yet."""

    agent_id: str
    digest: str
    fired_at: str  # ISO, UTC
    deadline_at: str
    headline: str


@dataclass
class Baseline:
    at: str  # ISO, UTC
    by_task: dict[str, float]


class Digests:
    def __init__(
        self,
        engine: Engine,
        facts: Facts,
        *,
        events: EventLog | None = None,
        pusher: DigestPushes | None = None,
        state_path: Path | None = None,
        tz: str = DEFAULT_TZ,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.engine = engine
        self.facts = facts
        self.events = events or EventLog(engine)
        self.pusher = pusher
        self.state_path = state_path
        self.tz = tz
        self.clock = clock
        self.boards: dict[str, str] = {}  # agent id → its digest board's artifact id
        self.pending: list[Pending] = []
        self.baselines: dict[str, Baseline] = {}  # 'day' / 'week' → usage by task at the report
        self._job: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self._load()

    # The built-in schedules -------------------------------------------------------------------

    def ensure_builtins(self) -> int:
        """Make each agent's missing digest schedules. Returns how many were made."""
        now = self.clock()
        made = 0
        with Session(self.engine) as s:
            agent_ids = list(s.exec(select(Agent.id)))
            have = set(s.exec(select(Schedule.id).where(col(Schedule.builtin).is_(True))))
            for agent_id in agent_ids:
                for b in BUILTINS:
                    sid = builtin_id(b.digest, agent_id)
                    if sid in have:
                        continue
                    s.add(Schedule(
                        id=sid, agent_id=agent_id, name=b.name, cron=b.cron, tz=self.tz,
                        location=ScheduleLocation.brain, prompt=b.prompt, builtin=True,
                        next_run_at=Cron.parse(b.cron, self.tz).next_after(now),
                    ))  # fmt: skip
                    made += 1
            s.commit()
        if made:
            log.info('Made %d built-in digest schedule(s).', made)
        return made

    def _rows(self, agent_id: str) -> dict[str, Schedule]:
        with Session(self.engine) as s:
            rows = s.exec(
                select(Schedule).where(
                    col(Schedule.id).in_([builtin_id(b.digest, agent_id) for b in BUILTINS])
                )
            )
            return {d: r for r in rows if (d := digest_of(r)) is not None}

    @staticmethod
    def usage_with_evening(rows: dict[str, Schedule]) -> bool:
        """The usage report goes with the evening digest: same time, neither paused."""
        evening, usage = rows.get('evening'), rows.get('usage')
        return (
            evening is not None
            and usage is not None
            and not evening.paused
            and not usage.paused
            and (evening.cron, evening.tz) == (usage.cron, usage.tz)
        )

    # The system event (the scheduler's `brain_frame`) -----------------------------------------

    def frame(self, row: Schedule, fired_at: datetime) -> dict[str, Any] | None:
        """The `digest_due` event for a digest schedule; any other schedule's usual event. None
        for the usage report when the evening digest carries it."""
        digest = digest_of(row)
        if digest is None:
            return default_brain_frame(row, fired_at)
        rows = self._rows(row.agent_id)
        with_evening = self.usage_with_evening(rows)
        if digest == 'usage' and with_evening:
            log.info('The usage report goes with the evening digest; nothing sent on its own.')
            return None
        include_usage = digest in ('usage', 'weekly') or (digest == 'evening' and with_evening)
        scope = 'week' if digest == 'weekly' else 'day'
        now = self.clock()
        before = self.baselines.get(scope)
        board = self._board(row.agent_id)
        bundle = self.facts.bundle(
            row.agent_id,
            digest,
            row.tz,
            now,
            include_usage=include_usage,
            usage_baseline=before.by_task if before else None,
            usage_since=datetime.fromisoformat(before.at) if before else None,
            skip_artifact=board,
        )
        if include_usage and self.facts.usage is not None:
            self.baselines[scope] = Baseline(iso(now), self.facts.usage.usage_by_task())
        label = BY_DIGEST[digest].label
        self.pending = [p for p in self.pending if (p.agent_id, p.digest) != (row.agent_id, digest)]
        self.pending.append(
            Pending(row.agent_id, digest, iso(now), iso(now + DEADLINE), headline(bundle))
        )
        self.save()
        data: dict[str, Any] = {'digest': digest, 'facts': bundle}
        if board is not None:
            data['artifact_id'] = board
        log.info('The %s is due for agent %s; the facts go to the brain.', label, row.agent_id)
        return {
            'type': 'system_event',
            'kind': 'digest_due',
            'text': f'The {label} is due. The facts bundle is attached; write it with the digest '
            'skill.',
            'data': data,
        }

    def _board(self, agent_id: str) -> str | None:
        board = self.boards.get(agent_id)
        if board is None:
            return None
        with Session(self.engine) as s:
            if s.get(Artifact, board) is None:
                del self.boards[agent_id]
                return None
        return board

    # Watching for the published digest --------------------------------------------------------

    def start(self) -> None:
        self._stop = asyncio.Event()
        self._job = asyncio.create_task(self.run(), name='digests')

    async def stop(self) -> None:
        if self._job is None or self._stop is None:
            return
        self._stop.set()
        await asyncio.gather(self._job, return_exceptions=True)
        self._job = None

    async def run(self) -> None:
        assert self._stop is not None
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_S)
            if self._stop.is_set():
                return
            try:
                self.ensure_builtins()
                self.check()
            except Exception:  # keep looking, whatever one look did
                log.exception('The digests could not look for published digests. They try again.')

    def check(self) -> None:
        """Announce each digest the brain has published; say so for each one it did not."""
        now = self.clock()
        kept: list[Pending] = []
        for p in self.pending:
            art = self._published(p)
            if art is not None:
                self.boards[p.agent_id] = art.id
                self._announce(p, art)
            elif now >= datetime.fromisoformat(p.deadline_at):
                self._fallback(p)
            else:
                kept.append(p)
        if kept != self.pending:
            self.pending = kept
            self.save()

    def _published(self, p: Pending) -> Artifact | None:
        """The doc Marcel published since the digest fired: the board when it has a new version,
        else the newest doc or dashboard Marcel published in the agent's inbox."""
        fired = datetime.fromisoformat(p.fired_at)
        board = self.boards.get(p.agent_id)
        with Session(self.engine) as s:
            inbox = s.exec(
                select(Project.id)
                .where(Project.agent_id == p.agent_id)
                .where(Project.slug == INBOX_SLUG)
                .where(col(Project.archived).is_(False))
            ).first()
            found = list(
                s.exec(
                    select(Artifact)
                    .where(col(Artifact.project_id) == inbox)
                    .where(col(Artifact.task_id).is_(None))
                    .where(Artifact.updated_by == MARCEL)
                    .where(col(Artifact.kind).in_([ArtifactKind.doc, ArtifactKind.dashboard]))
                    .where(col(Artifact.updated_at) >= fired)
                    .order_by(col(Artifact.updated_at).desc(), col(Artifact.id).desc())
                )
            )
        if not found:
            return None
        for art in found:
            if art.id == board:
                return art
        return found[0]

    def _announce(self, p: Pending, art: Artifact) -> None:
        label = BY_DIGEST[p.digest].label
        log.info('The %s is published as %s (version %d).', label, art.id, art.current_version)
        with Session(self.engine) as s:
            version = s.get(ArtifactVersion, (art.id, art.current_version))
            summary = (version.summary if version else None) or art.title
        message_id, user_id = self._post(
            p.agent_id, role=MessageRole.agent, kind=MessageKind.artifact, body={},
            artifact_id=art.id, project_id=art.project_id,
        )  # fmt: skip
        if message_id is not None:
            self._push(message_id, label, summary, user_id)

    def _fallback(self, p: Pending) -> None:
        label = BY_DIGEST[p.digest].label
        minutes = int(DEADLINE.total_seconds() // 60)
        text = (
            f'The {label} was not written: Marcel did not publish it within {minutes} minutes. '
            f'In short: {p.headline}'
        )
        log.warning('The %s for agent %s was not published in time.', label, p.agent_id)
        message_id, user_id = self._post(
            p.agent_id, role=MessageRole.system, kind=MessageKind.text, body={'text': text}
        )
        if message_id is not None:
            self._push(message_id, label, text, user_id)

    def _post(self, agent_id: str, **message: Any) -> tuple[str | None, str | None]:
        try:
            with Session(self.engine) as s:
                agent = s.get(Agent, agent_id)
                if agent is None:
                    return None, None
                main = ensure_main_conversation(s, agent)
                posted = post_message(s, self.events, main, **message)
                return posted.id, agent.owner_user_id
        except Exception:
            log.exception('Could not post the digest in the conversation of %s.', agent_id)
            return None, None

    def _push(self, message_id: str, label: str, body: str, user_id: str | None) -> None:
        if self.pusher is None:
            return
        title = label[0].upper() + label[1:]
        self.pusher.digest(message_id, title, clip(body, BODY_MAX), user_id)

    # The state file ---------------------------------------------------------------------------

    def save(self) -> None:
        if self.state_path is None:
            return
        data = {
            'boards': self.boards,
            'pending': [asdict(p) for p in self.pending],
            'baselines': {k: asdict(v) for k, v in self.baselines.items()},
        }
        tmp = self.state_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data))
        tmp.replace(self.state_path)

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text())
            boards = {str(k): str(v) for k, v in data['boards'].items()}
            pending = [Pending(**p) for p in data['pending']]
            baselines = {str(k): Baseline(**v) for k, v in data['baselines'].items()}
            for p in pending:
                datetime.fromisoformat(p.fired_at)
                datetime.fromisoformat(p.deadline_at)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            log.warning('Could not read %s (%s). Digests start afresh.', self.state_path, exc)
            return
        self.boards, self.pending, self.baselines = boards, pending, baselines


def install(
    app: FastAPI,
    scheduler: Scheduler,
    pusher: DigestPushes | None,
    usage: UsageSource | None,
    data_dir: Path,
    tz: str = DEFAULT_TZ,
) -> Digests:
    """Put the digests on `app.state.digests` and have the scheduler send their events. The app's
    lifespan makes the built-in schedules and starts the loop."""
    digests = Digests(
        app.state.engine,
        Facts(app.state.engine, usage),
        events=app.state.events,
        pusher=pusher,
        state_path=data_dir / STATE_FILE,
        tz=tz,
    )
    scheduler.brain_frame = digests.frame
    app.state.digests = digests
    return digests
