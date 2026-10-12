"""A cloud create on a repo (S-03.9, runner-api 0.1.7): `POST /cloud/sessions` with
`repo: {url, base?}` runs `claude --cloud` in a worktree of the workspace clone on `base`.

The repo is test_workspaces' local bare repo (branches `main` and `dev`), reached through an
`https://git.example.test/` rewrite, so the https-only check is the real one and nothing reaches
the network. Trust and the plugin install use test_workspaces' small `claude`. The `claude
--cloud` here is a script of this file's own: it records the folder it runs in, that folder's
branch and commit, then prints the SP3 create line.
"""

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from test_cloud import RECORDED_ID, script
from test_workspaces import URL, Ws, git

from marcel_runner.cloud import Cloud
from marcel_runner.sessions import SessionError
from marcel_runner.workspaces import CLOUD_DIR, Workspaces, parse_cloud_repo, parse_repo

LINE = f'Created cloud session: T View: https://claude.ai/code/{RECORDED_ID}?from=cli&m=0 Resume'

# What `claude --cloud` sees: its folder, the branch and commit there, the remote, then the line.
SEES = (
    'import subprocess\n'
    'def run(*a):\n'
    '    return subprocess.run(["git", *a], capture_output=True, text=True).stdout.strip()\n'
    'seen = {"cwd": os.getcwd(), "branch": run("rev-parse", "--abbrev-ref", "HEAD"),\n'
    '        "head": run("rev-parse", "HEAD"), "origin": run("config", "remote.origin.url"),\n'
    '        "upstream": run("rev-parse", "--abbrev-ref", "@{u}"), "argv": args}\n'
    'open(SEEN, "a").write(json.dumps(seen) + "\\n")\n'
)


class CloudWs(Ws):
    """test_workspaces' repo and claude, plus a `claude --cloud` and a `Cloud` wired to both."""

    def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        super().__init__(tmp, monkeypatch)
        self.seen = tmp / 'seen.jsonl'
        self.cloud_bin = tmp / 'cloud-bin'
        self.cloud_bin.mkdir()
        self.answer()

    def answer(self, tail: str = f'print({LINE!r}, flush=True)\n') -> None:
        """What the `claude --cloud` does after it has looked around."""
        script(self.cloud_bin, SEES.replace('SEEN', repr(str(self.seen))) + tail)

    def cloud(self, ws: Workspaces | None = None, **extra: Any) -> Cloud:
        return Cloud(self.cloud_bin / 'claude', self.home, workspaces=ws or self.ws, **extra)

    def create(self, repo: Any, **extra: Any) -> dict[str, Any]:
        body = json.dumps({'prompt': 'Fix the flaky login test', 'repo': repo}).encode()
        return self.cloud(**extra).spawn(body)

    def sightings(self) -> list[dict[str, Any]]:
        if not self.seen.exists():
            return []
        return [json.loads(line) for line in self.seen.read_text().splitlines()]

    @property
    def folder(self) -> Path:
        return self.root / 'marcel-wt' / CLOUD_DIR

    def tip(self, branch: str) -> str:
        return git(self.bare, 'rev-parse', branch).strip()


@pytest.fixture
def cw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CloudWs:
    return CloudWs(tmp_path, monkeypatch)


def worktrees(clone: Path) -> list[str]:
    out = git(clone, 'worktree', 'list', '--porcelain')
    return [line.split(' ', 1)[1] for line in out.splitlines() if line.startswith('worktree ')]


# --- done when: a `repo` cloud create runs in a folder of the clone on the branch ------------


@pytest.mark.parametrize(('base', 'branch'), [('dev', 'dev'), (None, 'main'), ('main', 'main')])
def test_claude_cloud_runs_in_a_folder_of_the_clone_on_the_base_branch(
    cw: CloudWs, base: str | None, branch: str
) -> None:
    repo = {'url': URL} if base is None else {'url': URL, 'base': base}
    out = cw.create(repo)
    assert out == {
        'session_id': RECORDED_ID,
        'url': f'https://claude.ai/code/{RECORDED_ID}',
        'title': 'T',
    }
    [seen] = cw.sightings()
    assert seen['cwd'] == str(cw.folder.resolve())
    assert seen['branch'] == branch
    assert seen['head'] == cw.tip(branch)
    assert seen['origin'] == URL  # claude.ai reads the repo from the remote
    assert seen['upstream'] == f'origin/{branch}'
    assert seen['argv'] == ['--cloud', '--', 'Fix the flaky login test']
    # The folder lives only while claude runs. The clone is left clean, with no extra worktree.
    clone = cw.root / 'marcel'
    assert not cw.folder.exists()
    assert worktrees(clone) == [str(clone.resolve())]
    assert git(clone, 'status', '--porcelain') == ''
    assert len(cw.calls('plugin')) == 1  # the clone was set up as for a `repo` spawn


def test_each_create_fetches_what_origin_has_now(cw: CloudWs) -> None:
    cw.create({'url': URL, 'base': 'dev'})
    pusher = cw.tmp / 'pusher'
    git(None, 'clone', '-q', '-b', 'dev', str(cw.bare), str(pusher))
    (pusher / 'new.txt').write_text('new\n')
    git(pusher, 'add', 'new.txt')
    git(pusher, 'commit', '-qm', 'new')
    git(pusher, 'push', '-q', 'origin', 'dev')
    cw.create({'url': URL, 'base': 'dev'})
    first, second = cw.sightings()
    assert first['head'] != second['head'] == cw.tip('dev')


def test_without_repo_nothing_changes(cw: CloudWs) -> None:
    out = cw.cloud().spawn(b'{"prompt": "x"}')
    assert out['session_id'] == RECORDED_ID
    [seen] = cw.sightings()
    assert seen['cwd'] == str(cw.home.resolve())
    assert not cw.root.exists()  # no clone was made


# --- the folder is always removed -------------------------------------------------------------


def test_the_folder_is_removed_when_claude_prints_no_session(cw: CloudWs) -> None:
    cw.answer('print("Error: not logged in")\nsys.exit(1)\n')
    with pytest.raises(SessionError) as e:
        cw.create({'url': URL})
    assert (e.value.status, e.value.code) == (422, 'cloud_unparsed')
    assert not cw.folder.exists()
    assert worktrees(cw.root / 'marcel') == [str((cw.root / 'marcel').resolve())]


def test_a_timeout_is_503_and_the_folder_is_removed(cw: CloudWs) -> None:
    cw.answer('time.sleep(30)\n')
    started = time.monotonic()
    with pytest.raises(SessionError) as e:
        cw.create({'url': URL}, spawn_timeout=1.0)
    assert time.monotonic() - started < 10
    assert (e.value.status, e.value.code) == (503, 'claude_timeout')
    assert not cw.folder.exists()


def test_a_folder_left_by_a_crash_is_removed_first(cw: CloudWs) -> None:
    cw.create({'url': URL})
    cw.folder.mkdir(parents=True)
    (cw.folder / 'leftover.txt').write_text('from before the crash\n')
    cw.create({'url': URL, 'base': 'dev'})
    assert cw.sightings()[-1]['branch'] == 'dev'
    assert not cw.folder.exists()


def test_a_leftover_worktree_git_still_knows_is_removed_first(cw: CloudWs) -> None:
    clone = cw.ws.ensure(parse_cloud_repo({'url': URL}))
    git(clone, 'worktree', 'add', '-q', '--detach', str(cw.folder), 'origin/dev')
    cw.create({'url': URL})
    assert cw.sightings()[-1]['branch'] == 'main'
    assert worktrees(clone) == [str(clone.resolve())]


# --- errors -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    'repo',
    [
        'https://git.example.test/shbunder/marcel',
        {'url': 'http://git.example.test/shbunder/marcel'},
        {'url': 'ssh://git.example.test/shbunder/marcel'},
        {'url': 'file:///etc'},
        {'url': 'ext::sh -c touch% /tmp/pwned'},
        {'url': 'https://me:secret@git.example.test/shbunder/marcel'},
        {'url': 'https://git.example.test/shbunder/marcel?x=1'},
        {'url': 'https://git.example.test/shbunder/../marcel'},
        {'url': URL, 'base': '-b'},
        {'url': URL, 'base': '--upload-pack=touch pwned'},
        {'url': URL, 'base': 'a..b'},
        {'url': URL, 'base': 'main.lock'},
        {'url': URL, 'base': 7},
        {'url': URL, 'task_id': 't'},
        {'base': 'main'},
        [URL],
    ],
)
def test_a_bad_repo_is_400_before_anything_runs(cw: CloudWs, repo: Any) -> None:
    """Control: the url and base checks (shared with a `repo` spawn) run before git or claude."""
    with pytest.raises(SessionError) as e:
        cw.create(repo)
    assert (e.value.status, e.value.code) == (400, 'bad_request')
    assert not cw.root.exists()
    assert cw.sightings() == []


@pytest.mark.parametrize(
    'raw',
    [
        b'{"repo": {"url": "https://git.example.test/shbunder/marcel"}}',
        b'{"prompt": "x", "repo": {"url": "https://git.example.test/shbunder/marcel"}, "cwd": "/"}',
        b'{"prompt": "", "repo": {"url": "https://git.example.test/shbunder/marcel"}}',
    ],
)
def test_a_bad_body_with_a_repo_is_400_through_the_route(cw: CloudWs, raw: bytes) -> None:
    status, body = cw.cloud().route('POST', '/cloud/sessions', raw)
    assert (status, body['code']) == (400, 'bad_request')
    assert not cw.root.exists()


def test_a_timeout_is_503_through_the_route(cw: CloudWs) -> None:
    cw.answer('time.sleep(30)\n')
    raw = json.dumps({'prompt': 'x', 'repo': {'url': URL}}).encode()
    status, body = cw.cloud(spawn_timeout=1.0).route('POST', '/cloud/sessions', raw)
    assert (status, body['code']) == (503, 'claude_timeout')
    assert body['message'].startswith('Claude did not create the cloud session within 1 s.')


def test_a_repo_that_does_not_exist_is_clone_failed(cw: CloudWs) -> None:
    with pytest.raises(SessionError) as e:
        cw.create({'url': 'https://git.example.test/nobody/nothing'})
    assert (e.value.status, e.value.code) == (422, 'clone_failed')
    assert e.value.detail  # git's own words
    assert cw.sightings() == []


def test_a_base_that_does_not_exist_is_clone_failed(cw: CloudWs) -> None:
    with pytest.raises(SessionError) as e:
        cw.create({'url': URL, 'base': 'nope'})
    assert (e.value.status, e.value.code) == (422, 'clone_failed')
    assert 'nope' in (e.value.detail or '')
    assert cw.sightings() == []
    assert not cw.folder.exists()


def test_a_git_step_that_takes_too_long_is_git_timeout(cw: CloudWs) -> None:
    with pytest.raises(SessionError) as e:
        cw.create({'url': URL}, ws=cw.make(timeout=0.000001))
    assert (e.value.status, e.value.code) == (503, 'git_timeout')
    assert cw.sightings() == []


def test_a_clone_that_cannot_be_trusted_is_clone_failed(cw: CloudWs) -> None:
    def refuse(claude: Path, cwd: Path, env: dict[str, str]) -> None:
        raise SessionError(422, 'not_trusted', f'The runner could not trust {cwd}.', 'screen')

    ws = Workspaces(cw.root, cw.claude, cw.home, trust=refuse)
    with pytest.raises(SessionError) as e:
        cw.create({'url': URL}, ws=ws)
    assert (e.value.status, e.value.code, e.value.detail) == (422, 'clone_failed', 'screen')
    assert e.value.message.startswith('The runner could not trust')
    assert cw.sightings() == []


def test_a_runner_without_workspaces_refuses_a_repo(cw: CloudWs) -> None:
    cloud = Cloud(cw.cloud_bin / 'claude', cw.home)
    with pytest.raises(SessionError) as e:
        cloud.spawn(json.dumps({'prompt': 'x', 'repo': {'url': URL}}).encode())
    assert (e.value.status, e.value.code) == (501, 'not_supported')


def test_a_default_branch_git_cannot_tell_is_clone_failed(cw: CloudWs) -> None:
    clone = cw.ws.ensure(parse_cloud_repo({'url': URL}))
    git(clone, 'symbolic-ref', '--delete', 'refs/remotes/origin/HEAD')
    # `remote set-head --auto` asks origin; a bare repo with a broken HEAD answers nothing.
    (cw.bare / 'HEAD').write_text('ref: refs/heads/gone\n')
    with pytest.raises(SessionError) as e:
        cw.create({'url': URL})
    assert (e.value.status, e.value.code) == (422, 'clone_failed')
    assert cw.sightings() == []


def test_a_missing_origin_head_is_set_again_from_origin(cw: CloudWs) -> None:
    clone = cw.ws.ensure(parse_cloud_repo({'url': URL}))
    git(clone, 'symbolic-ref', '--delete', 'refs/remotes/origin/HEAD')
    cw.create({'url': URL})
    assert cw.sightings()[-1]['branch'] == 'main'


# --- git safety, paths and the lock ---------------------------------------------------------


def test_no_hook_planted_in_the_clone_ever_runs(cw: CloudWs) -> None:
    """Control: every git call of a cloud create disables hooks (`core.hooksPath=/dev/null`)."""
    clone = cw.ws.ensure(parse_cloud_repo({'url': URL}))
    ran = cw.tmp / 'hook-ran'
    for hook in ('post-checkout', 'reference-transaction', 'post-index-change'):
        path = clone / '.git' / 'hooks' / hook
        path.write_text(f'#!/bin/sh\necho {hook} >> {ran}\n')
        path.chmod(0o755)
    cw.create({'url': URL, 'base': 'dev'})
    cw.create({'url': URL})
    assert not ran.exists()


def test_a_worktrees_folder_that_links_outside_the_root_is_refused(cw: CloudWs) -> None:
    """Control: the cloud folder must stay inside the workspaces root, links followed."""
    cw.ws.ensure(parse_cloud_repo({'url': URL}))
    outside = cw.tmp / 'outside'
    outside.mkdir()
    (cw.root / 'marcel-wt').symlink_to(outside)
    with pytest.raises(SessionError) as e:
        cw.create({'url': URL})
    assert (e.value.status, e.value.code) == (400, 'bad_request')
    assert list(outside.iterdir()) == []
    assert cw.sightings() == []


def test_a_cloud_folder_that_is_a_link_is_dropped_not_followed(cw: CloudWs) -> None:
    clone = cw.ws.ensure(parse_cloud_repo({'url': URL}))
    target = cw.root / 'keep'
    target.mkdir()
    (target / 'precious.txt').write_text('keep me\n')
    cw.folder.parent.mkdir(parents=True)
    cw.folder.symlink_to(target)
    cw.create({'url': URL})
    assert (target / 'precious.txt').read_text() == 'keep me\n'
    assert worktrees(clone) == [str(clone.resolve())]


def test_a_repo_spawn_of_the_same_repo_waits_until_claude_cloud_is_done(cw: CloudWs) -> None:
    """Control: the per-repo lock is held while `claude --cloud` runs in the folder."""
    marks = cw.tmp / 'marks'
    cw.answer(
        f'open({str(marks)!r}, "a").write("cloud-start\\n")\n'
        'time.sleep(1.0)\n'
        f'open({str(marks)!r}, "a").write("cloud-end\\n")\n'
        f'print({LINE!r}, flush=True)\n'
    )
    cw.ws.ensure(parse_cloud_repo({'url': URL}))
    create = threading.Thread(target=cw.create, args=({'url': URL},))
    create.start()
    deadline = time.monotonic() + 10
    while not marks.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    task = parse_repo({'url': URL, 'task_id': 'tsk_1', 'slug': 'x'})
    cw.ws.add_worktree(task)
    with marks.open('a') as f:
        f.write('worktree-added\n')
    create.join()
    assert marks.read_text().split() == ['cloud-start', 'cloud-end', 'worktree-added']


def test_the_clone_lets_go_of_the_base_branch_without_changing_a_file(cw: CloudWs) -> None:
    clone = cw.ws.ensure(parse_cloud_repo({'url': URL}))
    before = git(clone, 'rev-parse', 'HEAD')
    cw.create({'url': URL, 'base': 'main'})
    assert git(clone, 'rev-parse', 'HEAD') == before
    assert git(clone, 'status', '--porcelain') == ''
    detached = subprocess.run(['git', 'symbolic-ref', '-q', 'HEAD'], cwd=clone, check=False)
    assert detached.returncode == 1
