"""Projects (B-30…B-33a): reading, creating, changing and archiving them, filing tasks under them,
and the project briefing a worker starts with.

Every agent has one `inbox` project, made with the agent (`models.inbox_id`). It cannot be
archived, and neither can a project with live tasks. A slug is unique among an agent's live
projects; the database enforces that too (`ix_project_live_slug`). Each project has a notes file,
`memory/projects/<slug>.md` in the memory folder (B-33), made with the project.

Events: `project.created`, `project.updated` (any change to a project or its counts, including a
task in it changing), `task.moved`, and `artifact.updated` for each item of a moved task (but not
an item the owner pinned to a project of its own: it stays where it was pinned).
"""

import logging
import re
import threading
import unicodedata
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select, update

from marcel_hub.artifacts.store import ArtifactStore
from marcel_hub.memory import (
    SLICE_MAX_BYTES,
    InvalidMemoryPath,
    MemoryConflict,
    MemoryRepo,
    MemoryUnavailable,
)
from marcel_hub.models import (
    INBOX_SLUG,
    Agent,
    Artifact,
    Project,
    Task,
    TaskState,
    inbox_id,
    utcnow,
)
from marcel_hub.tasks.ports import NullPublisher, Publisher
from marcel_hub.tasks.registry import TaskNotFound, TaskRegistry, iso
from marcel_hub.tasks.states import FINISHED

log = logging.getLogger(__name__)

SLUG_PATTERN = re.compile(r'^[a-z0-9][a-z0-9-]{0,39}$')  # the contract's
SLUG_MAX = 40
# States counted as `working` on a project: everything still in flight except `needs_you`.
IN_FLIGHT = frozenset(set(TaskState) - FINISHED - {TaskState.needs_you})
DONE_WINDOW = timedelta(days=7)
EDITABLE = ('name', 'goal', 'repo', 'folder', 'instructions', 'claude_project_url')


class ProjectNotFound(Exception):
    """No such project, or not the caller's (404 `project_not_found`)."""


class AgentNotFound(Exception):
    """No such agent, or not the caller's (404 `agent_not_found`)."""


class SlugTaken(Exception):
    """A live project of this agent has the slug (409 `slug_taken`)."""


class ProjectArchived(Exception):
    """Tasks cannot be filed under an archived project (409 `project_archived`)."""


class NotArchivable(Exception):
    """The inbox, or a project with live tasks (409 `project_is_inbox` / `project_busy`)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def notes_path(slug: str) -> str:
    """The project's notes file, relative to the memory folder."""
    return f'projects/{slug}.md'


def slugify(name: str) -> str:
    """`Energy contracts` → `energy-contracts`: ASCII letters and digits joined by dashes."""
    ascii_name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    slug = re.sub(r'[^a-z0-9]+', '-', ascii_name.lower()).strip('-')
    return slug[:SLUG_MAX].strip('-') or 'project'


def _first_notes(project: Project) -> str:
    lines = [f'# {project.name}', '']
    if project.goal:
        lines += [project.goal, '']
    lines += [
        'Notes every thread in this project starts with. Add what later threads need to know.',
        '',
    ]
    return '\n'.join(lines)


def _fenced(kind: str, slug: str, text: str) -> str:
    """`text` between `<<<kind slug=…>>>` and `<<<end kind>>>`. Any `<<<` inside it is broken
    up, so text written by a worker can never close the block early or open another one."""
    safe = text.replace('<<<', '<< <')
    return f'<<<{kind} slug={slug}>>>\n{safe}\n<<<end {kind}>>>'


def _capped(text: str, cap: int) -> tuple[str, bool]:
    data = text.encode('utf-8')
    if len(data) <= cap:
        return text, False
    return data[:cap].decode('utf-8', errors='ignore'), True


class ProjectStore:
    def __init__(
        self,
        engine: Engine,
        memory: MemoryRepo | None = None,
        publisher: Publisher | None = None,
        tasks: TaskRegistry | None = None,
    ) -> None:
        self.engine = engine
        self.memory = memory
        self.publisher: Publisher = publisher or NullPublisher()
        self.tasks = tasks
        # Announces a moved task's items (`ArtifactStore.announce_moved`); None: not announced.
        self.artifacts: ArtifactStore | None = None
        self._lock = threading.Lock()

    # Reading ---------------------------------------------------------------------------------

    def get(self, project_id: str, user_id: str | None = None) -> Project:
        """The project; with `user_id`, only if that user owns its agent."""
        with Session(self.engine) as s:
            project = s.get(Project, project_id)
            if project is None:
                raise ProjectNotFound(project_id)
            if user_id is not None:
                agent = s.get(Agent, project.agent_id)
                if agent is None or agent.owner_user_id != user_id:
                    raise ProjectNotFound(project_id)
            return project

    def inbox(self, agent_id: str) -> Project:
        with Session(self.engine) as s:
            project_id = inbox_id(s.connection(), agent_id)
            s.commit()
        return self.get(project_id)

    def list_projects(
        self, agent_id: str, user_id: str, archived: bool = False
    ) -> list[dict[str, Any]]:
        """The Map: most recent activity first, `inbox` last. Empty for someone else's agent."""
        with Session(self.engine) as s:
            agent = s.get(Agent, agent_id)
            if agent is None or agent.owner_user_id != user_id:
                return []
            rows = list(
                s.exec(
                    select(Project)
                    .where(Project.agent_id == agent_id)
                    .where(Project.archived == archived)
                )
            )
        bodies = self.to_api_many(rows)
        bodies.sort(key=lambda b: (b['slug'] != INBOX_SLUG, b['updated_at'], b['id']), reverse=True)
        return bodies

    def to_api(self, project: Project) -> dict[str, Any]:
        return self.to_api_many([project])[0]

    def to_api_many(self, projects: list[Project]) -> list[dict[str, Any]]:
        """The contract's `Project`, with `counts` and `updated_at` (the last change to the
        project or any task in it) worked out from its tasks."""
        ids = [p.id for p in projects]
        counts: dict[str, dict[str, int]] = {i: {'working': 0, 'needs_you': 0, 'done_7d': 0}
                                             for i in ids}  # fmt: skip
        latest: dict[str, datetime] = {}
        with Session(self.engine) as s:
            live = s.exec(
                select(Task.project_id, Task.state, func.count())
                .where(col(Task.project_id).in_(ids))
                .where(col(Task.state).in_([*IN_FLIGHT, TaskState.needs_you]))
                .group_by(col(Task.project_id), col(Task.state))
            ).all()
            for project_id, state, n in live:
                key = 'needs_you' if state == TaskState.needs_you else 'working'
                counts[str(project_id)][key] += n
            done = s.exec(
                select(Task.project_id, func.count())
                .where(col(Task.project_id).in_(ids))
                .where(Task.state == TaskState.done)
                .where(col(Task.updated_at) >= utcnow() - DONE_WINDOW)
                .group_by(col(Task.project_id))
            ).all()
            for project_id, n in done:
                counts[str(project_id)]['done_7d'] = n
            for project_id, at in s.exec(
                select(Task.project_id, func.max(Task.updated_at))
                .where(col(Task.project_id).in_(ids))
                .group_by(col(Task.project_id))
            ).all():
                if at is not None:
                    latest[str(project_id)] = at
        bodies = []
        for p in projects:
            body: dict[str, Any] = {'id': p.id, 'agent_id': p.agent_id, 'slug': p.slug,
                                    'name': p.name}  # fmt: skip
            for name in ('goal', 'repo', 'folder', 'instructions', 'claude_project_url'):
                if (value := getattr(p, name)) is not None:
                    body[name] = value
            updated = max(p.updated_at, latest.get(p.id, p.updated_at))
            body |= {
                'board_artifact_id': p.board_artifact_id,
                'archived': p.archived,
                'counts': counts[p.id],
                'created_at': iso(p.created_at),
                'updated_at': iso(updated),
            }
            bodies.append(body)
        return bodies

    # Changing --------------------------------------------------------------------------------

    def create(self, user_id: str, agent_id: str, name: str, slug: str | None = None,
               by: str = 'app', **fields: Any) -> Project:  # fmt: skip
        """Make a project and its notes file. Without a slug, one is made from the name (with
        `-2`, `-3` … if that one is taken); a slug asked for and taken raises `SlugTaken`."""
        with self._lock, Session(self.engine) as s:
            agent = s.get(Agent, agent_id)
            if agent is None or agent.owner_user_id != user_id:
                raise AgentNotFound(agent_id)
            inbox_id(s.connection(), agent_id)  # the inbox exists before any other project
            live = set(
                s.exec(
                    select(Project.slug)
                    .where(Project.agent_id == agent_id)
                    .where(Project.archived == False)  # noqa: E712
                )
            )
            # A slug asked for is checked by the live-slug index on commit (below).
            if slug is None:
                slug = base = slugify(name)
                n = 2
                while slug in live:
                    suffix = f'-{n}'
                    slug = base[: SLUG_MAX - len(suffix)].rstrip('-') + suffix
                    n += 1
            project = Project(agent_id=agent_id, slug=slug, name=name, **fields)
            s.add(project)
            try:
                s.commit()
            except IntegrityError as exc:  # the live-slug index (ix_project_live_slug)
                raise SlugTaken(slug) from exc
            s.refresh(project)
        self.ensure_notes(project, by)
        self.publisher.publish('project.created', self.to_api(project))
        return project

    def ensure_notes(self, project: Project, by: str = 'app') -> None:
        """Create `memory/projects/<slug>.md` if it is not there. An archived project's notes stay
        on disk, so a new project with its slug carries on from them. A git failure is logged:
        the project still works, and its threads start without notes."""
        if self.memory is None:
            return
        try:
            self.memory.write(notes_path(project.slug), _first_notes(project), None,
                              'agent' if by == 'agent' else 'app')  # fmt: skip
        except MemoryConflict:
            pass  # it exists already
        except (MemoryUnavailable, InvalidMemoryPath) as exc:
            log.warning('The notes file for project %s was not made: %s', project.slug, exc)

    def update(self, project_id: str, user_id: str, changes: dict[str, Any]) -> Project:
        project = self.get(project_id, user_id)
        with Session(self.engine) as s:
            for name, value in changes.items():
                if name in EDITABLE:
                    setattr(project, name, value)
            project.updated_at = utcnow()
            s.add(project)
            s.commit()
            s.refresh(project)
        self.announce(project.id)
        return project

    def archive(self, project_id: str, user_id: str) -> Project:
        project = self.get(project_id, user_id)
        if project.slug == INBOX_SLUG:
            raise NotArchivable(
                'project_is_inbox', 'The Inbox cannot be archived: unfiled threads land there.'
            )
        if project.archived:
            return project
        with Session(self.engine) as s:
            live = s.exec(
                select(func.count())
                .select_from(Task)
                .where(Task.project_id == project.id)
                .where(col(Task.state).not_in(FINISHED))
            ).one()
            if live:
                raise NotArchivable(
                    'project_busy',
                    f'“{project.name}” still has {live} live thread{"s" if live > 1 else ""}. '
                    'Stop them or move them first.',
                )
            project.archived = True
            project.updated_at = utcnow()
            s.add(project)
            s.commit()
            s.refresh(project)
        self.announce(project.id)
        return project

    def move(self, task_id: str, project_id: str, user_id: str) -> Task:
        """File a task under another project of the same agent (B-31). Its artifacts go with it,
        except those the owner pinned to a project of their own (`Artifact.pinned`).
        Sends `task.moved`, then `task.updated`, then `project.updated` for both projects, then
        `artifact.updated` for each of its unpinned items, now filed under the new project."""
        with Session(self.engine) as s:
            task = s.get(Task, task_id)
            agent = s.get(Agent, task.agent_id) if task else None
            if task is None or agent is None or agent.owner_user_id != user_id:
                raise TaskNotFound(task_id)
        target = self.get(project_id, user_id)
        if target.agent_id != task.agent_id:
            raise ProjectNotFound(project_id)
        if target.archived:
            raise ProjectArchived(
                f'“{target.name}” is archived, so nothing new can be filed there. '
                'Pick a live project.'
            )
        source = task.project_id
        if source == target.id:
            return task
        with Session(self.engine) as s:
            task.project_id = target.id
            task.updated_at = utcnow()
            s.add(task)
            s.exec(  # type: ignore[call-overload]
                update(Artifact)
                .where(col(Artifact.task_id) == task_id)
                .where(col(Artifact.pinned) == False)  # noqa: E712  a pinned item stays put
                .values(project_id=target.id)
            )
            s.commit()
            s.refresh(task)
        self.publisher.publish(
            'task.moved',
            {'task_id': task_id, 'from_project_id': source, 'to_project_id': target.id},
        )
        if self.tasks is not None:
            self.tasks._publish_task(task_id)  # task.updated; its project follows (ProjectEcho)
        else:
            self.announce(target.id)
        if source is not None:
            self.announce(source)
        if self.artifacts is not None:
            self.artifacts.announce_moved(task_id)
        return task

    def announce(self, project_id: str) -> None:
        """Send `project.updated` with the project as it is now."""
        try:
            project = self.get(project_id)
        except ProjectNotFound:
            return
        self.publisher.publish('project.updated', self.to_api(project))

    # The worker's briefing -------------------------------------------------------------------

    def briefing(self, task: Task) -> str:
        """What a worker reads before its prompt: the project, its standing instructions and its
        notes, each cut at `SLICE_MAX_BYTES`. Empty for an inbox with no notes."""
        project = self.get(task.project_id) if task.project_id else self.inbox(task.agent_id)
        rel = notes_path(project.slug)
        notes: tuple[str, bool] | None = None
        if self.memory is not None:
            try:
                notes = self.memory.read_slice(rel)
            except InvalidMemoryPath:
                notes = None
        has_notes = notes is not None and notes[0].strip() != ''
        if project.slug == INBOX_SLUG and not project.instructions and not has_notes:
            return ''
        parts = [f'# Project: {project.name}']
        if project.goal:
            parts.append(project.goal)
        if project.repo:
            parts.append(f'Repository: {project.repo}')
        if project.folder:
            parts.append(f'Folder on the NUC: {project.folder}')
        if project.instructions:
            text, cut = _capped(project.instructions, SLICE_MAX_BYTES)
            more = '\n\n(The instructions are cut here; they are too long.)' if cut else ''
            parts.append(
                '## Standing instructions\n\n'
                + _fenced('project-instructions', project.slug, text.strip() + more)
            )
        if notes is not None and has_notes:
            text, cut = notes
            more = f'\n\n(Cut here; the rest is in memory/{rel}.)' if cut else ''
            parts.append(
                f'## Project notes (memory/{rel})\n\n'
                + _fenced('project-notes', project.slug, text.strip() + more)
            )
        parts.append(
            f'Add what later threads in this project need to know to memory/{rel}.\n\n# The task\n'
        )
        return '\n\n'.join(parts) + '\n'


class ProjectEcho:
    """Wraps the task registry's publisher: after each `task.created` or `task.updated` it sends
    `project.updated` for the task's project, whose counts or `updated_at` just changed."""

    def __init__(self, inner: Publisher, store: ProjectStore) -> None:
        self.inner = inner
        self.store = store

    def publish(self, type: str, data: dict[str, Any]) -> None:
        self.inner.publish(type, data)
        if type in ('task.created', 'task.updated') and data.get('project_id'):
            self.store.announce(data['project_id'])
