"""Workspaces: clone once, trust once, plugin at local scope, a worktree per task.

Everything runs against a local bare repo in a temp folder. Git rewrites
`https://git.example.test/` to that folder (`url.<base>.insteadOf` in a temp global git config),
so the runner's https-only check is the real one and nothing reaches the network.

`claude` for the trust prompt and `plugin install` is a small script of this file's own (the fake
shim fakes neither): it shows the trust dialog with "No, exit" first, and records "trusted" only
when it gets a down arrow before Enter, as the real dialog would choose "Yes".
"""

import functools
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import apart
from test_sessions import Rig

from marcel_runner.server import serve
from marcel_runner.sessions import SessionError, Sessions
from marcel_runner.usage import UsageProbe
from marcel_runner.workspaces import (
    LOCAL_SETTINGS,
    READY,
    RepoSpec,
    Workspaces,
    accept_trust,
    inside,
    parse_repo,
    root_from_env,
)

URL = 'https://git.example.test/shbunder/marcel'
TRACKED = {'README.md': '# marcel\n', '.gitignore': '*.pyc\n', 'app.py': 'print(1)\n'}

CLAUDE = """#!{python}
import json, os, sys
args = sys.argv[1:]
with open({log!r}, 'a') as f:
    f.write(json.dumps({{'argv': sys.argv, 'cwd': os.getcwd()}}) + '\\n')
mode = open({mode!r}).read().split() if os.path.exists({mode!r}) else []
if args[:2] == ['plugin', 'install']:
    os.makedirs('.claude', exist_ok=True)
    with open('.claude/settings.local.json', 'w') as f:
        json.dump({{'enabledPlugins': {{args[2]: True}}}}, f)
    if 'install-touches-tracked' in mode:
        open('README.md', 'a').write('changed by the install\\n')
    if 'install-fails' in mode:
        print('marketplace marcel-local not found', file=sys.stderr); sys.exit(1)
    sys.exit(0)
if not args:
    if 'no-dialog' in mode:
        print('Welcome to Claude Code'); sys.stdin.readline(); sys.exit(0)
    print(' Do you trust the files in this folder?')
    print(' > 1. No, exit')
    print('   2. Yes, I trust this folder', flush=True)
    answer = sys.stdin.readline()
    if '\\x1b[B' not in answer:
        sys.exit(0)  # "No, exit"
    open({trusted!r}, 'a').write(os.getcwd() + '\\n')
    sys.stdin.readline()  # /exit
    sys.exit(0)
print('not faked here: ' + ' '.join(args), file=sys.stderr); sys.exit(2)
"""


class Ws:
    """A bare repo, a git config that points https://git.example.test/ at it, and a claude."""

    def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp = tmp
        self.root = tmp / 'workspaces'
        self.home = tmp / 'home'
        self.home.mkdir()
        remotes = tmp / 'remotes'
        self.bare = remotes / 'shbunder' / 'marcel'
        gitconfig = tmp / 'gitconfig'
        gitconfig.write_text(
            '[user]\n\tname = Test\n\temail = test@example.test\n'
            '[init]\n\tdefaultBranch = main\n'
            f'[url "file://{remotes}/"]\n\tinsteadOf = https://git.example.test/\n'
        )
        monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(gitconfig))
        monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
        self._make_bare()
        self.log = tmp / 'claude-calls.jsonl'
        self.mode_file = tmp / 'claude-mode'
        self.trusted = tmp / 'trusted'
        self.claude = tmp / 'claude'
        self.claude.write_text(
            CLAUDE.format(
                python=sys.executable,
                log=str(self.log),
                mode=str(self.mode_file),
                trusted=str(self.trusted),
            )
        )
        self.claude.chmod(0o755)
        self.ws = self.make()

    def make(self, **extra: Any) -> Workspaces:
        trust = functools.partial(accept_trust, timeout=10.0, settle=0.2)
        return Workspaces(self.root, self.claude, self.home, trust=trust, **extra)

    def _make_bare(self) -> None:
        seed = self.tmp / 'seed'
        seed.mkdir()
        git(seed, 'init', '-q')
        for name, text in TRACKED.items():
            (seed / name).write_text(text)
        git(seed, 'add', *TRACKED)
        git(seed, 'commit', '-qm', 'first')
        git(seed, 'branch', 'dev')
        (seed / 'dev.txt').write_text('dev only\n')
        git(seed, 'checkout', '-q', 'dev')
        git(seed, 'add', 'dev.txt')
        git(seed, 'commit', '-qm', 'dev')
        git(seed, 'checkout', '-q', 'main')
        self.bare.parent.mkdir(parents=True)
        git(None, 'clone', '-q', '--bare', str(seed), str(self.bare))

    def mode(self, *flags: str) -> None:
        self.mode_file.write_text(' '.join(flags))

    def calls(self, word: str) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [r for r in rows if word in r['argv'][1:2] or (not word and len(r['argv']) == 1)]


def git(cwd: Path | None, *args: str) -> str:
    return subprocess.run(
        ['git', *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Ws:
    return Ws(tmp_path, monkeypatch)


def spec(
    task_id: str = 'tsk_01J9ZQ5', slug: str = 'fix-flaky-login-test', **extra: Any
) -> RepoSpec:
    return parse_repo({'url': URL, 'task_id': task_id, 'slug': slug, **extra})


# --- done when: a fresh clone gets the plugin at local scope; tracked files unchanged ---------


def test_a_fresh_clone_is_trusted_and_gets_the_plugin_at_local_scope_with_git_clean(
    ws: Ws,
) -> None:
    clone = ws.ws.ensure(spec())
    assert clone == ws.root / 'marcel'
    (install,) = ws.calls('plugin')
    assert install['argv'][1:] == ['plugin', 'install', 'marcel@marcel-local', '--scope', 'local']
    assert install['cwd'] == str(clone.resolve())
    settings = json.loads((clone / '.claude' / 'settings.local.json').read_text())
    assert settings == {'enabledPlugins': {'marcel@marcel-local': True}}
    # The trust dialog was answered "Yes" (a down arrow from the default "No, exit").
    assert ws.trusted.read_text().split() == [str(clone.resolve())]
    # Nothing tracked changed, and nothing new shows up: the clone is exactly the repo.
    assert git(clone, 'status', '--porcelain') == ''
    assert git(clone, 'diff', 'HEAD') == ''
    assert sorted(git(clone, 'ls-files').split()) == sorted(TRACKED)
    for name, text in TRACKED.items():
        assert (clone / name).read_text() == text
    assert (clone / '.git' / READY).exists()


def test_a_clone_is_set_up_only_once(ws: Ws) -> None:
    ws.ws.ensure(spec())
    ws.ws.ensure(spec(task_id='tsk_2'))
    assert len(ws.calls('plugin')) == 1
    assert len(ws.trusted.read_text().split()) == 1


def test_the_local_settings_file_is_excluded_in_this_clone_only(ws: Ws) -> None:
    clone = ws.ws.ensure(spec())
    exclude = (clone / '.git' / 'info' / 'exclude').read_text().splitlines()
    assert exclude.count(LOCAL_SETTINGS) == 1
    assert not (clone / '.gitignore').read_text().count('settings.local')


def test_an_install_that_changes_tracked_files_fails_and_is_not_marked_ready(ws: Ws) -> None:
    """The clean-tree check is a control: without it this clone would be marked ready."""
    ws.mode('install-touches-tracked')
    with pytest.raises(SessionError) as e:
        ws.ws.ensure(spec())
    assert (e.value.status, e.value.code) == (422, 'clone_failed')
    assert 'README.md' in (e.value.detail or '')
    assert not (ws.root / 'marcel' / '.git' / READY).exists()


def test_a_failed_install_is_retried_on_the_next_spawn(ws: Ws) -> None:
    ws.mode('install-fails')
    with pytest.raises(SessionError) as e:
        ws.ws.ensure(spec())
    assert e.value.detail == 'marketplace marcel-local not found'
    ws.mode()
    ws.ws.ensure(spec())
    assert len(ws.calls('plugin')) == 2


def test_a_folder_holding_another_repo_is_never_used(ws: Ws) -> None:
    ws.ws.ensure(spec())
    other = parse_repo(
        {'url': 'https://git.example.test/someone/marcel', 'task_id': 't', 'slug': 's'}
    )
    with pytest.raises(SessionError) as e:
        ws.ws.ensure(other)
    assert e.value.code == 'clone_failed'
    assert 'already holds a clone of' in e.value.message


def test_a_failed_clone_leaves_nothing_half_made(ws: Ws) -> None:
    missing = parse_repo(
        {'url': 'https://git.example.test/nobody/nothing', 'task_id': 't', 'slug': 's'}
    )
    with pytest.raises(SessionError) as e:
        ws.ws.ensure(missing)
    assert e.value.code == 'clone_failed'
    assert not (ws.root / 'nothing').exists()
    (ws.root / '.nothing.partial').mkdir()  # a leftover from a crash is cleared on the next try
    with pytest.raises(SessionError):
        ws.ws.ensure(missing)
    assert not (ws.root / '.nothing.partial').exists()


# --- trust ---------------------------------------------------------------------------------------


def test_trust_fails_in_words_when_claude_never_asks(ws: Ws) -> None:
    ws.mode('no-dialog')
    (ws.tmp / 'somewhere').mkdir()
    with pytest.raises(SessionError) as e:
        accept_trust(ws.claude, ws.tmp / 'somewhere', dict(os.environ), timeout=3.0)
    assert (e.value.status, e.value.code) == (422, 'not_trusted')
    assert 'Welcome to Claude Code' in (e.value.detail or '')


def test_a_clone_that_cannot_be_trusted_is_not_marked_ready(ws: Ws) -> None:
    ws.mode('no-dialog')
    trust = functools.partial(accept_trust, timeout=3.0)
    with pytest.raises(SessionError) as e:
        Workspaces(ws.root, ws.claude, ws.home, trust=trust).ensure(spec())
    assert e.value.code == 'not_trusted'
    assert not (ws.root / 'marcel' / '.git' / READY).exists()
    assert ws.calls('plugin') == []


# --- done when: worktree create and cleanup against a local bare repo -------------------------


def test_a_task_gets_its_own_worktree_on_its_own_branch_and_cleanup_removes_it(ws: Ws) -> None:
    s = spec()
    path, made = ws.ws.add_worktree(s)
    assert made
    assert path == ws.root / 'marcel-wt' / 'tsk_01J9ZQ5'
    assert git(path, 'rev-parse', '--abbrev-ref', 'HEAD').strip() == (
        'marcel/tsk_01J9ZQ5-fix-flaky-login-test'
    )
    assert git(path, 'rev-parse', 'HEAD') == git(ws.bare, 'rev-parse', 'main')
    assert str(path.resolve()) in git(ws.root / 'marcel', 'worktree', 'list')
    assert git(path, 'status', '--porcelain') == ''

    ws.ws.remove_worktree(s)
    assert not path.exists()
    assert str(path.resolve()) not in git(ws.root / 'marcel', 'worktree', 'list')
    assert s.branch in git(ws.root / 'marcel', 'branch')  # the work on the branch is kept
    ws.ws.remove_worktree(s, delete_branch=True)
    assert s.branch not in git(ws.root / 'marcel', 'branch')


def test_a_worktree_starts_from_the_base_branch_when_given(ws: Ws) -> None:
    path, _ = ws.ws.add_worktree(spec(base='dev'))
    assert (path / 'dev.txt').exists()


def test_two_tasks_get_two_worktrees(ws: Ws) -> None:
    a, _ = ws.ws.add_worktree(spec(task_id='tsk_a'))
    b, _ = ws.ws.add_worktree(spec(task_id='tsk_b'))
    assert a != b and a.exists() and b.exists()


def test_an_existing_worktree_on_the_tasks_branch_is_used_again(ws: Ws) -> None:
    first, _ = ws.ws.add_worktree(spec())
    again, made = ws.ws.add_worktree(spec())
    assert (again, made) == (first, False)


def test_an_existing_branch_is_checked_out_not_reset(ws: Ws) -> None:
    s = spec()
    path, _ = ws.ws.add_worktree(s)
    (path / 'work.txt').write_text('done\n')
    git(path, 'add', 'work.txt')
    git(path, 'commit', '-qm', 'work')
    ws.ws.remove_worktree(s)
    path, made = ws.ws.add_worktree(s)
    assert not made
    assert (path / 'work.txt').exists()


def test_a_task_folder_on_another_branch_is_refused(ws: Ws) -> None:
    path, _ = ws.ws.add_worktree(spec())
    git(path, 'checkout', '-qb', 'other')
    with pytest.raises(SessionError) as e:
        ws.ws.add_worktree(spec())
    assert e.value.code == 'clone_failed'


# --- controls: what users send never escapes the workspaces root or reaches git as an option --


@pytest.mark.parametrize(
    ('repo', 'words'),
    [
        ('nope', 'repo must be an object'),
        ({'url': URL, 'task_id': 't'}, 'Missing repo field(s): slug'),
        ({'url': URL, 'task_id': 't', 'slug': 's', 'x': 1}, 'Unknown repo field(s): x'),
        ({'url': 'http://git.example.test/a/b', 'task_id': 't', 'slug': 's'}, 'https://'),
        ({'url': 'file:///etc', 'task_id': 't', 'slug': 's'}, 'https://'),
        ({'url': 'ext::sh -c touch% /tmp/x', 'task_id': 't', 'slug': 's'}, 'https://'),
        ({'url': 'git@github.com:a/b.git', 'task_id': 't', 'slug': 's'}, 'https://'),
        ({'url': '--upload-pack=touch /tmp/x', 'task_id': 't', 'slug': 's'}, 'https://'),
        ({'url': 'https://u:pw@git.example.test/a/b', 'task_id': 't', 'slug': 's'}, 'password'),
        ({'url': 'https://git.example.test/a/..', 'task_id': 't', 'slug': 's'}, 'https://'),
        ({'url': 'https://git.example.test/a/b?x=1', 'task_id': 't', 'slug': 's'}, 'https://'),
        ({'url': 'https://git.example.test/', 'task_id': 't', 'slug': 's'}, 'https://'),
        ({'url': 'https://-oProxy/a/b', 'task_id': 't', 'slug': 's'}, 'https://'),
        ({'url': 'https://git.example.test/a/.git', 'task_id': 't', 'slug': 's'}, 'folder name'),
        ({'url': 'https://git.example.test/a/x-wt', 'task_id': 't', 'slug': 's'}, 'folder name'),
        ({'url': 'https://git.example.test/a/-x', 'task_id': 't', 'slug': 's'}, 'folder name'),
        ({'url': 7, 'task_id': 't', 'slug': 's'}, 'https://'),
        ({'url': URL, 'task_id': '../../escape', 'slug': 's'}, 'repo.task_id'),
        ({'url': URL, 'task_id': '-x', 'slug': 's'}, 'repo.task_id'),
        ({'url': URL, 'task_id': 'a/b', 'slug': 's'}, 'repo.task_id'),
        ({'url': URL, 'task_id': 'a~1', 'slug': 's'}, 'repo.task_id'),
        ({'url': URL, 'task_id': 't', 'slug': 'Fix'}, 'repo.slug'),
        ({'url': URL, 'task_id': 't', 'slug': 'a..b'}, 'repo.slug'),
        ({'url': URL, 'task_id': 't', 'slug': 's', 'base': '-x'}, 'repo.base'),
        ({'url': URL, 'task_id': 't', 'slug': 's', 'base': '../x'}, 'repo.base'),
        ({'url': URL, 'task_id': 't', 'slug': 's', 'base': 'a..b'}, 'repo.base'),
        ({'url': URL, 'task_id': 't', 'slug': 's', 'base': 'a/'}, 'repo.base'),
        ({'url': URL, 'task_id': 't', 'slug': 's', 'base': 'x.lock'}, 'repo.base'),
        ({'url': URL, 'task_id': 't', 'slug': 's', 'base': 'a b'}, 'repo.base'),
    ],
)
def test_bad_repo_specs_are_400_before_git_runs(repo: Any, words: str) -> None:
    with pytest.raises(SessionError) as e:
        parse_repo(repo)
    assert (e.value.status, e.value.code) == (400, 'bad_request')
    assert words in e.value.message


def test_good_repo_specs_name_their_folder_and_branch() -> None:
    s = parse_repo(
        {'url': URL + '.git', 'task_id': 'tsk_01J9ZQ5', 'slug': 'fix-it', 'base': 'release/1.2'}
    )
    assert (s.name, s.branch, s.base) == ('marcel', 'marcel/tsk_01J9ZQ5-fix-it', 'release/1.2')


def test_a_path_outside_the_root_is_refused_even_when_the_patterns_are_bypassed(ws: Ws) -> None:
    """The second line of defence: `inside()` on every path, made or removed."""
    escape = RepoSpec(URL, 'marcel', '../../escape', 's', None)
    with pytest.raises(SessionError) as e:
        ws.ws.add_worktree(escape)
    assert (e.value.status, e.value.code) == (400, 'bad_request')
    assert not (ws.tmp / 'escape').exists()
    with pytest.raises(SessionError):
        ws.ws.clone_dir(RepoSpec(URL, '..', 't', 's', None))


def test_cleanup_never_removes_a_worktree_outside_the_root(ws: Ws) -> None:
    """A task folder that is a link to a worktree elsewhere: following it would delete that
    worktree. Delete the `inside()` check in `_checked()` and this goes red."""
    s = spec(task_id='tsk_link')
    clone = ws.ws.ensure(s)
    outside = ws.tmp / 'outside-wt'
    git(clone, 'worktree', 'add', '-q', '-b', 'elsewhere', str(outside))
    (ws.root / 'marcel-wt').mkdir()
    (ws.root / 'marcel-wt' / 'tsk_link').symlink_to(outside)
    with pytest.raises(SessionError) as e:
        ws.ws.remove_worktree(s, delete_branch=True)
    assert 'outside' in e.value.message
    assert (outside / 'README.md').exists()
    assert str(outside.resolve()) in git(clone, 'worktree', 'list')


def test_inside() -> None:
    root = Path('/srv/ws')
    assert inside(root, root / 'a' / 'b')
    assert not inside(root, root)
    assert not inside(root, root / '..' / 'x')
    assert not inside(root, Path('/srv/wsx'))


def test_root_from_env() -> None:
    assert root_from_env({'MARCEL_WORKSPACES': '/x/ws'}) == Path('/x/ws')
    assert root_from_env({}) == Path.home() / 'marcel' / 'workspaces'


# --- a `repo` spawn through the socket ----------------------------------------------------------


@pytest.fixture
def up(ws: Ws, tmp_path: Path) -> Iterator[tuple[Rig, Ws]]:
    (tmp_path / 'rig').mkdir()
    rig = Rig(tmp_path / 'rig')
    sessions = Sessions(
        rig.settings,
        rig.state,
        home=rig.fake.home,
        cgroup_of=apart,
        channel_wait=5.0,
        poll=0.02,
    )
    rig.sessions = sessions
    rig.srv = serve(
        rig.sock,
        rig.settings,
        cgroup_of=apart,
        usage=UsageProbe(rig.claude, env=rig.fake.env()),  # the fake's HOME, never the real one
        sessions=sessions,
        workspaces=ws.ws,
    )
    threading.Thread(target=rig.srv.serve_forever, daemon=True).start()
    yield rig, ws
    rig.close()


def repo_body(**repo: Any) -> dict[str, Any]:
    return {
        'role': 'worker',
        'name': 'tsk-01J9ZQ5',
        'model': 'sonnet',
        'prompt': 'Fix the flaky login test.',
        'repo': {'url': URL, 'task_id': 'tsk_01J9ZQ5', 'slug': 'fix-flaky-login-test', **repo},
    }


def test_a_repo_spawn_starts_the_session_in_the_tasks_worktree(up: tuple[Rig, Ws]) -> None:
    rig, ws = up
    status, body = rig.call('POST', '/sessions', repo_body())
    assert status == 201, body
    worktree = ws.root / 'marcel-wt' / 'tsk_01J9ZQ5'
    assert body['branch'] == 'marcel/tsk_01J9ZQ5-fix-flaky-login-test'
    assert body['cwd'] == str(worktree)
    (call,) = rig.calls_with('--bg')
    assert call['cwd'] == str(worktree.resolve())
    status, session = rig.call('GET', f'/sessions/{body["session_id"]}')
    assert (status, session['role']) == (200, 'worker')


def test_a_repo_spawn_that_fails_removes_its_worktree_and_branch(up: tuple[Rig, Ws]) -> None:
    rig, ws = up
    rig.mode('bg-fails')
    status, body = rig.call('POST', '/sessions', repo_body())
    assert (status, body['code']) == (422, 'exited')
    assert not (ws.root / 'marcel-wt' / 'tsk_01J9ZQ5').exists()
    assert 'marcel/tsk_01J9ZQ5' not in git(ws.root / 'marcel', 'branch')


def test_a_bad_spawn_body_with_a_repo_is_400_before_any_clone(up: tuple[Rig, Ws]) -> None:
    rig, ws = up
    status, body = rig.call('POST', '/sessions', {**repo_body(), 'name': '-x'})
    assert (status, body['code']) == (400, 'bad_request')
    status, body = rig.call('POST', '/sessions', repo_body(task_id='../../x'))
    assert (status, body['code']) == (400, 'bad_request')
    assert not ws.root.exists()


def test_a_body_with_both_cwd_and_repo_is_still_400(up: tuple[Rig, Ws]) -> None:
    rig, _ = up
    status, body = rig.call('POST', '/sessions', {**repo_body(), 'cwd': str(rig.work)})
    assert (status, body['code']) == (400, 'bad_request')
    status, body = rig.call('POST', '/sessions', raw=b'{nope')
    assert (status, body['code']) == (400, 'bad_request')


def test_without_workspaces_a_repo_spawn_is_still_501(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.start()
    try:
        status, body = rig.call('POST', '/sessions', repo_body())
        assert (status, body['code']) == (501, 'not_supported')
    finally:
        rig.close()
