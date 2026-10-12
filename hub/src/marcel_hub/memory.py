"""Memory (B-20): Markdown files in a git working copy at `<data_dir>/memory`; the hub commits.

The brain reads and writes the files directly (they are symlinked into its workspace); the app
reads and edits them through `/api/memory`. Every save through the hub is one git commit with the
message `memory: <path> (app|agent)`.

- **Etag.** A file's etag is the git blob id of its bytes on disk, so an edit made anywhere (the
  app, the brain, a person over SSH) changes it. A save with a stale etag is refused with the
  current version (409), never a silent overwrite.
- **Paths.** A path must match the contract's pattern (letters, digits, `.`, `_`, `-`, `/`, ending
  in `.md`, no `..`, no leading `/`, no part starting with `.`), and no part of it may be a
  symlink, so a path can never leave the memory folder or reach `.git/`.
- **Git.** Run as a subprocess with no global or system config, so the host's hooks and settings
  never apply. If git is missing or fails, the save is undone and answers 503 `memory_unavailable`.
"""

import contextlib
import hashlib
import os
import re
import stat
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.concurrency import run_in_threadpool

from marcel_hub.auth import require_device

GIT = 'git'
MAX_FILE_BYTES = 1_000_000
PATH_PATTERN = re.compile(r'^(?!/)(?!.*\.\.)(?!(.*/)?\.)[A-Za-z0-9._/-]+\.md$')  # the contract's
SUBJECT = re.compile(r'^memory: .+ \((app|agent)\)$')

Writer = Literal['app', 'agent']


class InvalidMemoryPath(Exception):
    """The path is not a memory file path (400 `invalid_memory_path`)."""


class MemoryFileNotFound(Exception):
    """No file at that path (404 `memory_file_not_found`)."""


class MemoryConflict(Exception):
    """The file changed since it was read, or exists already (409). Carries the current one."""

    def __init__(self, current: dict[str, Any]) -> None:
        super().__init__(f'{current["path"]} changed since it was read.')
        self.current = current


class MemoryUnavailable(Exception):
    """Git could not be run. The message says why, in plain words (503 `memory_unavailable`)."""


def blob_id(data: bytes) -> str:
    """The git blob id of `data`: what `git hash-object` prints."""
    return hashlib.sha1(b'blob %d\0' % len(data) + data, usedforsecurity=False).hexdigest()


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def _write_file(path: Path, data: bytes) -> None:
    """Write through a temporary file and a rename, so a reader never sees half a file."""
    tmp = path.with_name(f'.{path.name}.saving')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


class MemoryRepo:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()

    # Git -----------------------------------------------------------------------------------

    def _git(self, *args: str) -> str:
        env = {
            **os.environ,
            'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_AUTHOR_NAME': 'Marcel hub',
            'GIT_AUTHOR_EMAIL': 'hub@marcel.invalid',
            'GIT_COMMITTER_NAME': 'Marcel hub',
            'GIT_COMMITTER_EMAIL': 'hub@marcel.invalid',
        }
        cmd = [GIT, '-C', str(self.root), '-c', f'safe.directory={self.root}',
               '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
               '-c', 'core.quotePath=false', *args]  # fmt: skip
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MemoryUnavailable(f'git could not run ({exc}).') from exc
        if out.returncode != 0:
            raise MemoryUnavailable(f'git {args[0]} failed: {out.stderr.strip()}')
        return out.stdout

    def _ensure(self) -> None:
        if not (self.root / '.git').is_dir():
            self.root.mkdir(parents=True, exist_ok=True)
            self._git('init', '-q', '-b', 'main')

    def _head_blobs(self) -> dict[str, str]:
        """path -> blob id for every file in the last commit (empty before the first one)."""
        try:
            out = self._git('ls-tree', '-r', '-z', 'HEAD')
        except MemoryUnavailable:
            return {}
        blobs: dict[str, str] = {}
        for entry in out.split('\0'):
            if entry:
                meta, path = entry.split('\t', 1)
                blobs[path] = meta.split()[2]
        return blobs

    def _last_commits(self) -> dict[str, tuple[str, str]]:
        """path -> (commit time, subject) of the last commit that touched it."""
        try:
            out = self._git('log', '--format=%x1e%cI%x1f%s', '--name-only')
        except MemoryUnavailable:
            return {}
        seen: dict[str, tuple[str, str]] = {}
        for record in out.split('\x1e')[1:]:
            header, _, names = record.partition('\n')
            at, _, subject = header.partition('\x1f')
            for name in names.splitlines():
                if name:
                    seen.setdefault(name, (at, subject))
        return seen

    # Paths ---------------------------------------------------------------------------------

    def resolve(self, rel: str) -> Path:
        """The file for `rel`, or `InvalidMemoryPath` if it could leave the memory folder."""
        if not PATH_PATTERN.fullmatch(rel) or '//' in rel:
            raise InvalidMemoryPath(rel)
        path = self.root
        for part in rel.split('/'):
            path = path / part
            try:
                if stat.S_ISLNK(os.lstat(path).st_mode):
                    raise InvalidMemoryPath(rel)
            except FileNotFoundError:
                break
        root = self.root.resolve()
        if not (self.root / rel).resolve().is_relative_to(root):
            raise InvalidMemoryPath(rel)
        return self.root / rel

    # Reading -------------------------------------------------------------------------------

    def _describe(self, rel: str, data: bytes, path: Path) -> dict[str, Any]:
        etag = blob_id(data)
        body: dict[str, Any] = {'path': rel, 'content': data.decode('utf-8', errors='replace'),
                                'etag': etag}  # fmt: skip
        body.update(self._when(rel, etag, path, self._head_blobs(), self._last_commits()))
        return body

    @staticmethod
    def _when(
        rel: str,
        etag: str,
        path: Path,
        head: dict[str, str],
        commits: dict[str, tuple[str, str]],
    ) -> dict[str, Any]:
        """`updated_at` and `updated_by`: from the last commit if the file is as committed, else
        from the file itself (an edit nobody committed yet has no author to name)."""
        if head.get(rel) == etag and rel in commits:
            at, subject = commits[rel]
            body: dict[str, Any] = {'updated_at': _iso(datetime.fromisoformat(at))}
            if match := SUBJECT.fullmatch(subject):
                body['updated_by'] = match.group(1)
            return body
        mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        return {'updated_at': _iso(mtime)}

    def _read_bytes(self, rel: str) -> tuple[Path, bytes] | None:
        path = self.resolve(rel)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError as exc:  # a symlink swapped in after the check
            raise InvalidMemoryPath(rel) from exc
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return None
        with os.fdopen(fd, 'rb') as f:
            return path, f.read()

    def read(self, rel: str) -> dict[str, Any]:
        """The contract's `MemoryFile`."""
        self._ensure()
        found = self._read_bytes(rel)
        if found is None:
            raise MemoryFileNotFound(rel)
        path, data = found
        return self._describe(rel, data, path)

    def tree(self) -> list[dict[str, Any]]:
        """The contract's `MemoryTree.files`: every reachable Markdown file, sorted by path."""
        self._ensure()
        head, commits = self._head_blobs(), self._last_commits()
        files: list[dict[str, Any]] = []
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            for name in filenames:
                path = Path(dirpath) / name
                rel = path.relative_to(self.root).as_posix()
                if path.is_symlink() or not PATH_PATTERN.fullmatch(rel):
                    continue
                data = path.read_bytes()
                when = self._when(rel, blob_id(data), path, head, commits)
                files.append({'path': rel, 'size_bytes': len(data),
                              'updated_at': when['updated_at']})  # fmt: skip
        return sorted(files, key=lambda f: f['path'])

    # Writing -------------------------------------------------------------------------------

    def write(self, rel: str, content: str, if_match: str | None, by: Writer) -> dict[str, Any]:
        """Save `content` and commit it. `if_match` is the etag the caller read, or None to create.

        Raises `MemoryConflict` (with the current file) when the etag does not match or the file
        exists already, `MemoryFileNotFound` when `if_match` names a file that is gone.
        """
        data = content.encode('utf-8')
        with self._lock:
            self._ensure()
            found = self._read_bytes(rel)
            if found is not None:
                current = self._describe(rel, found[1], found[0])
                if if_match != current['etag']:
                    raise MemoryConflict(current)
                if found[1] == data:
                    return current
            elif if_match is not None:
                raise MemoryFileNotFound(rel)

            path = self.resolve(rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.resolve(rel)  # a folder made just now cannot be a symlink, but check anyway
            _write_file(path, data)
            try:
                self._git('add', '--', rel)
                self._git('commit', '-q', '--only', '-m', f'memory: {rel} ({by})', '--', rel)
            except MemoryUnavailable:
                if found is None:
                    path.unlink()
                    self._git_quiet('rm', '-q', '--cached', '--ignore-unmatch', '--', rel)
                else:
                    _write_file(path, found[1])
                    self._git_quiet('add', '--', rel)
                raise
            return self._describe(rel, data, path)

    def _git_quiet(self, *args: str) -> None:
        """Best effort while undoing a failed save: the file on disk is already put back."""
        with contextlib.suppress(MemoryUnavailable):
            self._git(*args)


# Routes ----------------------------------------------------------------------------------

router = APIRouter(prefix='/api', dependencies=[Depends(require_device)])


class MemoryWrite(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    content: str


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({'code': code, 'message': message}, status_code=status)


def _invalid_path(rel: str) -> JSONResponse:
    return _error(
        400,
        'invalid_memory_path',
        f'"{rel}" is not a memory file. Use a path like MEMORY.md or memory/2026-10-04.md.',
    )


def _not_found(rel: str) -> JSONResponse:
    return _error(404, 'memory_file_not_found', f'There is no memory file called {rel}.')


def _unavailable(exc: MemoryUnavailable) -> JSONResponse:
    return _error(503, 'memory_unavailable', f'The memory was not saved: {exc} Try again later.')


def _file(body: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={'ETag': body['etag']})


def _repo(request: Request) -> MemoryRepo:
    return request.app.state.memory


def _etag(header: str | None) -> str | None:
    """Accept `abc`, `"abc"` and `W/"abc"`: the contract sends the bare etag, HTTP quotes it."""
    if header is None:
        return None
    return header.strip().removeprefix('W/').strip('"')


@router.get('/memory')
def get_memory_tree(request: Request) -> Any:
    try:
        return {'files': _repo(request).tree()}
    except MemoryUnavailable as exc:
        return _error(503, 'memory_unavailable', f'The memory cannot be read: {exc}')


@router.get('/memory/files/{path:path}')
def get_memory_file(request: Request, path: str) -> Any:
    try:
        return _file(_repo(request).read(path))
    except InvalidMemoryPath:
        return _invalid_path(path)
    except MemoryFileNotFound:
        return _not_found(path)
    except MemoryUnavailable as exc:
        return _error(503, 'memory_unavailable', f'The memory cannot be read: {exc}')


@router.put('/memory/files/{path:path}')
async def put_memory_file(request: Request, path: str) -> Any:
    raw = await request.body()
    try:
        body = MemoryWrite.model_validate_json(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = '.'.join(str(p) for p in first['loc']) or 'the body'
        return _error(400, 'bad_request', f'Send JSON like {{"content": "…"}} ({where}: '
                                          f'{first["msg"]}).')  # fmt: skip
    if len(body.content.encode('utf-8')) > MAX_FILE_BYTES:
        return _error(400, 'bad_request', 'That memory file is too long. Keep it under 1 MB.')
    if_match = _etag(request.headers.get('If-Match'))
    repo = _repo(request)
    try:
        # Off the event loop: the save runs git and holds the write lock.
        saved = await run_in_threadpool(repo.write, path, body.content, if_match, 'app')
    except InvalidMemoryPath:
        return _invalid_path(path)
    except MemoryFileNotFound:
        return _error(
            404,
            'memory_file_not_found',
            f'{path} was removed since you opened it. Save it again to create it.',
        )
    except MemoryConflict as exc:
        return _file(exc.current, 409)
    except MemoryUnavailable as exc:
        return _unavailable(exc)
    return _file(saved)


def install(app: FastAPI) -> MemoryRepo:
    """Put the memory repo on `app.state.memory` and mount `/api/memory`. Git runs on first use."""
    repo = MemoryRepo(app.state.settings.data_dir / 'memory')
    app.state.memory = repo
    app.include_router(router)
    return repo
