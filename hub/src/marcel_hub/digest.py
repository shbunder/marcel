"""Digests (S-09.2, B-17): the hub gathers the facts, the brain writes the digest.

- **Four built-in schedules** per agent, made by `Digests.ensure_builtins` (at start, then on every
  look for agents made later): *Morning digest* 07:00, *Evening digest* 21:00, *Weekly review*
  Sunday 18:00 and *Usage report* 21:00, in the owner's time zone (`Settings.tz`). They are
  `builtin` (paused or changed in the app, never deleted) and run in the `brain`. Their ids name
  the digest: `sch_digest_<digest>_<agent id>`. When `tz` changes, each one the owner has not
  edited (its cron and time zone are still what the hub gave it, kept in the state file) moves to
  the new time zone at start; an edited one is left alone.
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
  `[hidden]` (token shapes, the hub's device tokens, runs of 32+ hex characters, and the value of
  `NAME=value` where NAME ends in secret, token, key or password). The weekly review covers the
  last 7 days; the others cover today since midnight.
- **Delivery.** The bundle goes to the brain as a `digest_due` system event through the channel
  outbox (the scheduler's `brain_frame`), so it waits there while the brain restarts. The brain
  writes the digest with the `digest` skill and publishes it with `artifacts_publish`, with the
  `title` the event gives ("Evening digest — Monday 12 October") and `in_reply_to` set to the
  event's `digest_id` (S-04.14, brain-api 0.1.1). The event names the agent's digest board
  (`artifact_id`) once there is one, so each digest is a new version of the same item. The hub
  knows the published digest first by `in_reply_to` (kept on the version), whatever its title or
  project; without it, by a new version of the board, or a doc by Marcel in the inbox with exactly
  that title, unless that version answers another digest. Any other doc published meanwhile is not
  taken for it. `in_reply_to` with an id the hub never sent (or one older than `KNOWN_ANSWERED`
  answered digests) is refused 404 `digest_not_found`.
- **Announcing.** When the board has a new version, the hub posts it as a card in the main
  conversation and pushes it (category `digest`, the version's summary as the text). The brain
  adds its one headline line with `reply`.
- **When the brain is away** (down or restarting, e.g. a Claude Code self-update) the event waits
  in the outbox and reaches the brain when it is back; `DEADLINE` then counts from that, so no
  "not written" line is said for a digest the brain never got. After `WAIT_FOR_BRAIN` away, the
  hub says so once ("Marcel has not been reachable for 3 hours …") and keeps waiting for it.
- **When the brain does not publish** within `DEADLINE` (it is busy or failed), nothing
  waits on it: the hub logs a warning and posts one plain line of its own in the main
  conversation, with a push, made from the bundle's numbers ("The evening digest was not written
  … In short: 2 approvals wait for you; 3 threads are working."). It never writes the digest itself:
  only the brain thinks. A digest published after that is still recognised (until the next digest
  of its kind, at most `LATE_FOR`): it becomes the board and gets its card, without a second push.
- **Restart.** The board ids, the digests waiting to be published, the usage baselines (usage
  by task is counted since the last report) and what the hub gave each built-in schedule are kept
  in `<data_dir>/digests.json`.
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

from marcel_hub.auth.tokens import TOKEN_PREFIX
from marcel_hub.brain_api import NotSetUp
from marcel_hub.channel.outbox import ChannelOutbox, conversation_target
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
    new_id,
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
# While the event still waits in the outbox (the brain is down or restarting), `DEADLINE` counts
# from when the brain gets it, not from the fire; this long at most, then the hub says so anyway.
WAIT_FOR_BRAIN = timedelta(hours=3)
LATE_FOR = timedelta(days=8)  # a late digest is still recognised this long (or until the next)
TICK_S = 30.0  # seconds between looks for a published digest
MAX_PER_DAY = 48  # fires of one schedule listed per day, at most
STATE_FILE = 'digests.json'
DEFAULT_TZ = 'Europe/Brussels'
MARCEL = 'Marcel'  # `Artifact.updated_by` of what the brain publishes (brain_api.py)
KNOWN_ANSWERED = 50  # answered digest ids still accepted as `in_reply_to` (a fixed version)


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

# Shapes of common secrets: GitHub, Anthropic/OpenAI-style, Slack and AWS keys, bearer tokens,
# the hub's device tokens (`mrc_` + 64 hex) and any run of 32 or more hex characters (the hub
# secret, hashes). A git commit id (40 hex) is hidden too: better than a leaked secret.
SECRET = re.compile(
    r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,}'
    r'|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})'
    r'|\b[Bb]earer\s+[A-Za-z0-9._~+/=-]{16,}'
    r'|' + re.escape(TOKEN_PREFIX) + r'[0-9A-Fa-f]{16,}'
    r'|(?<![0-9A-Fa-f])[0-9A-Fa-f]{32,}(?![0-9A-Fa-f])'
)
# `NAME=value` where NAME is, or ends in, secret / token / key / password (any case), as in
# `MARCEL_HUB_SECRET=…` or `api_key=…`: the value is hidden, the name stays.
SECRET_SETTING = re.compile(
    r'\b((?:[A-Za-z0-9]+[_-])*(?:secret|token|key|password|SECRET|TOKEN|KEY|PASSWORD|Secret|'
    r'Token|Key|Password))\s*=\s*(?:"[^"]*"|\'[^\']*\'|\S+)'
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
    hidden = SECRET_SETTING.sub(r'\1=[hidden]', value or '')
    return clip(SECRET.sub('[hidden]', hidden), TEXT_MAX)


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


def digest_title(digest: str, now: datetime, tz: str) -> str:
    """The title the brain publishes the digest with, e.g. "Evening digest — Monday 12 October"
    (the date in `tz`). The hub knows the published digest by it."""
    try:
        where: tzinfo = zone(tz)
    except CronError:
        where = UTC
    day = _aware(now).astimezone(where)
    return f'{BY_DIGEST[digest].name} — {DAYS[day.weekday()]} {day.day} {MONTHS[day.month - 1]}'


DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
          'September', 'October', 'November', 'December')  # fmt: skip


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
    """A digest sent to the brain and not published yet. `late` once the fallback line was said:
    it is still recognised as the board when it comes, until the next digest of its kind."""

    agent_id: str
    digest: str
    fired_at: str  # ISO, UTC
    deadline_at: str
    headline: str
    title: str = ''  # the title the event asks the brain to publish with
    late: bool = False
    digest_id: str = ''  # the event's `digest_id`; '' for one sent before S-04.14


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
        waiting: Callable[[str, str], bool] = lambda _agent_id, _digest_id: False,
    ) -> None:
        self.engine = engine
        self.facts = facts
        # (agent id, digest id) → the `digest_due` event has not reached the agent's brain yet.
        self.waiting = waiting
        self.events = events or EventLog(engine)
        self.pusher = pusher
        self.state_path = state_path
        self.tz = tz
        self.clock = clock
        self.boards: dict[str, str] = {}  # agent id → its digest board's artifact id
        self.pending: list[Pending] = []
        self.baselines: dict[str, Baseline] = {}  # 'day' / 'week' → usage by task at the report
        # schedule id → the cron and tz the hub last gave a built-in digest. A row that still has
        # them was not edited by the owner, so it follows a changed `tz` setting.
        self.made: dict[str, dict[str, str]] = {}
        # digest id → agent id of the last `KNOWN_ANSWERED` digests published, oldest first: a
        # corrected version may still name one in `in_reply_to`.
        self.answered: dict[str, str] = {}
        self._job: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self._load()

    # The built-in schedules -------------------------------------------------------------------

    def ensure_builtins(self) -> int:
        """Make each agent's missing digest schedules, and move the ones the owner has not edited
        to the `tz` setting. Returns how many were made."""
        now = self.clock()
        made, moved = 0, 0
        with Session(self.engine) as s:
            # An archived agent gets no digests (S-17.7).
            agent_ids = list(s.exec(select(Agent.id).where(col(Agent.archived_at).is_(None))))
            have = {
                r.id: r for r in s.exec(select(Schedule).where(col(Schedule.builtin).is_(True)))
            }
            for agent_id in agent_ids:
                for b in BUILTINS:
                    sid = builtin_id(b.digest, agent_id)
                    if sid in have:
                        moved += self._follow_tz(s, have[sid], b, now)
                        continue
                    self.made[sid] = {'cron': b.cron, 'tz': self.tz}
                    s.add(Schedule(
                        id=sid, agent_id=agent_id, name=b.name, cron=b.cron, tz=self.tz,
                        location=ScheduleLocation.brain, prompt=b.prompt, builtin=True,
                        next_run_at=Cron.parse(b.cron, self.tz).next_after(now),
                    ))  # fmt: skip
                    made += 1
            s.commit()
        if made or moved:
            self.save()
        if made:
            log.info('Made %d built-in digest schedule(s).', made)
        if moved:
            log.info('Moved %d built-in digest schedule(s) to the time zone %s.', moved, self.tz)
        return made

    def _follow_tz(self, s: Session, row: Schedule, b: Builtin, now: datetime) -> int:
        """1 when `row` moved to the `tz` setting. A row the hub has no record of counts as the
        hub's own while its cron is still the built-in one."""
        given = self.made.get(row.id)
        if given is None:
            if row.cron != b.cron:
                return 0
            given = self.made[row.id] = {'cron': row.cron, 'tz': row.tz}
        if (row.cron, row.tz) != (given['cron'], given['tz']) or row.tz == self.tz:
            return 0  # edited by the owner, or already right
        row.tz = self.tz
        if not row.paused:
            row.next_run_at = Cron.parse(row.cron, self.tz).next_after(now)
        s.add(row)
        self.made[row.id] = {'cron': row.cron, 'tz': self.tz}
        return 1

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
        title = digest_title(digest, now, row.tz)
        digest_id = new_id('dig')
        self.pending.append(
            Pending(row.agent_id, digest, iso(now), iso(now + DEADLINE), headline(bundle), title,
                    digest_id=digest_id)
        )  # fmt: skip
        self.save()
        data: dict[str, Any] = {
            'digest': digest, 'title': title, 'facts': bundle, 'digest_id': digest_id,
        }  # fmt: skip
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

    def knows(self, agent_id: str, digest_id: str) -> bool:
        """`brain_api.DigestIds`: a digest this agent's brain was sent, waiting or answered."""
        if self.answered.get(digest_id) == agent_id:
            return True
        return any(p.digest_id == digest_id and p.agent_id == agent_id for p in self.pending)

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
        """Announce each digest the brain has published; say so, once, for each one it did not
        publish in time, and keep recognising it if it comes late."""
        now = self.clock()
        kept: list[Pending] = []
        changed = False
        for p in self.pending:
            art = self._published(p)
            if art is not None:
                self.boards[p.agent_id] = art.id
                self._announce(p, art, push=not p.late)
                if p.digest_id:
                    self.answered[p.digest_id] = p.agent_id
                    while len(self.answered) > KNOWN_ANSWERED:
                        del self.answered[next(iter(self.answered))]
                changed = True
            elif p.late:
                if now - datetime.fromisoformat(p.fired_at) < LATE_FOR:
                    kept.append(p)
                else:
                    changed = True
            elif now >= datetime.fromisoformat(p.deadline_at):
                undelivered = bool(p.digest_id) and self.waiting(p.agent_id, p.digest_id)
                if undelivered and now - datetime.fromisoformat(p.fired_at) < WAIT_FOR_BRAIN:
                    # The brain never got it (down or restarting): Marcel gets its full time
                    # once it is back, rather than a "not written" line it could not help.
                    p.deadline_at = iso(now + DEADLINE)
                else:
                    self._fallback(p, unreachable=undelivered)
                    p.late = True
                kept.append(p)
                changed = True
            else:
                kept.append(p)
        if changed:
            self.pending = kept
            self.save()

    def _published(self, p: Pending) -> Artifact | None:
        """The digest Marcel published in answer to this event: a version whose `in_reply_to`
        is the event's `digest_id`; else a new version of the board, or a doc in the agent's
        inbox with the exact title the event asked for (it names the digest and its date, so
        another doc published meanwhile is never taken for it), unless that version answers
        another digest."""
        fired = datetime.fromisoformat(p.fired_at)
        board = self.boards.get(p.agent_id)
        with Session(self.engine) as s:
            if p.digest_id:
                answer = s.exec(
                    select(Artifact)
                    .join(ArtifactVersion, col(ArtifactVersion.artifact_id) == Artifact.id)
                    .join(Project, col(Project.id) == Artifact.project_id)
                    .where(ArtifactVersion.in_reply_to == p.digest_id)
                    .where(Project.agent_id == p.agent_id)
                    .order_by(col(ArtifactVersion.created_at).desc())
                ).first()
                if answer is not None:
                    return answer
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
            # A version that answers another digest is that digest, never this one.
            found = [
                art
                for art in found
                if (version := s.get(ArtifactVersion, (art.id, art.current_version))) is None
                or version.in_reply_to in (None, p.digest_id)
            ]
        for art in found:
            if art.id == board:
                return art
        for art in found:
            if p.title and art.title == p.title:
                return art
        return None

    def _announce(self, p: Pending, art: Artifact, push: bool = True) -> None:
        label = BY_DIGEST[p.digest].label
        log.info('The %s is published as %s (version %d).', label, art.id, art.current_version)
        with Session(self.engine) as s:
            version = s.get(ArtifactVersion, (art.id, art.current_version))
            summary = (version.summary if version else None) or art.title
        message_id, user_id = self._post(
            p.agent_id, role=MessageRole.agent, kind=MessageKind.artifact, body={},
            artifact_id=art.id, project_id=art.project_id,
        )  # fmt: skip
        if message_id is not None and push:
            self._push(message_id, label, summary, user_id)

    def _fallback(self, p: Pending, unreachable: bool = False) -> None:
        label = BY_DIGEST[p.digest].label
        minutes = int(DEADLINE.total_seconds() // 60)
        hours = int(WAIT_FOR_BRAIN.total_seconds() // 3600)
        why = (
            f'Marcel has not been reachable for {hours} hours; it gets the {label} when it is back'
            if unreachable
            else f'Marcel did not publish it within {minutes} minutes'
        )
        text = f'The {label} was not written: {why}. In short: {p.headline}'
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
            'made': self.made,
            'answered': self.answered,
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
            made = {
                str(k): {'cron': str(v['cron']), 'tz': str(v['tz'])}
                for k, v in data.get('made', {}).items()
            }
            answered = {str(k): str(v) for k, v in data.get('answered', {}).items()}
            for p in pending:
                datetime.fromisoformat(p.fired_at)
                datetime.fromisoformat(p.deadline_at)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            log.warning('Could not read %s (%s). Digests start afresh.', self.state_path, exc)
            return
        self.boards, self.pending, self.baselines, self.made = boards, pending, baselines, made
        self.answered = answered


def undelivered(outbox: ChannelOutbox) -> Callable[[str, str], bool]:
    """(agent id, digest id) → its `digest_due` is still in the agent's brain's outbox: the
    brain has not received it (down or restarting). The outbox keeps it until the brain is back."""

    def waiting(agent_id: str, digest_id: str) -> bool:
        try:
            target = conversation_target(outbox.main_conversation(agent_id))
        except LookupError:
            return False
        return any(
            f.get('kind') == 'digest_due' and (f.get('data') or {}).get('digest_id') == digest_id
            for f in outbox.pending(target)
        )

    return waiting


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
    gateway = getattr(app.state, 'channel', None)
    digests = Digests(
        app.state.engine,
        Facts(app.state.engine, usage),
        events=app.state.events,
        pusher=pusher,
        state_path=data_dir / STATE_FILE,
        tz=tz,
        waiting=undelivered(gateway.outbox) if gateway is not None else lambda _a, _d: False,
    )
    scheduler.brain_frame = digests.frame
    app.state.brain_ports.digests = digests  # which `in_reply_to` ids the brain may name
    app.state.digests = digests
    return digests
