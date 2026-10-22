"""Memory (B-20): Markdown files in a git working copy at `<data_dir>/memory`; the hub commits.

**One folder per person's agent (S-17.4, B-38).** Each agent's memory lives under
`agents/<slug>/` (`MEMORY.md`, `memory/…`, `projects/<slug>.md`). Everyone reaches it through
`MemoryRepo.for_agent(agent_id)`, an `AgentMemory` that shows the paths without the prefix, so the
app, the brain and workers keep using `MEMORY.md` and `memory/…`. The owner's memory from before
0.7 is moved under `agents/<their slug>/` once, with git (`git log --follow` keeps its history),
and links are left at the old places so a brain workspace rendered earlier still finds it; the hub
never follows a link, so the old paths reach nothing through it.

The brain reads and writes the files directly (they are symlinked into its workspace); the app
reads and edits them through `/api/memory`. Every save through the hub is one git commit with the
message `memory: <path> (app|agent)`; a worker's note (`append`) is `memory: <path> (worker <task
id>)`, which the app reads as `agent`.

- **Etag.** A file's etag is the git blob id of its bytes on disk, so an edit made anywhere (the
  app, the brain, a person over SSH) changes it. A save with a stale etag is refused with the
  current version (409), never a silent overwrite.
- **Paths.** A path must match the contract's pattern (letters, digits, `.`, `_`, `-`, `/`, ending
  in `.md`, no `..`, no leading `/`, no part starting with `.`), and no part of it may be a
  symlink, so a path can never leave the memory folder or reach `.git/`.
- **Git.** The git database lives in `<data_dir>/memory.git`, outside the folder the brain can
  reach, so nobody but the hub can change its config or hooks. Git runs with no global or system
  config, hooks, file-system monitor or attribute files forced off, and the hub refuses to commit
  while a `.gitattributes` sits in the memory folder: nothing planted can make git run a program.
  A `.git` folder left inside the memory folder by an older hub is moved out once, with a fresh
  config. If git is missing or fails, the save is undone and answers 503 `memory_unavailable`.
"""

import contextlib
import hashlib
import logging
import os
import re
import shutil
import stat
import subprocess
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlmodel import Session, col, select
from starlette.concurrency import run_in_threadpool

from marcel_hub.auth import current_device, require_device
from marcel_hub.auth.routes import buddy_of
from marcel_hub.auth.tokens import OWNER_USER_ID
from marcel_hub.models import Agent, Device

log = logging.getLogger('marcel_hub')

GIT = 'git'
MAX_FILE_BYTES = 1_000_000
# The most of one memory file a worker's opening prompt carries (a "memory slice", e.g. project
# notes). The rest stays in the file, which the prompt names.
SLICE_MAX_BYTES = 8_000
# The request body may be longer than the file: JSON escapes (`\n`, `\/`, `\u00e9`) take room.
MAX_BODY_BYTES = 2 * MAX_FILE_BYTES
PATH_PATTERN = re.compile(r'^(?!/)(?!.*\.\.)(?!(.*/)?\.)[A-Za-z0-9._/-]+\.md$')  # the contract's
SUBJECT = re.compile(r'^memory: .+ \((app|agent|worker)(?: [^)]*)?\)$')
# Who `append` names in its commit subject, e.g. `worker tsk_019a2b3c4d5e1f2a3b4c`.
APPENDER = re.compile(r'^[a-z]+ [A-Za-z0-9_-]{1,80}$')

Writer = Literal['app', 'agent']

AGENTS = 'agents'
# An agent's folder name: its fixed slug (S-04.15), or its id when it has none.
AGENT_FOLDER = re.compile(r'^[a-z0-9][a-z0-9_-]{0,79}$')


class InvalidMemoryPath(Exception):
    """The path is not a memory file path (400 `invalid_memory_path`)."""


class MemoryFileNotFound(Exception):
    """No file at that path (404 `memory_file_not_found`)."""


class MemoryConflict(Exception):
    """The file changed since it was read, or exists already (409). Carries the current one."""

    def __init__(self, current: dict[str, Any]) -> None:
        super().__init__(f'{current["path"]} changed since it was read.')
        self.current = current


class MemoryFileTooBig(Exception):
    """A file on disk over `MAX_FILE_BYTES`: the app cannot open it (400 `memory_file_too_big`)."""


class TooManyAppends(Exception):
    """The writer already made as many appends as it may (`append`'s `max_appends`)."""


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


# Settings forced on every git command. They beat any config file, so nothing planted in a config
# can make git run a program: no hooks, no file-system monitor, no attribute files.
GIT_OVERRIDES = (
    'core.hooksPath=/dev/null',
    'core.fsmonitor=false',
    'core.attributesFile=/dev/null',
    'commit.gpgsign=false',
    'core.quotePath=false',
)
# A hub-written config for a git folder moved in from the memory folder (see `_migrate`).
FRESH_CONFIG = '[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n'


def _placeholder(path: Path) -> bool:
    """An empty file, or a folder with nothing but empty folders in it."""
    if path.is_file():
        return path.stat().st_size == 0
    return path.is_dir() and all(p.is_dir() for p in path.rglob('*'))


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


class MemoryRepo:
    """The memory folder (`root`, the work tree the brain can reach) and its git database
    (`git_dir`, default `<root>.git` next to it, which only the hub can reach)."""

    def __init__(
        self,
        root: Path,
        git_dir: Path | None = None,
        folder_of: Callable[[str], str | None] = str.lower,
        owner_folder: Callable[[], str | None] = lambda: None,
    ) -> None:
        self.root = root
        self.git_dir = git_dir or root.with_name(f'{root.name}.git')
        # Re-entrant: `write` and `append` hold it while `_ensure` may run the one-time move,
        # which takes it too, so the move and every save are one at a time.
        self._lock = threading.RLock()
        self._settled = False  # the owner's old memory is moved (or there was none): skip the look
        # agent id -> its folder under agents/: `install` reads the agent's slug; alone, its id.
        self.folder_of = folder_of
        self.owner_folder = owner_folder  # where the owner's memory from before 0.7 goes

    def for_agent(self, agent_id: str) -> 'AgentMemory':
        """The memory of one agent: what its brain, its workers and its person's app see."""
        folder = self.folder_of(agent_id)
        if folder is None:
            raise MemoryFileNotFound(agent_id)
        return AgentMemory(self, folder)

    # Git -----------------------------------------------------------------------------------

    def _git(self, *args: str, ok: tuple[int, ...] = (0,)) -> str | None:
        """Run git on the memory repo. Returns stdout, or None for an allowed non-zero exit."""
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env |= {
            'GIT_CONFIG_GLOBAL': os.devnull,  # never the host user's ~/.gitconfig
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_ATTR_NOSYSTEM': '1',
            'GIT_AUTHOR_NAME': 'Marcel hub',
            'GIT_AUTHOR_EMAIL': 'hub@marcel.invalid',
            'GIT_COMMITTER_NAME': 'Marcel hub',
            'GIT_COMMITTER_EMAIL': 'hub@marcel.invalid',
        }
        cmd = [GIT, f'--git-dir={self.git_dir}', f'--work-tree={self.root}', '--literal-pathspecs']
        for setting in GIT_OVERRIDES:
            cmd += ['-c', setting]
        try:
            out = subprocess.run(
                [*cmd, *args], capture_output=True, text=True, env=env, cwd=self.root, timeout=30
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MemoryUnavailable(f'git could not run ({exc}).') from exc
        if out.returncode == 0:
            return out.stdout
        if out.returncode in ok:
            return None
        raise MemoryUnavailable(f'git {args[0]} failed: {out.stderr.strip()}')

    def _out(self, *args: str) -> str:
        return self._git(*args) or ''

    def _ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._migrate()
        if not self.git_dir.is_dir():
            self._git('init', '-q', '-b', 'main')
        self._move_owners_memory()

    def settle(self) -> None:
        """Make sure the owner's memory from before 0.7 is where `for_agent` looks. Cheap once
        done; the brain's start-up notes and briefings call it before their first read."""
        if not self._settled:
            with self._lock:
                self._ensure()

    def _move_owners_memory(self) -> None:
        """Move what sits at the top of the memory folder (the owner's memory from before 0.7:
        `MEMORY.md`, `memory/`, `projects/`) under `agents/<owner's folder>/`, and commit it, so
        `git log --follow` keeps its history (S-17.4).

        Safe to stop at any point and run again: each entry gets its link back right after it is
        moved (a brain workspace rendered before still finds it), what git still tracks at the
        top is what is left to commit, and an entry already moved is recognised by its link. An
        empty file or folder at the top (what `brain/render.py` makes when it finds none) gives
        way to the link. Waits while the owner has no agent; never overwrites a file."""
        with self._lock:
            if self._settled:
                return
            folder = self.owner_folder()
            if folder is None or not AGENT_FOLDER.fullmatch(folder):
                return  # no owner agent yet: look again next time
            dest = self.root / AGENTS / folder
            tracked = {p.split('/', 1)[0] for p in self._head_blobs()} - {AGENTS}
            on_disk = {e.name for e in self.root.iterdir() if not e.name.startswith('.')} - {AGENTS}
            # What the owner's folder holds gets its link at the top too (a link lost later).
            kept = {e.name for e in dest.iterdir()} if dest.is_dir() else set()
            names = sorted(n for n in tracked | on_disk | kept if not n.startswith('.'))
            if not names:
                self._settled = True
                return
            self._refuse_attribute_files()
            moved: list[str] = []
            for name in names:
                if self._move_one(name, dest, folder):
                    moved.append(name)
            gone = [n for n in names if n in tracked and (self.root / n).is_symlink()]
            if gone:  # the old paths leave git; the links stay out of it
                self._git('rm', '-r', '-q', '--cached', '--ignore-unmatch', '--', *gone)
            if dest.is_dir():
                self._git('add', '-A', '--', f'{AGENTS}/{folder}')
            if self._git('diff', '--cached', '--quiet', ok=(0, 1)) is None:  # 1: staged changes
                subject = f"memory: move the owner's memory to {AGENTS}/{folder}/ (S-17.4)"
                self._git('commit', '-q', '-m', subject)
            if moved:
                log.info('Moved %s into %s/%s/.', moved, AGENTS, folder)
            # Settled once everything at the top is a link into the owner's folder.
            self._settled = all((self.root / n).is_symlink() or not (self.root / n).exists()
                                for n in names)  # fmt: skip

    def _move_one(self, name: str, dest: Path, folder: str) -> bool:
        """Move one top-level entry and put its link back. True if it moved now."""
        here, there = self.root / name, dest / name
        link = Path(AGENTS) / folder / name
        if here.is_symlink():
            return False  # moved before
        if not here.exists():
            if there.exists():  # moved, but stopped before its link was made
                here.symlink_to(link)
            return False
        if there.exists():
            if not _placeholder(here):
                log.warning('Did not move %s into %s: it has one already.', here, dest)
                return False
            _remove(here)  # an empty one `render.py` made after the move: the link replaces it
            here.symlink_to(link)
            return False
        dest.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(here, there)
        except OSError as exc:
            log.warning('Could not move %s into %s: %s', here, dest, exc)
            return False
        here.symlink_to(link)
        return True

    def _migrate(self) -> None:
        """Move a git folder out of the memory folder, once, so the brain can no longer edit it.

        What came along could have been edited by anyone who reached the memory folder, so its
        config is replaced and its hooks and attribute files are dropped; the history stays.
        """
        inside = self.root / '.git'
        if self.git_dir.exists() or not inside.is_dir() or inside.is_symlink():
            return
        os.replace(inside, self.git_dir)
        (self.git_dir / 'config').write_text(FRESH_CONFIG)
        shutil.rmtree(self.git_dir / 'hooks', ignore_errors=True)
        (self.git_dir / 'info' / 'attributes').unlink(missing_ok=True)

    def _refuse_attribute_files(self) -> None:
        """A `.gitattributes` can name a filter program; the hub never commits next to one."""
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            if '.gitattributes' in filenames:
                where = Path(dirpath, '.gitattributes').relative_to(self.root)
                raise MemoryUnavailable(
                    f'the memory folder has a {where} file, which the hub does not allow. '
                    'Remove it.'
                )
            dirnames[:] = [d for d in dirnames if d != '.git']

    def _has_commits(self) -> bool:
        return self._git('rev-parse', '-q', '--verify', 'HEAD', ok=(1, 128)) is not None

    def _head_blobs(self) -> dict[str, str]:
        """path -> blob id for every file in the last commit (empty before the first one)."""
        if not self._has_commits():
            return {}
        blobs: dict[str, str] = {}
        for entry in self._out('ls-tree', '-r', '-z', 'HEAD').split('\0'):
            if entry:
                meta, path = entry.split('\t', 1)
                blobs[path] = meta.split()[2]
        return blobs

    def _last_commits(self) -> dict[str, tuple[str, str]]:
        """path -> (commit time, subject) of the last commit that touched it."""
        if not self._has_commits():
            return {}
        out = self._out('log', '--format=%x1e%cI%x1f%s', '--name-only')
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
                # A worker is one of the agent's sessions: the contract's `updated_by` is
                # `app` or `agent`.
                body['updated_by'] = 'app' if match.group(1) == 'app' else 'agent'
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
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(fd)
            return None
        with os.fdopen(fd, 'rb') as f:
            data = f.read(MAX_FILE_BYTES + 1)  # a file written straight to disk can be any size
        if len(data) > MAX_FILE_BYTES:
            raise MemoryFileTooBig(rel)
        return path, data

    def read_slice(self, rel: str, cap: int = SLICE_MAX_BYTES) -> tuple[str, bool] | None:
        """The first `cap` bytes of a file as text, and whether it was cut; None if there is no
        file. Reads no more than that and runs no git once the owner's old memory is settled
        (`settle`), so it is cheap enough for every spawn."""
        self.settle()
        try:
            fd = os.open(self.resolve(rel), os.O_RDONLY | os.O_NOFOLLOW)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as exc:  # a symlink swapped in after the check
            raise InvalidMemoryPath(rel) from exc
        with os.fdopen(fd, 'rb') as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                return None
            data = f.read(cap + 1)
        cut = len(data) > cap
        return data[:cap].decode('utf-8', errors='ignore'), cut

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
                size = path.stat().st_size
                etag = blob_id(path.read_bytes()) if size <= MAX_FILE_BYTES else ''
                when = self._when(rel, etag, path, head, commits)
                files.append({'path': rel, 'size_bytes': size,
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

            return self._save(rel, data, found, f'memory: {rel} ({by})')

    def append(self, rel: str, line: str, by: str, max_appends: int | None = None) -> None:
        """Add `line` at the end of `rel` (made if missing) and commit it as
        `memory: <rel> (<by>)`, e.g. a worker's note (`by` = `worker <task id>`).

        Under the write lock, so appends at the same time never lose one another. `line` must be
        one line. With `max_appends`, raises `TooManyAppends` once git already holds that many
        commits by `by` (in any file); `MemoryFileTooBig` when the file would pass 1 MB.
        """
        if '\n' in line or '\r' in line or not line.strip():
            raise ValueError('append takes one line of text')
        if not APPENDER.fullmatch(by):
            raise ValueError(f'append cannot name {by!r} in a commit')
        with self._lock:
            self._ensure()
            if max_appends is not None and self._appends_by(by) >= max_appends:
                raise TooManyAppends(by)
            found = self._read_bytes(rel)
            before = found[1] if found is not None else b''
            if before and not before.endswith(b'\n'):
                before += b'\n'
            data = before + line.encode('utf-8') + b'\n'
            if len(data) > MAX_FILE_BYTES:
                raise MemoryFileTooBig(rel)
            self._save(rel, data, found, f'memory: {rel} ({by})')

    def _appends_by(self, by: str) -> int:
        if not self._has_commits():
            return 0
        tail = f' ({by})'
        out = self._out('log', '--format=%s', '--fixed-strings', f'--grep={tail}')
        return sum(1 for subject in out.splitlines() if subject.endswith(tail))

    def _save(
        self, rel: str, data: bytes, found: tuple[Path, bytes] | None, subject: str
    ) -> dict[str, Any]:
        """Write `data` and commit it; on a git failure put the file back as it was (`found`)."""
        self._refuse_attribute_files()
        path = self.resolve(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.resolve(rel)  # a folder made just now cannot be a symlink, but check anyway
        _write_file(path, data)
        try:
            self._git('add', '--', rel)
            self._git('commit', '-q', '--only', '-m', subject, '--', rel)
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


class AgentMemory:
    """One agent's memory: `agents/<folder>/` of the repo, with paths shown without the prefix.
    Nothing here can reach outside the folder: every path is checked as a memory path first."""

    def __init__(self, repo: MemoryRepo, folder: str) -> None:
        if not AGENT_FOLDER.fullmatch(folder):
            raise InvalidMemoryPath(folder)
        self.repo = repo
        self.prefix = f'{AGENTS}/{folder}/'

    def _full(self, rel: str) -> str:
        if not PATH_PATTERN.fullmatch(rel) or '//' in rel:
            raise InvalidMemoryPath(rel)
        return self.prefix + rel

    def _shown(self, body: dict[str, Any]) -> dict[str, Any]:
        return body | {'path': body['path'].removeprefix(self.prefix)}

    def read(self, rel: str) -> dict[str, Any]:
        try:
            return self._shown(self.repo.read(self._full(rel)))
        except MemoryFileNotFound as exc:
            raise MemoryFileNotFound(rel) from exc

    def read_slice(self, rel: str, cap: int = SLICE_MAX_BYTES) -> tuple[str, bool] | None:
        return self.repo.read_slice(self._full(rel), cap)

    def tree(self) -> list[dict[str, Any]]:
        return [self._shown(f) for f in self.repo.tree() if f['path'].startswith(self.prefix)]

    def write(self, rel: str, content: str, if_match: str | None, by: Writer) -> dict[str, Any]:
        try:
            return self._shown(self.repo.write(self._full(rel), content, if_match, by))
        except MemoryConflict as exc:
            raise MemoryConflict(self._shown(exc.current)) from exc
        except MemoryFileNotFound as exc:
            raise MemoryFileNotFound(rel) from exc

    def append(self, rel: str, line: str, by: str, max_appends: int | None = None) -> None:
        self.repo.append(self._full(rel), line, by, max_appends)


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


def _too_long() -> JSONResponse:
    return _error(400, 'bad_request', 'That memory file is too long. Keep it under 1 MB.')


def _too_big(rel: str) -> JSONResponse:
    message = f'{rel} is over 1 MB, too big to open in the app. Shorten it on the NUC.'
    return _error(400, 'memory_file_too_big', message)


def _file(body: dict[str, Any], status: int = 200) -> JSONResponse:
    # HTTP quotes an ETag; the body's `etag` stays bare.
    return JSONResponse(body, status_code=status, headers={'ETag': f'"{body["etag"]}"'})


async def _capped_body(request: Request) -> bytes | None:
    """The request body, or None once it passes `MAX_BODY_BYTES`. Never reads more than that."""
    declared = request.headers.get('content-length', '')
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > MAX_BODY_BYTES:
            return None
    return bytes(raw)


def _repo(request: Request) -> AgentMemory | None:
    """The memory of the buddy the calling phone shows (`Device.agent_id`, app-api 0.8.1). None
    while there is no buddy."""
    repo: MemoryRepo = request.app.state.memory
    agent_id = request.app.state.memory_agent_of(current_device(request))
    return repo.for_agent(agent_id) if agent_id is not None else None


def _etag(header: str | None) -> str | None:
    """Accept `abc`, `"abc"` and `W/"abc"`: the body's etag is bare, the header quotes it."""
    if header is None:
        return None
    return header.strip().removeprefix('W/').strip('"')


@router.get('/memory')
def get_memory_tree(request: Request) -> Any:
    mine = _repo(request)
    if mine is None:
        return {'files': []}
    try:
        return {'files': mine.tree()}
    except MemoryUnavailable as exc:
        return _error(503, 'memory_unavailable', f'The memory cannot be read: {exc}')


@router.get('/memory/files/{path:path}')
def get_memory_file(request: Request, path: str) -> Any:
    mine = _repo(request)
    if mine is None:
        return _not_found(path)
    try:
        return _file(mine.read(path))
    except InvalidMemoryPath:
        return _invalid_path(path)
    except MemoryFileTooBig:
        return _too_big(path)
    except MemoryFileNotFound:
        return _not_found(path)
    except MemoryUnavailable as exc:
        return _error(503, 'memory_unavailable', f'The memory cannot be read: {exc}')


@router.put('/memory/files/{path:path}')
async def put_memory_file(request: Request, path: str) -> Any:
    raw = await _capped_body(request)
    if raw is None:
        return _too_long()
    try:
        body = MemoryWrite.model_validate_json(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = '.'.join(str(p) for p in first['loc']) or 'the body'
        return _error(400, 'bad_request', f'Send JSON like {{"content": "…"}} ({where}: '
                                          f'{first["msg"]}).')  # fmt: skip
    if len(body.content.encode('utf-8')) > MAX_FILE_BYTES:
        return _too_long()
    if_match = _etag(request.headers.get('If-Match'))
    repo = _repo(request)
    if repo is None:
        return _error(404, 'memory_file_not_found', 'You have no helper yet, so no memory to save.')
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
    except MemoryFileTooBig:
        return _too_big(path)
    except MemoryUnavailable as exc:
        return _unavailable(exc)
    return _file(saved)


def _folder(session: Session, agent: Agent | None) -> str | None:
    """The agent's folder: its slug. One without a slug (made by an older `agent create`) gets
    it now, for good, as `marcel-hub setup` would give it (the owner's first agent `marcel`), so
    its memory never splits between two folders."""
    if agent is None:
        return None
    if agent.slug is None:
        from marcel_hub.users.store import OWNER_AGENT_SLUG, slug_for  # users/ builds on memory's

        taken = session.exec(select(Agent).where(Agent.slug == OWNER_AGENT_SLUG)).first()
        first = session.exec(
            select(Agent)
            .where(Agent.owner_user_id == OWNER_USER_ID, col(Agent.archived_at).is_(None))
            .order_by(col(Agent.id))
        ).first()
        is_owners = first is not None and first.id == agent.id
        agent.slug = (
            OWNER_AGENT_SLUG if is_owners and taken is None else slug_for(session, agent.name)
        )
        session.add(agent)
        session.commit()
        session.refresh(agent)
    return agent.slug


def install(app: FastAPI) -> MemoryRepo:
    """Put the memory repo on `app.state.memory` and mount `/api/memory`. Git runs on first use.
    The repo learns each agent's folder, and the owner's, from the database."""
    engine = app.state.engine

    def folder_of(agent_id: str) -> str | None:
        with Session(engine) as s:
            return _folder(s, s.get(Agent, agent_id))

    def first_agent(user_id: str) -> Agent | None:
        with Session(engine) as s:
            return s.exec(
                select(Agent)
                .where(Agent.owner_user_id == user_id, col(Agent.archived_at).is_(None))
                .order_by(col(Agent.id))
            ).first()

    def owner_folder() -> str | None:
        found = first_agent(OWNER_USER_ID)
        if found is None:
            return None
        with Session(engine) as s:
            return _folder(s, s.get(Agent, found.id))

    def agent_of(device: Device) -> str | None:
        with Session(engine) as s:
            return buddy_of(s, device)

    repo = MemoryRepo(
        app.state.settings.data_dir / 'memory', folder_of=folder_of, owner_folder=owner_folder
    )
    app.state.memory = repo
    app.state.memory_agent_of = agent_of
    app.include_router(router)
    return repo
