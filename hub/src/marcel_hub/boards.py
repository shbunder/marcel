"""Project boards (B-35): one `board` artifact per project, kept current by the hub, no brain turn.

A board is a dashboard (`contracts/dashboard.schema.json`) with four sections and Marcel's notes:

- *Waiting on you*: open approvals in the project, and tasks that need you for anything else.
- *In flight*: running tasks, each with its state and the last step it reported.
- *Recent results*: tasks finished in the last 7 days, with their summary and items (newest 5).
- *Upcoming*: the project's schedules. Schedules do not exist yet, so it is always empty.
- Marcel's "what this means" lines (`BoardKeeper.board_note`, the brain's `board_note` tool), the
  newest `MAX_NOTES`.

The board artifact is made with its project (`models.make_board`). `BoardKeeper` watches the app
event log: any task, approval or artifact change in a project marks that project's board. A marked
board is rebuilt `DEBOUNCE_S` after the first mark, and never sooner than `DEBOUNCE_S` after its
last rebuild, so a burst of changes makes one rebuild. A rebuild stores a new version only when
the content changed. The keeper ignores events about boards, so a rebuild never marks itself.
"""

import asyncio
import contextlib
import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI
from sqlalchemy import desc
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from marcel_hub.artifacts import HUB_AUTHOR, ArtifactNotFound, ArtifactStore, Content
from marcel_hub.artifacts.store import URL_KINDS, check_dashboard
from marcel_hub.models import (
    Approval,
    ApprovalState,
    Artifact,
    ArtifactKind,
    Project,
    Task,
    TaskEvent,
    TaskEventType,
    TaskState,
    board_title,
    make_board,
    utcnow,
)
from marcel_hub.projects.store import IN_FLIGHT

log = logging.getLogger(__name__)

DEBOUNCE_S = 10.0
TICK_S = 1.0
RECENT = timedelta(days=7)
MAX_RESULTS = 5
MAX_ITEMS = 20  # per list in a section
MAX_NOTES = 5
NOTE_MAX = 400  # dashboard.schema.json Note.text maxLength
SUMMARY = 'Rebuilt after a change in the project.'

STATE_WORDS = {
    TaskState.queued: 'Waiting for a free slot',
    TaskState.starting: 'Starting',
    TaskState.working: 'Working',
    TaskState.silent: 'Gone quiet',
    TaskState.unknown: 'State unknown',
}
KIND_WORDS = {
    ArtifactKind.pr: 'Pull request',
    ArtifactKind.branch: 'Branch',
    ArtifactKind.doc: 'Document',
    ArtifactKind.file: 'File',
    ArtifactKind.dashboard: 'Dashboard',
    ArtifactKind.link: 'Link',
}


def _when(value: datetime) -> str:
    return value.strftime('%d %b %H:%M UTC')


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + '…'


def _section(title: str, empty: str, widgets: list[dict[str, Any]]) -> dict[str, Any]:
    return {'type': 'section', 'title': title, 'empty_text': empty, 'widgets': widgets}


def _list(items: list[dict[str, Any]], title: str | None = None) -> list[dict[str, Any]]:
    if not items and title is None:
        return []
    widget: dict[str, Any] = {'type': 'list', 'items': items[:MAX_ITEMS]}
    if title is not None:
        widget['title'] = title
    return [widget]


def board_content(s: Session, project: Project, notes: list[dict[str, Any]]) -> dict[str, Any]:
    """The board of `project` as it stands, as a dashboard document."""
    tasks = list(s.exec(select(Task).where(Task.project_id == project.id)
                        .order_by(col(Task.created_at), col(Task.id))))  # fmt: skip
    by_id = {t.id: t for t in tasks}

    # Waiting on you: open approvals, then tasks that need you without one.
    waiting: list[dict[str, Any]] = []
    approvals = s.exec(
        select(Approval)
        .where(col(Approval.task_id).in_(list(by_id)))
        .where(Approval.state == ApprovalState.open)
        .order_by(col(Approval.created_at), col(Approval.id))
    ).all()
    for a in approvals:
        since = _when(a.created_at)
        waiting.append({'text': a.summary, 'detail': f'{by_id[a.task_id].title} · since {since}'})
    asked = {a.task_id for a in approvals}
    for t in tasks:
        if t.state == TaskState.needs_you and t.id not in asked:
            waiting.append({'text': t.waiting_for or 'Needs your answer',
                            'detail': f'{t.title} · since {_when(t.updated_at)}'})  # fmt: skip

    # In flight, with the last step each one reported.
    flying: list[dict[str, Any]] = []
    for t in tasks:
        if t.state not in IN_FLIGHT:
            continue
        step = s.exec(
            select(TaskEvent.data_json)
            .where(TaskEvent.task_id == t.id)
            .where(TaskEvent.type == TaskEventType.progress)
            .order_by(desc(col(TaskEvent.seq)))
            .limit(1)
        ).first()
        detail = STATE_WORDS.get(t.state, t.state.value)
        if step and step.get('text'):
            detail += f' · {step["text"]}'
        flying.append({'text': t.title, 'detail': detail})

    # Recent results, each with its items.
    cutoff = utcnow() - RECENT
    done = sorted(
        (t for t in tasks if t.state == TaskState.done and _aware(t.updated_at) >= cutoff),
        key=lambda t: (t.updated_at, t.id),
        reverse=True,
    )[:MAX_RESULTS]
    results: list[dict[str, Any]] = []
    for t in done:
        items: list[dict[str, Any]] = []
        if t.summary:
            items.append({'text': t.summary})
        for art in s.exec(select(Artifact).where(Artifact.task_id == t.id)
                          .order_by(col(Artifact.created_at), col(Artifact.id))):  # fmt: skip
            item: dict[str, Any] = {'text': f'{art.title} · v{art.current_version}',
                                    'detail': KIND_WORDS.get(art.kind, art.kind.value)}  # fmt: skip
            if art.kind in URL_KINDS and art.url:
                item['url'] = art.url
            items.append(item)
        results += _list(items, title=t.title)

    widgets: list[dict[str, Any]] = [
        _section('Waiting on you', 'Nothing waiting on you.', _list(waiting)),
        _section('In flight', 'Nothing in flight.', _list(flying)),
        _section('Recent results', 'No results in the last 7 days.', results),
        _section('Upcoming', 'Nothing scheduled.', []),
    ]
    widgets += [{'type': 'note', **n} for n in notes[-MAX_NOTES:]]
    doc: dict[str, Any] = {'version': 1, 'title': _cut(project.name, 80), 'widgets': widgets}
    if project.goal:
        doc['subtitle'] = _cut(project.goal, 160)
    return doc


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=utcnow().tzinfo)


def encode(doc: dict[str, Any]) -> bytes:
    """One byte form per board, so an unchanged board compares equal."""
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


class BoardKeeper:
    """Rebuilds project boards, debounced per project. `clock` is seconds (monotonic)."""

    def __init__(
        self,
        engine: Engine,
        artifacts: ArtifactStore,
        clock: Callable[[], float] = time.monotonic,
        debounce: float = DEBOUNCE_S,
    ) -> None:
        self.engine = engine
        self.artifacts = artifacts
        self.clock = clock
        self.debounce = debounce
        # Two locks, never held together by `mark`: an event observer marks while the event log
        # holds its own lock, and a rebuild publishes through that log.
        self._marks = threading.Lock()
        self._building = threading.RLock()
        self._due: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._task: asyncio.Task[None] | None = None
        artifacts.build_board = self.build

    # Marking ---------------------------------------------------------------------------------

    def mark(self, project_id: str, at_once: bool = False) -> None:
        """Rebuild this project's board soon: `debounce` seconds after this first mark, and no
        sooner than `debounce` after the last rebuild. `at_once` skips the wait for a board that
        was never built."""
        with self._marks:
            now = self.clock()
            last = self._last.get(project_id)
            earliest = now if last is None else last + self.debounce
            due = max(earliest, now if at_once else now + self.debounce)
            if project_id not in self._due or due < self._due[project_id]:
                self._due[project_id] = due

    def mark_all(self) -> None:
        """At start: every live project's board, at once (boards made by the migration have no
        content yet, and anything may have changed while the hub was down)."""
        with Session(self.engine) as s:
            ids = s.exec(select(Project.id).where(Project.archived == False)).all()  # noqa: E712
        for project_id in ids:
            self.mark(project_id, at_once=True)

    def due(self) -> dict[str, float]:
        with self._marks:
            return dict(self._due)

    def observe(self, _user_id: str, type_: str, data: dict[str, Any]) -> None:
        """`EventLog` observer: mark the boards a change touches. Events about boards are
        ignored, so a rebuild never marks its own board again."""
        try:
            for project_id, at_once in self._touched(type_, data):
                self.mark(project_id, at_once)
        except Exception:  # an event must never fail because of a board
            log.exception('Could not follow a %s event for the boards.', type_)

    def _touched(self, type_: str, data: dict[str, Any]) -> list[tuple[str, bool]]:
        if type_ in ('task.created', 'task.updated'):
            return [(data['project_id'], False)] if data.get('project_id') else []
        if type_ == 'task.moved':
            return [(data['from_project_id'], False), (data['to_project_id'], False)]
        if type_ in ('approval.created', 'approval.resolved'):
            with Session(self.engine) as s:
                task = s.get(Task, data['task_id'])
            return [(task.project_id, False)] if task and task.project_id else []
        if type_ in ('artifact.created', 'artifact.updated'):
            art = data['artifact'] if type_ == 'artifact.updated' else data
            if art.get('kind') == ArtifactKind.board.value or not art.get('project_id'):
                return []
            return [(art['project_id'], False)]
        if type_ == 'project.created':
            return [(data['id'], True)]
        if type_ == 'project.updated':
            return [(data['id'], False)]
        return []

    # Building --------------------------------------------------------------------------------

    def flush(self) -> list[str]:
        """Rebuild every board that is due. Returns their project ids."""
        with self._marks:
            now = self.clock()
            ready = sorted(p for p, due in self._due.items() if due <= now)
            for project_id in ready:
                del self._due[project_id]
                self._last[project_id] = now
        for project_id in ready:
            try:
                self.build(project_id)
            except Exception:  # one broken board must not stop the others
                log.exception('Could not rebuild the board of project %s.', project_id)
        return ready

    def build(self, project_id: str) -> bool:
        """Rebuild the board now. Returns True when it stored new content."""
        with self._building:
            with Session(self.engine) as s:
                project = s.get(Project, project_id)
                if project is None:
                    return False
                try:
                    art = self.artifacts.get(project.board_artifact_id)
                except ArtifactNotFound:
                    make_board(s.connection(), project.id, project.board_artifact_id, project.name)
                    s.commit()
                    art = self.artifacts.get(project.board_artifact_id)
                data = encode(board_content(s, project, list(art.meta_json.get('notes', []))))
                title = board_title(project.name)
            check_dashboard(data)  # a board the app cannot draw is a bug: fail loudly
            stored = self.artifacts.stored_bytes(art)
            if stored is None and art.current_version == 1:
                self.artifacts.fill_first(art.id, data, title)
                return True
            if stored == data and art.title == title:
                return False
            self.artifacts.add_version(
                art.id, Content(data=data, content_type='application/json'), title=title,
                author=HUB_AUTHOR, summary=SUMMARY,
            )  # fmt: skip
            return True

    # Marcel's notes --------------------------------------------------------------------------

    def board_note(self, project_id: str, text: str, author: str = 'Marcel') -> None:
        """Add a "what this means" line to the project's board (B-35). The brain's `board_note`
        tool (S-06.6) calls this; the board shows the newest `MAX_NOTES`. Raises `ValueError`
        for an empty note and `ArtifactNotFound` for an unknown project."""
        text = text.strip()
        if not text:
            raise ValueError('A board note needs some text.')
        with Session(self.engine) as s:
            project = s.get(Project, project_id)
        if project is None:
            raise ArtifactNotFound(project_id)
        with self._building:
            art = self.artifacts.get(project.board_artifact_id)
            note = {'text': _cut(text, NOTE_MAX), 'author': author,
                    'at': utcnow().isoformat().replace('+00:00', 'Z')}  # fmt: skip
            notes = [*art.meta_json.get('notes', []), note][-MAX_NOTES:]
            self.artifacts.set_meta(art.id, notes=notes)
        self.mark(project_id)

    # Running ---------------------------------------------------------------------------------

    def start(self, tick: float | None = None) -> None:
        """Rebuild due boards every `tick` seconds (`TICK_S`), on the running event loop."""
        self._task = asyncio.get_running_loop().create_task(self._run(tick or TICK_S))

    async def _run(self, tick: float) -> None:
        while True:
            await asyncio.sleep(tick)
            self.flush()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


def install(app: FastAPI) -> BoardKeeper:
    """Put the keeper on `app.state.boards` and let it watch the app event log. Call after
    `artifacts.install`; the app's lifespan starts it (`mark_all`, then `start`)."""
    keeper = BoardKeeper(app.state.engine, app.state.artifacts)
    app.state.events.observers.append(keeper.observe)
    app.state.boards = keeper
    return keeper
