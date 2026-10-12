"""Workspaces: one clone per repo and one git worktree per task, for `repo` spawns.

`~/marcel/workspaces/<repo>` is cloned once. Then, also once, the runner accepts Claude Code's
trust prompt there (worktrees inherit trust from their main repo, SP2) and installs the marcel
plugin at **local** scope, so nothing tracked in someone else's repo changes. Each task gets a
worktree, `~/marcel/workspaces/<repo>-wt/<task-id>`, on branch `marcel/<task-id>-<slug>`.

Repo URLs, task ids, slugs and base branches come from users. Every one is checked against a
narrow pattern before git sees it, every git call puts `--` before its paths, and every path is
checked to stay inside the workspaces root, both when it is made and when it is removed.
"""

import contextlib
import dataclasses
import json
import os
import pty
import re
import select
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from marcel_runner.claude_env import scrubbed_env
from marcel_runner.sessions import SessionError, Sessions, parse_spawn

PLUGIN = 'marcel@marcel-local'
LOCAL_SETTINGS = '/.claude/settings.local.json'  # where `--scope local` writes, never tracked
READY = 'marcel-ready'  # in the clone's .git folder: trusted and plugin installed
GIT_TIMEOUT = 600.0
# On every git call: a worker could plant a hook or an fsmonitor command in the shared .git, and
# the runner must never run it.
GIT_SAFE = (
    '-c',
    'core.hooksPath=/dev/null',
    '-c',
    'core.fsmonitor=false',
    '-c',
    'protocol.ext.allow=never',
)

REPO_KEYS = {'url', 'base', 'task_id', 'slug'}
HOST = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252}[A-Za-z0-9])?(?::[0-9]{1,5})?')
SEGMENT = re.compile(r'[A-Za-z0-9_.~-]{1,100}')
REPO_NAME = re.compile(r'[A-Za-z0-9_][A-Za-z0-9._-]{0,99}')
TASK_ID = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,59}')
SLUG = re.compile(r'[a-z0-9-]{1,40}')  # contracts/runner-api.yaml `RepoSpec.slug`
BASE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9._/-]{0,99}')
OPTION = re.compile(r'^\s*(?P<mark>[❯›>])?\s*(?P<num>[1-9])\.\s+(?P<text>\S.*?)\s*$')
YES = re.compile(r'Yes,?\s+I\s+trust\s+this\s+folder', re.I)
NO = re.compile(r'No\b', re.I)
ANSI = re.compile(rb'\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07')
MOVE = re.compile(rb'\x1b\[[0-9;]*[HfBEd]')  # cursor moves start a new screen line
DOWN, UP, ENTER, CTRL_C = b'\x1b[B', b'\x1b[A', b'\r', b'\x03'


def root_from_env(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get('MARCEL_WORKSPACES') or Path.home() / 'marcel' / 'workspaces')


def _bad(message: str) -> SessionError:
    return SessionError(400, 'bad_request', message)


@dataclass(frozen=True)
class RepoSpec:
    url: str
    name: str  # the folder under the workspaces root, from the URL's last part
    task_id: str
    slug: str
    base: str | None

    @property
    def branch(self) -> str:
        return f'marcel/{self.task_id}-{self.slug}'


def parse_repo(value: Any) -> RepoSpec:
    """Check a `RepoSpec`. Only `https://host/path` URLs without a user or password are cloned:
    no other transport (`ext::`, `file://`, ssh), and nothing git could read as an option."""
    if not isinstance(value, dict):
        raise _bad('repo must be an object with url, task_id and slug.')
    unknown = sorted(set(value) - REPO_KEYS)
    if unknown:
        raise _bad(f'Unknown repo field(s): {", ".join(unknown)}.')
    missing = [k for k in ('url', 'task_id', 'slug') if k not in value]
    if missing:
        raise _bad(f'Missing repo field(s): {", ".join(missing)}.')
    url, task_id, slug, base = value['url'], value['task_id'], value['slug'], value.get('base')
    name = _repo_name(url)
    if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
        raise _bad(
            'repo.task_id must be 1–60 letters, digits, dashes or underscores, '
            'not starting with a dash.'
        )
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise _bad('repo.slug must be 1–40 lowercase letters, digits or dashes.')
    if base is not None and not _good_base(base):
        raise _bad('repo.base must be a branch name like "main".')
    return RepoSpec(url, name, task_id, slug, base)


def _repo_name(url: Any) -> str:
    words = 'repo.url must be an https:// address like https://github.com/owner/repo.'
    if not isinstance(url, str) or len(url) > 300 or not url.startswith('https://'):
        raise _bad(words)
    try:
        parts = urlsplit(url)
    except ValueError:
        raise _bad(words) from None
    if parts.username is not None or parts.password is not None:
        raise _bad('repo.url must not hold a user name or password.')
    if parts.query or parts.fragment or not HOST.fullmatch(parts.netloc):
        raise _bad(words)
    segments = parts.path.strip('/').split('/')
    if not segments or not all(SEGMENT.fullmatch(s) and s not in ('.', '..') for s in segments):
        raise _bad(words)
    name = segments[-1].removesuffix('.git')
    # `<name>-wt` holds the worktrees of `<name>`, so a repo with that name would collide.
    if not REPO_NAME.fullmatch(name) or name.endswith('-wt'):
        raise _bad(f'The runner cannot use "{segments[-1]}" as a folder name for this repo.')
    return name


def _good_base(base: Any) -> bool:
    return (
        isinstance(base, str)
        and BASE.fullmatch(base) is not None
        and '..' not in base
        and '//' not in base
        and not base.endswith(('/', '.', '.lock'))
    )


def inside(root: Path, path: Path) -> bool:
    """True when `path`, with every link followed, is strictly below `root`."""
    real_root = root.resolve()
    real = path.resolve()
    return real != real_root and real.is_relative_to(real_root)


def trust_menu(screen: str) -> tuple[int, int] | None:
    """(the highlighted option, the "Yes, I trust this folder" option) of the last trust dialog
    on `screen`. None until the dialog is fully drawn with exactly one highlighted option and both
    a "Yes, I trust this folder" and a "No" option, so the runner never presses a key blind."""
    groups: list[list[re.Match[str]]] = []
    last = -2
    lines = [line for line in screen.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        m = OPTION.match(line)
        if m is None:
            continue
        if i != last + 1:
            groups.append([])
        groups[-1].append(m)
        last = i
    for group in reversed(groups):
        yes = [m for m in group if YES.match(m['text'])]
        if not yes:
            continue
        marked = [m for m in group if m['mark']]
        if len(yes) != 1 or len(marked) != 1 or not any(NO.match(m['text']) for m in group):
            return None
        return int(marked[0]['num']), int(yes[0]['num'])
    return None


def trust_problem(home: Path, cwd: Path) -> str | None:
    """Why `~/.claude.json` does not show `cwd` trusted with no MCP server of its own enabled.
    None when it does."""
    try:
        data = json.loads((home / '.claude.json').read_text('utf-8'))
    except (OSError, ValueError):
        return f'{home / ".claude.json"} could not be read.'
    projects = data.get('projects') if isinstance(data, dict) else None
    projects = projects if isinstance(projects, dict) else {}
    entry = projects.get(str(cwd)) or projects.get(str(cwd.resolve()))
    if not isinstance(entry, dict) or entry.get('hasTrustDialogAccepted') is not True:
        return 'Claude did not save the folder as trusted.'
    if entry.get('enabledMcpjsonServers') or entry.get('enableAllProjectMcpServers'):
        return "The repo's own MCP servers were enabled, which the runner never allows."
    return None


def _screen(buf: bytes) -> str:
    return ANSI.sub(b'', MOVE.sub(b'\n', buf.replace(b'\r\n', b'\n').replace(b'\r', b'\n'))).decode(
        'utf-8', 'replace'
    )


def accept_trust(
    claude: Path, cwd: Path, env: dict[str, str], *, timeout: float = 40.0, settle: float = 4.0
) -> None:
    """Accept Claude Code's trust prompt for `cwd` headlessly (SP2,
    `scratch/spikes/SP2-worker-control/accept_trust.py`). Runs an interactive `claude` on a pty.

    It reads the screen and presses keys only for the trust dialog: arrows to "Yes, I trust this
    folder" (the default is "No, exit"), then Enter once the screen shows Yes highlighted. It never
    answers anything else (a repo's `.mcp.json` brings an "use these MCP servers?" dialog next) and
    leaves with Ctrl-C. Then `~/.claude.json` must show the folder trusted and none of the repo's
    MCP servers enabled. Anything else raises `SessionError` (`not_trusted`)."""
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [str(claude)],
        cwd=cwd,
        env={**env, 'TERM': 'xterm-256color'},
        stdin=slave,
        stdout=slave,
        stderr=slave,
        start_new_session=True,
    )
    os.close(slave)
    buf = b''
    seen = 0  # where the screen after the last key press starts
    moved = answered = False
    answered_at = 0.0
    problem = 'Claude never showed its trust dialog.'
    end = time.monotonic() + timeout
    try:
        while time.monotonic() < end:
            if answered and time.monotonic() - answered_at > settle:
                break
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    chunk = b''
                if not chunk:
                    break
                buf += chunk
            if answered:
                continue
            menu = trust_menu(_screen(buf[seen:]))
            if menu is None:
                continue
            selected, yes = menu
            if selected == yes:
                os.write(master, ENTER)
                answered, answered_at = True, time.monotonic()
            elif moved:  # the arrows did not reach Yes: never press Enter on anything else
                break
            else:
                os.write(master, (DOWN if yes > selected else UP) * abs(yes - selected))
                moved, seen = True, len(buf)
                problem = 'The runner could not move to "Yes, I trust this folder".'
        with contextlib.suppress(OSError):  # claude may have exited already
            os.write(master, CTRL_C)  # leave without answering anything else
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=0.5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        os.close(master)
    if answered:
        problem = trust_problem(Path(env.get('HOME') or Path.home()), cwd)
        if problem is None:
            return
    raise SessionError(
        422,
        'not_trusted',
        f'The runner could not trust {cwd}: {problem} '
        'Remove the folder to have the runner clone it again.',
        _screen(buf).strip()[-500:] or None,
    )


Trust = Callable[[Path, Path, dict[str, str]], None]


class Workspaces:
    """Clones and worktrees under `root`. Thread-safe: one lock per repo around its git changes."""

    def __init__(
        self,
        root: Path,
        claude: Path,
        home: Path,
        *,
        trust: Trust = accept_trust,
        timeout: float = GIT_TIMEOUT,
    ) -> None:
        self.root = root
        self.claude = claude
        self.home = home
        self.trust = trust
        self.timeout = timeout
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # --- paths ----------------------------------------------------------------------------

    def _checked(self, path: Path) -> Path:
        if not inside(self.root, path):
            raise SessionError(
                400, 'bad_request', f'{path} is outside {self.root}, so the runner left it alone.'
            )
        return path

    def clone_dir(self, spec: RepoSpec) -> Path:
        return self._checked(self.root / spec.name)

    def worktree_dir(self, spec: RepoSpec) -> Path:
        return self._checked(self.root / f'{spec.name}-wt' / spec.task_id)

    def _lock_for(self, spec: RepoSpec) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(spec.name, threading.Lock())

    # --- running git and claude -----------------------------------------------------------

    def _env(self) -> dict[str, str]:
        return {**scrubbed_env(os.environ, self.home), 'GIT_TERMINAL_PROMPT': '0'}

    def _exec(
        self, argv: list[str], cwd: Path | None, what: str
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                argv,
                cwd=cwd,
                env=self._env(),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            code = 'git_timeout' if argv[0] == 'git' else 'claude_timeout'
            raise SessionError(503, code, f'{what} took too long. Try again later.') from None
        except OSError as e:
            raise SessionError(422, 'clone_failed', f'{what} could not run.', str(e)) from None

    def _run(self, argv: list[str], cwd: Path | None, what: str) -> str:
        p = self._exec(argv, cwd, what)
        if p.returncode != 0:
            said = (p.stderr or p.stdout).strip() or f'exit code {p.returncode}'
            raise SessionError(422, 'clone_failed', f'{what} failed.', said)
        return p.stdout

    def git(self, cwd: Path | None, *args: str, what: str = 'git') -> str:
        return self._run(['git', *GIT_SAFE, *args], cwd, what)

    # --- the clone ------------------------------------------------------------------------

    def ensure(self, spec: RepoSpec) -> Path:
        """The clone for `spec`, made, trusted and given the plugin once."""
        with self._lock_for(spec):
            return self._ensure(spec)

    def _ensure(self, spec: RepoSpec) -> Path:
        clone = self.clone_dir(spec)
        if clone.exists():
            origin = self.git(
                clone, 'config', '--get', 'remote.origin.url', what='Reading the clone'
            )
            if origin.strip() != spec.url:
                raise SessionError(
                    422,
                    'clone_failed',
                    f'{clone} already holds a clone of {origin.strip()}, not {spec.url}.',
                )
        else:
            self.root.mkdir(parents=True, exist_ok=True)
            partial = self._checked(self.root / f'.{spec.name}.partial')
            if partial.exists():
                shutil.rmtree(partial)
            self.git(None, 'clone', '--', spec.url, str(partial), what=f'Cloning {spec.url}')
            partial.rename(clone)
        git_dir = clone / '.git'
        if not (git_dir / READY).exists():
            self._exclude_local_settings(git_dir)
            self.trust(self.claude, clone, scrubbed_env(os.environ, self.home))
            self._run(
                [str(self.claude), 'plugin', 'install', PLUGIN, '--scope', 'local'],
                clone,
                'Installing the marcel plugin',
            )
            changed = self.git(clone, 'status', '--porcelain', what='Checking the clone').strip()
            if changed:
                raise SessionError(
                    422,
                    'clone_failed',
                    f'Setting up {clone} changed files git tracks there, so the runner stopped.',
                    changed,
                )
            (git_dir / READY).write_text('trusted, plugin installed at local scope\n')
        return clone

    @staticmethod
    def _exclude_local_settings(git_dir: Path) -> None:
        """Keep `.claude/settings.local.json` out of `git status` in this clone only."""
        exclude = git_dir / 'info' / 'exclude'
        exclude.parent.mkdir(parents=True, exist_ok=True)
        text = exclude.read_text('utf-8') if exclude.exists() else ''
        if LOCAL_SETTINGS not in text.splitlines():
            prefix = '' if not text or text.endswith('\n') else '\n'
            exclude.write_text(f'{text}{prefix}{LOCAL_SETTINGS}\n', 'utf-8')

    # --- worktrees ------------------------------------------------------------------------

    def add_worktree(self, spec: RepoSpec) -> tuple[Path, bool]:
        """The task's worktree on `spec.branch`. Returns (path, made now). An existing worktree
        for the task is used again only if it is on the task's branch."""
        with self._lock_for(spec):
            clone = self._ensure(spec)
            path = self.worktree_dir(spec)
            if path.exists():
                head = self.git(
                    path, 'rev-parse', '--abbrev-ref', 'HEAD', what='Reading a worktree'
                )
                if head.strip() != spec.branch:
                    raise SessionError(
                        422, 'clone_failed', f'{path} exists but is not on {spec.branch}.'
                    )
                return path, False
            self.git(clone, 'fetch', '--prune', 'origin', what='Fetching')
            exists = self._has_branch(clone, spec.branch)
            if exists:
                args = ['worktree', 'add', '--', str(path), spec.branch]
            else:
                start = f'origin/{spec.base}' if spec.base else 'origin/HEAD'
                args = ['worktree', 'add', '-b', spec.branch, '--', str(path), start]
            path.parent.mkdir(parents=True, exist_ok=True)
            self.git(clone, *args, what='Making the task worktree')
            return path, not exists

    def _has_branch(self, clone: Path, branch: str) -> bool:
        ref = f'refs/heads/{branch}'
        argv = ['git', *GIT_SAFE, 'show-ref', '--verify', '--quiet', ref]
        return self._exec(argv, clone, 'Looking up the branch').returncode == 0

    def remove_worktree(self, spec: RepoSpec, *, delete_branch: bool = False) -> None:
        """Remove the task's worktree (and, if asked, its branch). Never touches anything
        outside the workspaces root: the path is checked with every link followed."""
        with self._lock_for(spec):
            clone = self.clone_dir(spec)
            path = self.worktree_dir(spec)
            if path.exists():
                self.git(clone, 'worktree', 'remove', '--force', '--', str(path), what='Removing')
            self.git(clone, 'worktree', 'prune', what='Pruning worktrees')
            if delete_branch and self._has_branch(clone, spec.branch):
                self.git(clone, 'branch', '-D', '--', spec.branch, what='Deleting the branch')

    # --- a `repo` spawn -------------------------------------------------------------------

    def spawn(self, sessions: Sessions, raw: bytes) -> dict[str, Any] | None:
        """`POST /sessions` with `repo`: the worktree, then an ordinary spawn in it. None when
        the body has no `repo`, so `Sessions` answers it. A failed spawn removes the worktree
        it made (and the branch, when this spawn made it)."""
        try:
            body = json.loads(raw or b'null')
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(body, dict) or 'repo' not in body or 'cwd' in body:
            return None
        spec = parse_repo(body['repo'])
        rest = {k: v for k, v in body.items() if k != 'repo'}
        # Check the rest of the body before any git work, with a stand-in cwd.
        req = parse_spawn(json.dumps({**rest, 'cwd': '/'}).encode())
        path, made = self.add_worktree(spec)
        try:
            spawned = sessions.spawn(dataclasses.replace(req, cwd=path))
        except SessionError:
            if made:
                with contextlib.suppress(SessionError):
                    self.remove_worktree(spec, delete_branch=True)
            raise
        return {**spawned, 'branch': spec.branch}
