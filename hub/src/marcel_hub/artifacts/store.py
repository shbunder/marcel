"""Artifacts (B-21, B-34, B-36): the things tasks produce, kept by the hub and listed in the app's
Items tab, each with its versions.

A worker hands an artifact in with `report` (channel.schema.json `Artifact`); the channel gateway
passes it to `ArtifactStore.publish_reported`. Without `artifact_id` that makes a new artifact at
version 1 (`artifact.created`, and the item-get `artifact` message in the main chat when a task
made it). With `artifact_id` it adds the next version (`artifact.updated`, no second message); an
id the hub does not have, one from another project, or a board is refused with `UnknownArtifact`.
Each version's bytes are kept in their own file (`blob_name`), so older versions stay readable.

A project's board (kind `board`) is made with the project and rebuilt by the hub (`boards.py`).

What each kind needs:

- `pr`, `branch`, `link`: an `http(s)` URL. A `pr` URL must name a pull request (`…/pull/<n>`).
- `doc` (Markdown or HTML), `file` (anything) and `dashboard` (JSON): an absolute path on the NUC.
  The hub copies the bytes into `<data_dir>/artifacts/<artifact id>`, and only from inside the
  workers' folder (`files_root`); a path or symlink that leads anywhere else is refused.
- `dashboard`: must match `contracts/dashboard.schema.json`, and every chart series must have one
  value per label. Anything else is refused before it is stored.

A refusal raises `ArtifactRefused` with a message for the worker that says what to fix.

`trusted` is true only for HTML the hub renders itself (`publish_hub_html`); anything a worker
hands in is stored with `trusted: false`. The hub never renders or runs artifact content: it
serves the bytes with their own content type, `nosniff`, and a CSP `sandbox` header.

A PR's CI and review status is refreshed from a `PrStatusSource`; `pr_status_from_gh` turns
GitHub's REST answers into the contract's `pr` fields. Per ADR-002 the hub reads GitHub itself with
a read-only token: `github.py` (S-04.12) installs that client and the loop that refreshes open PRs.
Without a token the store keeps `NoPrStatus`, which says plainly that the token is missing.
When a refresh fails, `github.py` keeps the reason as `meta_json['pr_note']`; the card shows it as
`pr.note` (null once a refresh works again).

`move` pins an item to another project of the same agent (`POST /api/artifacts/{id}/move`): its
`project_id` changes and it is marked `pinned`, so a later move of its task leaves it there. A
board stays with its project (`ArtifactIsBoard`), nothing goes into an archived project
(`MoveTargetArchived`), and a move into the project it is in changes nothing.
"""

import json
import logging
import mimetypes
import os
import re
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import desc
from sqlmodel import Session, col, select

from marcel_hub.conversations import agent_of_artifact, ensure_main_conversation, post_message
from marcel_hub.models import (
    BOARD_AUTHOR,
    Agent,
    Artifact,
    ArtifactKind,
    ArtifactVersion,
    MessageKind,
    MessageRole,
    Project,
    Task,
    TaskEventType,
    inbox_id,
    utcnow,
)
from marcel_hub.tasks.ports import NullPublisher, Publisher
from marcel_hub.tasks.registry import TaskRegistry, iso
from marcel_hub.ws import EventLog

log = logging.getLogger(__name__)

CONTRACTS_DIR = Path(__file__).resolve().parents[4] / 'contracts'
HUB_AUTHOR = BOARD_AUTHOR  # "the hub"
MAX_BLOB_BYTES = 25 * 1024 * 1024

PR_URL = re.compile(r'/pull/(\d+)/?$')
# Marcel's own repository (B-24): a PR there is a self-change, whoever hands it in.
SELF_REPO = 'shbunder/marcel'
URL_KINDS = {ArtifactKind.pr, ArtifactKind.branch, ArtifactKind.link}
FILE_KINDS = {ArtifactKind.doc, ArtifactKind.file, ArtifactKind.dashboard, ArtifactKind.board}
DOC_TYPES = {'.md': 'text/markdown', '.markdown': 'text/markdown', '.html': 'text/html',
             '.htm': 'text/html'}  # fmt: skip

PR_STATES = {'open', 'draft', 'merged', 'closed'}
PR_CHECKS = {'pending', 'passing', 'failing', 'none'}
PR_REVIEWS = {'none', 'requested', 'approved', 'changes_requested'}
PR_NOTE_MAX = 200  # app-api.yaml `Artifact.pr.note` maxLength

# Only the standard table, never /etc/mime.types: the same file gets the same type everywhere.
_MIME = mimetypes.MimeTypes()


class ArtifactRefused(Exception):
    """The artifact was not stored. The message tells the worker what to fix."""


class UnknownArtifact(ArtifactRefused):
    """A new version was asked for an artifact the worker may not update: the hub does not have
    it, it is in another project, or it is a board. The channel answers with an error ack."""


class ArtifactNotFound(Exception):
    """No artifact with that id (404 `artifact_not_found`)."""


class VersionNotFound(Exception):
    """The artifact has no such version (404 `not_found`)."""


class NoContent(Exception):
    """The artifact lives at a URL, not in the hub (404). The message says so."""


class ArtifactIsBoard(Exception):
    """A project's board stays with its project (409 `artifact_is_board`)."""


class MoveTargetNotFound(Exception):
    """No such project, or not one of the item's agent (404 `project_not_found`)."""


class MoveTargetArchived(Exception):
    """Nothing new is filed in an archived project (409 `project_archived`)."""


class PrStatusUnavailable(Exception):
    """The PR status could not be read. The message says why, in plain words."""


# Self-changes ------------------------------------------------------------------------------


def is_self_change_url(url: str | None) -> bool:
    """True for exactly `https://github.com/shbunder/marcel/pull/<n>` (B-24). The hub decides
    this from the URL; a worker can neither claim nor avoid it."""
    if not url:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != 'https' or parts.netloc.lower() != 'github.com':
        return False
    if parts.query or parts.fragment:
        return False
    owner, _, rest = parts.path.strip('/').partition('/')
    repo, _, tail = rest.partition('/')
    return (
        f'{owner}/{repo}'.lower() == SELF_REPO
        and re.fullmatch(r'pull/[1-9][0-9]{0,9}', tail) is not None
    )


# PR status ---------------------------------------------------------------------------------


class PrStatusSource(Protocol):
    async def fetch(self, url: str) -> dict[str, Any]:
        """The PR's `state`, and `checks` and `review` when known (the contract's `pr` values).
        Raises `PrStatusUnavailable`."""
        ...


class NoPrStatus:
    async def fetch(self, url: str) -> dict[str, Any]:
        raise PrStatusUnavailable(
            'The hub has no GitHub token (MARCEL_GITHUB_TOKEN is not set), so it cannot check '
            "this PR's checks and reviews."
        )


FAILED_CHECKS = {'failure', 'timed_out', 'cancelled', 'action_required', 'startup_failure'}


def pr_status_from_gh(
    pull: dict[str, Any], check_runs: dict[str, Any], reviews: list[dict[str, Any]]
) -> dict[str, str]:
    """Map `gh api repos/{o}/{r}/pulls/{n}`, `…/commits/{sha}/check-runs` and `…/pulls/{n}/reviews`
    to the contract's `pr.state`, `pr.checks` and `pr.review`."""
    if pull.get('merged_at'):
        state = 'merged'
    elif pull.get('state') == 'closed':
        state = 'closed'
    else:
        state = 'draft' if pull.get('draft') else 'open'

    runs = check_runs.get('check_runs') or []
    if any(r.get('conclusion') in FAILED_CHECKS for r in runs):
        checks = 'failing'
    elif any(r.get('status') != 'completed' for r in runs):
        checks = 'pending'
    else:
        checks = 'passing' if runs else 'none'

    latest: dict[str, str] = {}  # a comment does not change where a reviewer stands
    for r in reviews:
        if r.get('state') in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'):
            latest[(r.get('user') or {}).get('login', '')] = r['state']
    if 'CHANGES_REQUESTED' in latest.values():
        review = 'changes_requested'
    elif 'APPROVED' in latest.values():
        review = 'approved'
    else:
        review = 'requested' if pull.get('requested_reviewers') else 'none'
    return {'state': state, 'checks': checks, 'review': review}


# Dashboards --------------------------------------------------------------------------------


@cache
def _dashboard_validator() -> Draft202012Validator:
    schema = json.loads((CONTRACTS_DIR / 'dashboard.schema.json').read_text())
    # No format checker: its date and URI checks need optional packages, so they would run in
    # tests and be skipped in production. The app draws these strings; it never runs them.
    return Draft202012Validator(schema)


def _charts(widgets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for w in widgets:
        if w.get('type') == 'chart':
            found.append(w)
        elif w.get('type') == 'section':
            found.extend(_charts(w.get('widgets', [])))
    return found


def check_dashboard(data: bytes) -> None:
    """Refuse a dashboard that breaks the schema, or a chart without one value per label."""
    try:
        doc = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactRefused(
            f'The dashboard was not saved: it is not valid JSON ({exc}).'
        ) from exc
    error = best_match(_dashboard_validator().iter_errors(doc))
    if error is not None:
        where = ' → '.join(str(p) for p in error.absolute_path) or 'the top level'
        raise ArtifactRefused(
            f'The dashboard was not saved: at {where}, {error.message}. '
            'Fix it to match dashboard.schema.json and hand it in again.'
        )
    for i, chart in enumerate(_charts(doc['widgets']), start=1):
        name = chart.get('title') or f'number {i}'
        for series in chart['series']:
            if len(series['values']) != len(chart['labels']):
                raise ArtifactRefused(
                    f'The dashboard was not saved: chart “{name}” has {len(chart["labels"])} '
                    f'labels but series “{series["name"]}” has {len(series["values"])} values. '
                    'Give every series exactly one value per label.'
                )


def _open_at(
    name: str, dir_fd: int, flags: int, outside: ArtifactRefused, missing: ArtifactRefused
) -> int:
    """Open `name` inside the folder `dir_fd`, never through a symlink."""
    try:
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | flags, dir_fd=dir_fd)
    except FileNotFoundError as exc:
        raise missing from exc
    except OSError as exc:  # ELOOP or ENOTDIR: a symlink, or a file where a folder should be
        try:
            is_link = stat.S_ISLNK(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode)
        except OSError:
            is_link = False
        raise (outside if is_link else missing) from exc


# The store ---------------------------------------------------------------------------------


class ReportedArtifact(BaseModel):
    """channel.schema.json `Artifact`: what a worker's `report` hands in."""

    model_config = ConfigDict(extra='forbid')
    kind: Literal['pr', 'branch', 'doc', 'file', 'dashboard', 'link']
    title: str = Field(min_length=1, max_length=300)
    url: str | None = None
    path: str | None = None
    artifact_id: str | None = None
    summary: str | None = Field(default=None, max_length=200)


@dataclass(frozen=True)
class Content:
    """What one version of an artifact holds: its bytes (file kinds) or its URL (link kinds)."""

    data: bytes | None = None
    url: str | None = None
    content_type: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


def blob_name(artifact_id: str, version: int) -> str:
    """The file a version's bytes are kept in. Version 1 keeps the name it had before versions."""
    return artifact_id if version == 1 else f'{artifact_id}.v{version}'


class ArtifactStore:
    def __init__(
        self,
        engine: Any,
        blob_dir: Path,
        tasks: TaskRegistry | None = None,
        publisher: Publisher | None = None,
        files_root: Path | None = None,
        pr_source: PrStatusSource | None = None,
        events: EventLog | None = None,
    ) -> None:
        self.engine = engine
        self.blob_dir = blob_dir
        self.tasks = tasks
        self.publisher = publisher
        self.files_root = files_root  # the workers' folder as the hub sees it; None: no copying
        self.pr_source: PrStatusSource = pr_source or NoPrStatus()
        self.events = events  # for the item-get message (B-36); None: no message
        # Writes a board's content now, by project id: set by the board keeper, so a board read
        # before its first build is built on the spot (`boards.BoardKeeper`).
        self.build_board: Callable[[str], object] | None = None
        self._lock = threading.Lock()  # one new version of an artifact at a time

    # Reading -------------------------------------------------------------------------------

    def get(self, artifact_id: str) -> Artifact:
        with Session(self.engine) as s:
            art = s.get(Artifact, artifact_id)
        if art is None:
            raise ArtifactNotFound(artifact_id)
        return art

    def owner_of(self, artifact_id: str) -> str | None:
        """The person whose agent the artifact belongs to (through its task or its project), or
        None when there is no such artifact or it is filed nowhere."""
        with Session(self.engine) as s:
            row = s.get(Artifact, artifact_id)
            agent_id = agent_of_artifact(s, row) if row is not None else None
            agent = s.get(Agent, agent_id) if agent_id is not None else None
            return agent.owner_user_id if agent is not None else None

    def page(
        self,
        task_id: str | None = None,
        kind: ArtifactKind | None = None,
        offset: int = 0,
        limit: int = 50,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> tuple[list[Artifact], int | None]:
        """Newest change first. Boards are listed only when asked for (`kind=board`): a project
        shows its own board, the Items tab shows what tasks made. With `user_id`, only that
        person's items (those filed in a project of one of their agents). Returns the page and
        the offset of the next one (None at the end)."""
        query = select(Artifact)
        if user_id is not None:
            query = (
                query.join(Project, col(Project.id) == col(Artifact.project_id))
                .join(Agent, col(Agent.id) == col(Project.agent_id))
                .where(Agent.owner_user_id == user_id)
            )
        if task_id is not None:
            query = query.where(Artifact.task_id == task_id)
        if project_id is not None:
            query = query.where(Artifact.project_id == project_id)
        if kind is not None:
            query = query.where(Artifact.kind == kind)
        else:
            query = query.where(Artifact.kind != ArtifactKind.board)
        query = query.order_by(desc(col(Artifact.updated_at)), desc(col(Artifact.id)))
        with Session(self.engine) as s:
            rows = list(s.exec(query.offset(offset).limit(limit + 1)))
        more = len(rows) > limit
        return rows[:limit], (offset + limit if more else None)

    def versions(self, artifact_id: str) -> list[ArtifactVersion]:
        """Every version, newest first. Raises `ArtifactNotFound`."""
        self.get(artifact_id)
        with Session(self.engine) as s:
            return list(
                s.exec(
                    select(ArtifactVersion)
                    .where(ArtifactVersion.artifact_id == artifact_id)
                    .order_by(desc(col(ArtifactVersion.version)))
                )
            )

    def version(self, artifact_id: str, version: int) -> ArtifactVersion:
        with Session(self.engine) as s:
            row = s.get(ArtifactVersion, (artifact_id, version))
        if row is None:
            raise VersionNotFound(artifact_id, version)
        return row

    def content(self, artifact_id: str, version: int | None = None) -> tuple[bytes, str, bool]:
        """The bytes, content type and `trusted` of an artifact the hub holds."""
        path, art, content_type = self.content_path(artifact_id, version)
        return path.read_bytes(), content_type, art.trusted

    def content_path(
        self, artifact_id: str, version: int | None = None
    ) -> tuple[Path, Artifact, str]:
        """The file holding a version's bytes (the current one by default), the artifact, and
        that version's content type. Raises `ArtifactNotFound`, `VersionNotFound`, `NoContent`."""
        art = self.get(artifact_id)
        if art.kind not in FILE_KINDS:
            raise NoContent(f'“{art.title}” is a link, so the hub has no copy. Open its link.')
        number = art.current_version if version is None else version
        content_type = art.content_type
        if number != art.current_version:
            content_type = self.version(artifact_id, number).content_type
        path = self.blob_dir / blob_name(art.id, number)
        if not path.is_file() and art.kind is ArtifactKind.board and self.build_board is not None:
            assert art.project_id is not None
            self.build_board(art.project_id)  # a board read before the hub first built it
        if not path.is_file():
            raise ArtifactNotFound(artifact_id)
        return path, art, content_type or 'application/octet-stream'

    def stored_bytes(self, art: Artifact) -> bytes | None:
        """The current version's bytes, or None when the hub has none yet."""
        path = self.blob_dir / blob_name(art.id, art.current_version)
        return path.read_bytes() if path.is_file() else None

    @staticmethod
    def to_api(art: Artifact) -> dict[str, Any]:
        """The contract's `Artifact`: the current version's fields."""
        body: dict[str, Any] = {
            'id': art.id,
            'kind': art.kind.value,
            'title': art.title,
            'current_version': art.current_version,
            'trusted': art.trusted,
            'self_change': art.self_change,
            'created_at': iso(art.created_at),
            'updated_at': iso(art.updated_at),
        }
        for name in ('task_id', 'project_id', 'updated_by', 'url', 'content_type', 'size_bytes'):
            if (value := getattr(art, name)) is not None:
                body[name] = value
        if 'pr' in art.meta_json:
            note = art.meta_json.get('pr_note')
            body['pr'] = {**art.meta_json['pr'], 'note': note[:PR_NOTE_MAX] if note else None}
        return body

    @staticmethod
    def version_to_api(row: ArtifactVersion) -> dict[str, Any]:
        """The contract's `ArtifactVersion`."""
        body: dict[str, Any] = {'artifact_id': row.artifact_id, 'version': row.version}
        for name in ('summary', 'author', 'task_id', 'url', 'content_type', 'size_bytes'):
            if (value := getattr(row, name)) is not None:
                body[name] = value
        body['created_at'] = iso(row.created_at)
        return body

    # Storing -------------------------------------------------------------------------------

    def version_target(
        self, artifact_id: str, task_id: str | None, project_id: str | None = None
    ) -> Artifact:
        """The artifact a worker (or Marcel) may hand a new version of. The caller's project is
        its task's, or `project_id` when no task hands it in (Marcel's own items). Raises
        `UnknownArtifact` for an id the hub does not have, one from another project, or a board
        (the hub keeps boards itself)."""
        try:
            art = self.get(artifact_id)
        except ArtifactNotFound:
            art = None
        if art is None:
            raise UnknownArtifact(
                f'The artifact was not saved: there is no artifact {artifact_id}. Hand it in '
                'without artifact_id to make a new one.'
            )
        if art.kind is ArtifactKind.board:
            raise UnknownArtifact(
                f'The artifact was not saved: {artifact_id} is a project board, which the hub '
                'keeps up to date itself.'
            )
        if task_id is not None:
            project_id = None
            with Session(self.engine) as s:
                task = s.get(Task, task_id)
                project_id = task.project_id if task else None
        if art.project_id != project_id:
            raise UnknownArtifact(
                f'The artifact was not saved: {artifact_id} belongs to another project than this '
                'task. Hand it in without artifact_id to make a new one.'
            )
        return art

    def publish_reported(
        self,
        spec: dict[str, Any],
        task_id: str | None = None,
        project_id: str | None = None,
        author: str | None = None,
    ) -> Artifact:
        """Store what a worker handed in with `report`: a new artifact, or with `artifact_id` a
        new version of one in the task's project. Marcel's own items have no task: they are
        filed under `project_id`, by `author` ("Marcel"). Raises `ArtifactRefused`
        (`UnknownArtifact` for an id it may not update)."""
        try:
            reported = ReportedArtifact.model_validate(spec)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = '.'.join(str(p) for p in first['loc']) or 'the artifact'
            raise ArtifactRefused(f'The artifact was not saved: {where}: {first["msg"]}.') from exc
        kind = ArtifactKind(reported.kind)
        target = None
        if reported.artifact_id is not None:
            target = self.version_target(reported.artifact_id, task_id, project_id)
            if target.kind is not kind:
                raise ArtifactRefused(
                    f'The artifact was not saved: “{target.title}” is a {target.kind.value}, so '
                    f'a new version of it must be a {target.kind.value} too, not a {kind.value}.'
                )
        content = self._read_reported(kind, reported)
        if target is not None:
            return self.add_version(
                target.id, content, title=reported.title, task_id=task_id,
                summary=reported.summary, author=author,
            )  # fmt: skip
        art = Artifact(kind=kind, title=reported.title, task_id=task_id, url=content.url,
                       trusted=False, content_type=content.content_type, meta_json=content.meta,
                       size_bytes=None if content.data is None else len(content.data),
                       project_id=project_id if task_id is None else None,
                       updated_by=author,
                       self_change=kind is ArtifactKind.pr
                       and is_self_change_url(content.url))  # fmt: skip
        return self._store(art, content.data, reported.summary)

    def publish_hub_html(
        self, title: str, html: str, task_id: str | None = None, project_id: str | None = None
    ) -> Artifact:
        """HTML the hub rendered itself: the only artifact that is `trusted`. Filed with its task,
        else in `project_id`, else in the owner's inbox (`models._file_artifact`, B-38)."""
        data = html.encode('utf-8')
        art = Artifact(kind=ArtifactKind.doc, title=title, task_id=task_id, trusted=True,
                       project_id=project_id if task_id is None else None,
                       content_type='text/html', size_bytes=len(data),
                       updated_by=HUB_AUTHOR)  # fmt: skip
        return self._store(art, data)

    def _read_reported(self, kind: ArtifactKind, reported: ReportedArtifact) -> Content:
        if kind in URL_KINDS:
            return self._check_url(kind, reported)
        if reported.path is None:
            raise ArtifactRefused(f'The artifact was not saved: a {kind.value} needs a path.')
        data, name = self._read_worker_file(reported.path)
        if kind is ArtifactKind.doc:
            content_type = DOC_TYPES.get(Path(name).suffix.lower())
            if content_type is None:
                raise ArtifactRefused(
                    f'The doc was not saved: {name} is not Markdown or HTML. Hand it in as a file.'
                )
        elif kind is ArtifactKind.dashboard:
            check_dashboard(data)
            content_type = 'application/json'
        else:
            content_type = _MIME.guess_type(name)[0] or 'application/octet-stream'
        return Content(data=data, content_type=content_type)

    def _check_url(self, kind: ArtifactKind, reported: ReportedArtifact) -> Content:
        url = reported.url
        if url is None:
            raise ArtifactRefused(f'The artifact was not saved: a {kind.value} needs a url.')
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.netloc:
            raise ArtifactRefused(
                f'The artifact was not saved: {url} is not a web address. Use http or https.'
            )
        meta: dict[str, Any] = {}
        if kind is ArtifactKind.pr:
            match = PR_URL.search(parts.path)
            if match is None:
                raise ArtifactRefused(
                    f'The artifact was not saved: {url} is not a pull request. A PR url ends in '
                    '/pull/<number>; hand in a branch or link otherwise.'
                )
            meta['pr'] = {'number': int(match.group(1)), 'state': 'open'}
        return Content(url=url, meta=meta)

    def _read_worker_file(self, raw: str) -> tuple[bytes, str]:
        """The bytes of a worker's file, only from inside `files_root`.

        The path is opened one folder at a time from `files_root`, each step refusing a symlink
        (`O_NOFOLLOW` relative to the folder already open). Nothing is checked first and opened
        later, so a worker cannot swap a folder for a link in between.
        """
        path = Path(os.path.normpath(raw))
        if not path.is_absolute():
            raise ArtifactRefused(f'The artifact was not saved: {raw} must be an absolute path.')
        if self.files_root is None:
            raise ArtifactRefused(
                'The artifact was not saved: this hub cannot read files from the NUC. '
                'Hand it in as a link instead.'
            )
        outside = ArtifactRefused(
            f'The artifact was not saved: {raw} is outside the workers’ folder or goes through '
            'a link, so the hub will not copy it.'
        )
        missing = ArtifactRefused(f'The artifact was not saved: there is no file at {raw}.')
        root = self.files_root.resolve()  # set by the hub, not by a worker
        rel = next((path.relative_to(base) for base in (self.files_root, root)
                    if path.is_relative_to(base)), None)  # fmt: skip
        if rel is None or not rel.parts:
            raise outside

        fds = [os.open(root, os.O_RDONLY | os.O_DIRECTORY)]
        try:
            for part in rel.parts[:-1]:
                fds.append(_open_at(part, fds[-1], os.O_DIRECTORY, outside, missing))
            fd = _open_at(rel.parts[-1], fds[-1], os.O_NONBLOCK, outside, missing)
        finally:
            for f in fds:
                os.close(f)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise missing
        with os.fdopen(fd, 'rb') as f:
            data = f.read(MAX_BLOB_BYTES + 1)
        if len(data) > MAX_BLOB_BYTES:
            raise ArtifactRefused(
                f'The artifact was not saved: {path.name} is too big '
                f'(the limit is {MAX_BLOB_BYTES // (1024 * 1024)} MB).'
            )
        return data, path.name

    def _write_blob(self, name: str, data: bytes) -> Path:
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        blob = self.blob_dir / name
        tmp = blob.with_name(f'.{name}.saving')
        tmp.write_bytes(data)
        os.replace(tmp, blob)
        return blob

    def _store(self, art: Artifact, data: bytes | None, summary: str | None = None) -> Artifact:
        if art.task_id is not None:
            with Session(self.engine) as s:
                if s.get(Task, art.task_id) is None:
                    raise ArtifactRefused(
                        f'The artifact was not saved: there is no task {art.task_id}.'
                    )
        blob = None if data is None else self._write_blob(blob_name(art.id, 1), data)
        try:
            art = self._insert(art, summary)
        except Exception:
            if blob is not None:
                blob.unlink(missing_ok=True)
            raise
        self._announce(art)
        return art

    def _insert(self, art: Artifact, summary: str | None) -> Artifact:
        with Session(self.engine) as s:
            s.add(art)
            s.commit()  # version 1 is written with it (`models._first_version`)
            if summary is not None:
                first = s.get(ArtifactVersion, (art.id, 1))
                assert first is not None
                first.summary = summary
                s.add(first)
                s.commit()
            s.refresh(art)
        return art

    def add_version(
        self,
        artifact_id: str,
        content: Content,
        *,
        title: str | None = None,
        task_id: str | None = None,
        author: str | None = None,
        summary: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Artifact:
        """Store `content` as the artifact's next version and send `artifact.updated`. The
        author defaults to the task's title. Never posts a second item-get message (B-36)."""
        with self._lock:
            art = self.get(artifact_id)
            number = art.current_version + 1
            blob = None
            if content.data is not None:
                blob = self._write_blob(blob_name(art.id, number), content.data)
            try:
                with Session(self.engine) as s:
                    row = s.get(Artifact, artifact_id)
                    assert row is not None
                    task = s.get(Task, task_id) if task_id is not None else None
                    now = utcnow()
                    row.current_version = number
                    row.updated_at = now
                    row.updated_by = author or (task.title if task else None) or row.updated_by
                    row.title = title or row.title
                    if content.url is not None:
                        row.url = content.url
                        if row.kind is ArtifactKind.pr:  # a new address decides it again
                            row.self_change = is_self_change_url(row.url)
                    if content.data is not None:
                        row.size_bytes = len(content.data)
                        row.content_type = content.content_type or row.content_type
                    row.meta_json = {**row.meta_json, **content.meta, **(meta or {})}
                    version = ArtifactVersion(
                        artifact_id=row.id, version=number, url=row.url,
                        content_type=row.content_type, size_bytes=row.size_bytes,
                        summary=summary, author=row.updated_by, task_id=task_id, created_at=now,
                    )  # fmt: skip
                    s.add(row)
                    s.add(version)
                    s.commit()
                    s.refresh(row)
                    s.refresh(version)
            except Exception:
                if blob is not None:
                    blob.unlink(missing_ok=True)
                raise
        if task_id is not None and self.tasks is not None:
            self.tasks.append_event(task_id, TaskEventType.artifact,
                                    {'artifact_id': row.id, 'kind': row.kind.value,
                                     'title': row.title})  # fmt: skip
        self._updated(row, version)
        return row

    def fill_first(self, artifact_id: str, data: bytes, title: str | None = None) -> Artifact:
        """Write the bytes of version 1 of an artifact made without them (a project's board, made
        with the project) and send `artifact.created`: the app hears of it now."""
        with self._lock:
            self._write_blob(blob_name(artifact_id, 1), data)
            with Session(self.engine) as s:
                row = s.get(Artifact, artifact_id)
                first = s.get(ArtifactVersion, (artifact_id, 1))
                assert row is not None and first is not None
                row.size_bytes = first.size_bytes = len(data)
                row.title = title or row.title
                s.add(row)
                s.add(first)
                s.commit()
                s.refresh(row)
        self._publisher().publish('artifact.created', self.to_api(row))
        return row

    def set_meta(self, artifact_id: str, **values: Any) -> Artifact:
        """Change `meta_json` keys without a new version (e.g. a board's notes)."""
        with self._lock, Session(self.engine) as s:
            row = s.get(Artifact, artifact_id)
            if row is None:
                raise ArtifactNotFound(artifact_id)
            row.meta_json = {**row.meta_json, **values}
            s.add(row)
            s.commit()
            s.refresh(row)
            return row

    def move(self, artifact_id: str, project_id: str, user_id: str) -> tuple[Artifact, str | None]:
        """Pin an item to another live project of its agent's. Returns the item and the project
        it came from, or None as that project when nothing changed (it was there already). Sends
        `artifact.moved`, then `artifact.updated` with its current version; the caller announces
        both projects.

        Raises `ArtifactNotFound` (also for someone else's item), `MoveTargetNotFound`,
        `ArtifactIsBoard`, `MoveTargetArchived`."""
        with self._lock, Session(self.engine) as s:
            row = s.get(Artifact, artifact_id)
            agent_id = agent_of_artifact(s, row) if row is not None else None
            agent = s.get(Agent, agent_id) if agent_id is not None else None
            if row is None or agent is None or agent.owner_user_id != user_id:
                raise ArtifactNotFound(artifact_id)
            target = s.get(Project, project_id)
            if target is None or target.agent_id != agent.id:
                raise MoveTargetNotFound(project_id)
            # An item filed nowhere counts as in the inbox, where `_file_artifact` puts those.
            source = row.project_id or inbox_id(s.connection(), agent.id)
            if source == target.id:
                return row, None
            if row.kind is ArtifactKind.board:
                raise ArtifactIsBoard(artifact_id)
            if target.archived:
                raise MoveTargetArchived(target.name)
            target_id = row.project_id = target.id
            row.pinned = True  # from now on it stays here when its task moves
            s.add(row)
            s.commit()
            s.refresh(row)
        self._publisher().publish(
            'artifact.moved',
            {'artifact_id': row.id, 'from_project_id': source, 'to_project_id': target_id},
        )
        self._updated(row, self.version(row.id, row.current_version))
        return row, source

    def announce_updated(self, artifact_id: str) -> None:
        """Send `artifact.updated` with the item as it is now (no new version), e.g. when its
        PR note changed."""
        art = self.get(artifact_id)
        self._updated(art, self.version(art.id, art.current_version))

    def announce_moved(self, task_id: str) -> None:
        """A task moved to another project (S-04.10): its unpinned items moved with it. Send
        `artifact.updated` for each, with its current version, so the app files it again."""
        with Session(self.engine) as s:
            rows = list(
                s.exec(
                    select(Artifact)
                    .where(Artifact.task_id == task_id)
                    .where(Artifact.pinned == False)  # noqa: E712
                )
            )
        for art in rows:
            self._updated(art, self.version(art.id, art.current_version))

    def _updated(self, art: Artifact, version: ArtifactVersion) -> None:
        self._publisher().publish(
            'artifact.updated',
            {'artifact': self.to_api(art), 'version': self.version_to_api(version)},
        )

    def _announce(self, art: Artifact) -> None:
        """A new artifact: record it on its task, tell the app (`artifact.created`), and post the
        item-get message in the agent's main conversation (B-36)."""
        if art.task_id is not None and self.tasks is not None:
            self.tasks.append_event(
                art.task_id,
                TaskEventType.artifact,
                {'artifact_id': art.id, 'kind': art.kind.value, 'title': art.title},
            )
            self.tasks._publish_task(art.task_id)  # artifact_count changed
        self._publisher().publish('artifact.created', self.to_api(art))
        if art.task_id is not None:
            self._item_get(art)

    def _item_get(self, art: Artifact) -> None:
        """The `artifact` message: "an item arrived" (B-36). Only for the first version of an
        artifact a task made; a failure to post it never loses the artifact."""
        if self.events is None:
            return
        try:
            with Session(self.engine) as s:
                task = s.get(Task, art.task_id)
                agent = s.get(Agent, task.agent_id) if task else None
                if task is None or agent is None:
                    return
                post_message(
                    s, self.events, ensure_main_conversation(s, agent), role=MessageRole.agent,
                    kind=MessageKind.artifact, body={}, task_id=task.id,
                    project_id=art.project_id, artifact_id=art.id,
                )  # fmt: skip
        except Exception:
            log.exception('Could not post the item message for %s.', art.id)

    def _publisher(self) -> Publisher:
        if self.publisher is not None:
            return self.publisher
        return self.tasks.publisher if self.tasks is not None else NullPublisher()

    # PR status -----------------------------------------------------------------------------

    async def refresh_pr(self, artifact_id: str) -> Artifact:
        """Ask the PR status source and store the PR's state, checks and review, and the time of
        this good refresh as `pr.refreshed_at`. When state, checks or review changed, send
        `artifact.updated` with the current version (no new version: the artifact is the same
        PR); when nothing else changed, send nothing.

        Raises `PrStatusUnavailable` (the last known status stays), `ArtifactNotFound`, or
        `ArtifactRefused` for an artifact that is not a PR.
        """
        art = self.get(artifact_id)
        if art.kind is not ArtifactKind.pr or art.url is None:
            raise ArtifactRefused(f'“{art.title}” is not a pull request, so it has no checks.')
        status = await self.pr_source.fetch(art.url)
        allowed = {'state': PR_STATES, 'checks': PR_CHECKS, 'review': PR_REVIEWS}
        for key, values in allowed.items():
            if (key in status or key == 'state') and status.get(key) not in values:
                raise PrStatusUnavailable(
                    f'GitHub sent a PR {key} the hub does not know ({status.get(key)!r}).'
                )
        with self._lock, Session(self.engine) as s:
            row = s.get(Artifact, artifact_id)
            if row is None:
                raise ArtifactNotFound(artifact_id)
            before = dict(row.meta_json.get('pr', {}))
            pr = {**before, **{k: status[k] for k in allowed if k in status}}
            changed = pr != before
            # Every good refresh is stamped; a refresh that changes nothing else sends no event,
            # so the new time rides on the next `artifact.updated` (or the next read).
            pr['refreshed_at'] = iso(utcnow())
            row.meta_json = {**row.meta_json, 'pr': pr}
            s.add(row)
            s.commit()
            s.refresh(row)
        if changed:
            self._updated(row, self.version(row.id, row.current_version))
        return row
