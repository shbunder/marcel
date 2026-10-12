"""Artifacts (B-21): the things tasks produce, kept by the hub and listed in the app's Items tab.

A worker hands an artifact in with `report` (channel.schema.json `Artifact`); the channel gateway
passes it to `ArtifactStore.publish_reported`. What each kind needs:

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
a read-only token; that client is S-04.12. Until it is wired, the hub starts with `NoPrStatus`,
which says plainly that it cannot ask GitHub.
"""

import json
import mimetypes
import os
import re
import stat
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import desc
from sqlmodel import Session, col, select

from marcel_hub.auth import require_device
from marcel_hub.models import Artifact, ArtifactKind, Task, TaskEventType
from marcel_hub.tasks.ports import NullPublisher, Publisher
from marcel_hub.tasks.registry import TaskRegistry, iso

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / 'contracts'
MAX_BLOB_BYTES = 25 * 1024 * 1024

PR_URL = re.compile(r'/pull/(\d+)/?$')
URL_KINDS = {ArtifactKind.pr, ArtifactKind.branch, ArtifactKind.link}
FILE_KINDS = {ArtifactKind.doc, ArtifactKind.file, ArtifactKind.dashboard}
DOC_TYPES = {'.md': 'text/markdown', '.markdown': 'text/markdown', '.html': 'text/html',
             '.htm': 'text/html'}  # fmt: skip

PR_STATES = {'open', 'draft', 'merged', 'closed'}
PR_CHECKS = {'pending', 'passing', 'failing', 'none'}
PR_REVIEWS = {'none', 'requested', 'approved', 'changes_requested'}

# Only the standard table, never /etc/mime.types: the same file gets the same type everywhere.
_MIME = mimetypes.MimeTypes()


class ArtifactRefused(Exception):
    """The artifact was not stored. The message tells the worker what to fix."""


class ArtifactNotFound(Exception):
    """No artifact with that id (404 `artifact_not_found`)."""


class NoContent(Exception):
    """The artifact lives at a URL, not in the hub (404). The message says so."""


class PrStatusUnavailable(Exception):
    """The PR status could not be read. The message says why, in plain words."""


# PR status ---------------------------------------------------------------------------------


class PrStatusSource(Protocol):
    async def fetch(self, url: str) -> dict[str, Any]:
        """The PR's `state`, and `checks` and `review` when known (the contract's `pr` values).
        Raises `PrStatusUnavailable`."""
        ...


class NoPrStatus:
    async def fetch(self, url: str) -> dict[str, Any]:
        raise PrStatusUnavailable(
            'The hub has no way to ask GitHub yet, so PR checks and reviews are not refreshed.'
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


class ArtifactStore:
    def __init__(
        self,
        engine: Any,
        blob_dir: Path,
        tasks: TaskRegistry | None = None,
        publisher: Publisher | None = None,
        files_root: Path | None = None,
        pr_source: PrStatusSource | None = None,
    ) -> None:
        self.engine = engine
        self.blob_dir = blob_dir
        self.tasks = tasks
        self.publisher = publisher
        self.files_root = files_root  # the workers' folder as the hub sees it; None: no copying
        self.pr_source: PrStatusSource = pr_source or NoPrStatus()

    # Reading -------------------------------------------------------------------------------

    def get(self, artifact_id: str) -> Artifact:
        with Session(self.engine) as s:
            art = s.get(Artifact, artifact_id)
        if art is None:
            raise ArtifactNotFound(artifact_id)
        return art

    def page(
        self,
        task_id: str | None = None,
        kind: ArtifactKind | None = None,
        offset: int = 0,
        limit: int = 50,
        project_id: str | None = None,
    ) -> tuple[list[Artifact], int | None]:
        """Newest first. Returns the page and the offset of the next one (None at the end)."""
        query = select(Artifact)
        if task_id is not None:
            query = query.where(Artifact.task_id == task_id)
        if project_id is not None:
            query = query.where(Artifact.project_id == project_id)
        if kind is not None:
            query = query.where(Artifact.kind == kind)
        query = query.order_by(desc(col(Artifact.created_at)), desc(col(Artifact.id)))
        with Session(self.engine) as s:
            rows = list(s.exec(query.offset(offset).limit(limit + 1)))
        more = len(rows) > limit
        return rows[:limit], (offset + limit if more else None)

    def content(self, artifact_id: str) -> tuple[bytes, str, bool]:
        """The bytes, content type and `trusted` of an artifact the hub holds."""
        path, art = self.content_path(artifact_id)
        return path.read_bytes(), art.content_type or 'application/octet-stream', art.trusted

    def content_path(self, artifact_id: str) -> tuple[Path, Artifact]:
        art = self.get(artifact_id)
        if art.kind not in FILE_KINDS:
            raise NoContent(f'“{art.title}” is a link, so the hub has no copy. Open its link.')
        path = self.blob_dir / art.id
        if not path.is_file():
            raise ArtifactNotFound(artifact_id)
        return path, art

    @staticmethod
    def to_api(art: Artifact) -> dict[str, Any]:
        """The contract's `Artifact`. Versions come with S-04.11; until then every artifact is at
        version 1, and its current version is its first."""
        body: dict[str, Any] = {
            'id': art.id,
            'kind': art.kind.value,
            'title': art.title,
            'current_version': 1,
            'trusted': art.trusted,
            'self_change': art.self_change,
            'created_at': iso(art.created_at),
            'updated_at': iso(art.created_at),
        }
        for name in ('task_id', 'project_id', 'url', 'content_type', 'size_bytes'):
            if (value := getattr(art, name)) is not None:
                body[name] = value
        if 'pr' in art.meta_json:
            body['pr'] = art.meta_json['pr']
        return body

    # Storing -------------------------------------------------------------------------------

    def publish_reported(self, spec: dict[str, Any], task_id: str | None = None) -> Artifact:
        """Store what a worker handed in with `report`. Raises `ArtifactRefused`."""
        try:
            reported = ReportedArtifact.model_validate(spec)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = '.'.join(str(p) for p in first['loc']) or 'the artifact'
            raise ArtifactRefused(f'The artifact was not saved: {where}: {first["msg"]}.') from exc
        if reported.artifact_id is not None:
            raise ArtifactRefused(
                'The artifact was not saved: this hub cannot store a new version of an artifact '
                'yet. Hand it in without artifact_id.'
            )
        kind = ArtifactKind(reported.kind)
        if kind in URL_KINDS:
            return self._publish_url(kind, reported, task_id)

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
        return self._store(
            Artifact(kind=kind, title=reported.title, task_id=task_id, trusted=False,
                     content_type=content_type, size_bytes=len(data)),
            data,
        )  # fmt: skip

    def publish_hub_html(self, title: str, html: str, task_id: str | None = None) -> Artifact:
        """HTML the hub rendered itself: the only artifact that is `trusted`."""
        data = html.encode('utf-8')
        art = Artifact(kind=ArtifactKind.doc, title=title, task_id=task_id, trusted=True,
                       content_type='text/html', size_bytes=len(data))  # fmt: skip
        return self._store(art, data)

    def _publish_url(
        self, kind: ArtifactKind, reported: ReportedArtifact, task_id: str | None
    ) -> Artifact:
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
        art = Artifact(kind=kind, title=reported.title, task_id=task_id, url=url, trusted=False,
                       meta_json=meta)  # fmt: skip
        return self._store(art, None)

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

    def _store(self, art: Artifact, data: bytes | None) -> Artifact:
        if art.task_id is not None:
            with Session(self.engine) as s:
                if s.get(Task, art.task_id) is None:
                    raise ArtifactRefused(
                        f'The artifact was not saved: there is no task {art.task_id}.'
                    )
        blob: Path | None = None
        if data is not None:
            self.blob_dir.mkdir(parents=True, exist_ok=True)
            blob = self.blob_dir / art.id
            tmp = blob.with_name(f'.{art.id}.saving')
            tmp.write_bytes(data)
            os.replace(tmp, blob)
        try:
            art = self._insert(art)
        except Exception:
            if blob is not None:
                blob.unlink(missing_ok=True)
            raise
        self._announce(art)
        return art

    def _insert(self, art: Artifact) -> Artifact:
        with Session(self.engine) as s:
            s.add(art)
            s.commit()
            s.refresh(art)
        return art

    def _announce(self, art: Artifact) -> None:
        """Record the artifact on its task and tell the app (`artifact.created`)."""
        if art.task_id is not None and self.tasks is not None:
            self.tasks.append_event(
                art.task_id,
                TaskEventType.artifact,
                {'artifact_id': art.id, 'kind': art.kind.value, 'title': art.title},
            )
            self.tasks._publish_task(art.task_id)  # artifact_count changed
        self._publisher().publish('artifact.created', self.to_api(art))

    def _publisher(self) -> Publisher:
        if self.publisher is not None:
            return self.publisher
        return self.tasks.publisher if self.tasks is not None else NullPublisher()

    # PR status -----------------------------------------------------------------------------

    async def refresh_pr(self, artifact_id: str) -> Artifact:
        """Ask the PR status source and store the PR's state, checks and review.

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
        with Session(self.engine) as s:
            row = s.get(Artifact, artifact_id)
            assert row is not None
            pr = dict(row.meta_json.get('pr', {}))
            pr.update({k: status[k] for k in allowed if k in status})
            row.meta_json = {**row.meta_json, 'pr': pr}
            s.add(row)
            s.commit()
            s.refresh(row)
        return row


# Routes ------------------------------------------------------------------------------------

router = APIRouter(prefix='/api', dependencies=[Depends(require_device)])


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({'code': code, 'message': message}, status_code=status)


def _not_found(artifact_id: str) -> JSONResponse:
    return _error(404, 'artifact_not_found', f'There is no item {artifact_id}. It may be gone.')


def _store(request: Request) -> ArtifactStore:
    return request.app.state.artifacts


@router.get('/artifacts')
def list_artifacts(
    request: Request,
    task_id: str | None = None,
    project_id: str | None = None,
    kind: str | None = None,
    cursor: str | None = None,
    limit: str = '50',
) -> Any:
    if kind == 'board':  # in the contract; boards arrive with S-04.11
        return {'items': []}
    kinds = [k.value for k in ArtifactKind]
    if kind is not None and kind not in kinds:
        return _error(400, 'bad_request', f'kind must be one of {", ".join(kinds)}.')
    if not limit.isdigit() or not 1 <= int(limit) <= 200:
        return _error(400, 'bad_request', 'limit must be a number from 1 to 200.')
    if cursor is not None and not cursor.isdigit():
        return _error(400, 'bad_request', 'That page cursor is not valid. Start again without it.')
    store = _store(request)
    rows, nxt = store.page(
        task_id, ArtifactKind(kind) if kind else None, int(cursor or 0), int(limit), project_id
    )
    page: dict[str, Any] = {'items': [store.to_api(a) for a in rows]}
    if nxt is not None:
        page['next_cursor'] = str(nxt)
    return page


@router.get('/artifacts/{artifact_id}')
def get_artifact(request: Request, artifact_id: str) -> Any:
    try:
        return _store(request).to_api(_store(request).get(artifact_id))
    except ArtifactNotFound:
        return _not_found(artifact_id)


@router.get('/artifacts/{artifact_id}/content')
def get_artifact_content(
    request: Request, artifact_id: str, version: Annotated[int | None, Query(ge=1)] = None
) -> Any:
    try:
        path, art = _store(request).content_path(artifact_id)
    except ArtifactNotFound:
        return _not_found(artifact_id)
    except NoContent as exc:
        return _error(404, 'not_found', str(exc))
    if version is not None and version != 1:
        return _error(404, 'not_found', f'“{art.title}” has only one version.')
    # The hub never renders content. Untrusted HTML opened straight from this URL runs in a
    # sandbox with no scripts; even trusted HTML gets an origin of its own.
    headers = {
        'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': 'sandbox allow-scripts' if art.trusted else 'sandbox',
    }
    return FileResponse(path, media_type=art.content_type, headers=headers)


def install(app: FastAPI, files_root: Path | None = None) -> ArtifactStore:
    """Put the store on `app.state.artifacts` and mount `/api/artifacts`. Call after
    `tasks.install`: an artifact handed in by a task is recorded on that task."""
    store = ArtifactStore(
        app.state.engine,
        app.state.settings.data_dir / 'artifacts',
        tasks=getattr(app.state, 'tasks', None),
        files_root=files_root,
    )
    app.state.artifacts = store
    app.include_router(router)
    return store
