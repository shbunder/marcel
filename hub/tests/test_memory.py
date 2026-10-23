"""Memory (B-20): a git working copy the hub commits to, read and edited through `/api/memory`.

Checked against contracts/app-api.yaml. Every write is one commit; a stale etag is a 409; a path
can never leave the memory folder.
"""

import os
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from test_tasks_fakes import validator
from test_tasks_routes import pair

from marcel_hub import memory
from marcel_hub.app import Settings, create_app
from marcel_hub.memory import (
    InvalidMemoryPath,
    MemoryConflict,
    MemoryFileNotFound,
    MemoryRepo,
    MemoryUnavailable,
    blob_id,
)
from marcel_hub.users.store import ensure_owner_agent

TREE = validator('app-api.yaml#/components/schemas/MemoryTree')
FILE = validator('app-api.yaml#/components/schemas/MemoryFile')
ERROR = validator('app-api.yaml#/components/schemas/Error')


def url(path: str) -> str:
    return '/api/memory/files/' + quote(path, safe='')


def git(root: Path, *args: str) -> str:
    """Plain git on the memory repo, the way a person on the NUC would look at it."""
    git_dir = root.with_name(f'{root.name}.git')
    cmd = ['git', f'--git-dir={git_dir}', f'--work-tree={root}', *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def git_log(root: Path) -> list[str]:
    return git(root, 'log', '--format=%s').splitlines()


@pytest.fixture
def repo(tmp_path: Path) -> MemoryRepo:
    return MemoryRepo(tmp_path / 'memory')


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db'))
    with TestClient(app) as c:
        with Session(app.state.engine) as s:
            ensure_owner_agent(s, 'Marcel')  # slug `marcel`: the phone's memory is agents/marcel/
        c.headers['Authorization'] = f'Bearer {pair(c, app.state.engine)}'
        yield c


def mine(tmp_path: Path) -> Path:
    """The paired phone's memory on disk (S-17.4): its agent's folder in the memory repo."""
    return tmp_path / 'memory' / 'agents' / 'marcel'


# Done when: concurrent edit -> 409; the git log shows one commit per write. ------------------


def test_an_edit_with_a_stale_etag_is_a_409_with_the_current_version(client: TestClient) -> None:
    created = client.put(url('MEMORY.md'), json={'content': '# Memory\n'})
    assert created.status_code == 200, created.text
    etag = created.json()['etag']

    phone = client.put(url('MEMORY.md'), json={'content': 'phone\n'}, headers={'If-Match': etag})
    assert phone.status_code == 200
    laptop = client.put(url('MEMORY.md'), json={'content': 'laptop\n'}, headers={'If-Match': etag})

    assert laptop.status_code == 409
    FILE.validate(laptop.json())
    assert laptop.json()['content'] == 'phone\n'
    assert laptop.json()['etag'] == phone.json()['etag']
    assert client.get(url('MEMORY.md')).json()['content'] == 'phone\n'


def test_the_git_log_shows_one_commit_per_write(client: TestClient, tmp_path: Path) -> None:
    etag = client.put(url('MEMORY.md'), json={'content': 'one\n'}).json()['etag']
    for text in ('two\n', 'three\n'):
        res = client.put(url('MEMORY.md'), json={'content': text}, headers={'If-Match': etag})
        etag = res.json()['etag']
    client.put(url('memory/2026-10-12.md'), json={'content': 'dinner at 18:30\n'})

    assert git_log(tmp_path / 'memory') == [
        'memory: agents/marcel/memory/2026-10-12.md (app)',
        'memory: agents/marcel/MEMORY.md (app)',
        'memory: agents/marcel/MEMORY.md (app)',
        'memory: agents/marcel/MEMORY.md (app)',
    ]


def test_two_saves_at_the_same_moment_cannot_both_win(
    repo: MemoryRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The write lock: without it both saves pass the etag check before either one writes."""
    etag = repo.write('MEMORY.md', 'start\n', None, 'app')['etag']
    real_write = memory._write_file

    def slow_write(path: Path, data: bytes) -> None:
        time.sleep(0.2)
        real_write(path, data)

    monkeypatch.setattr(memory, '_write_file', slow_write)
    results: list[str] = []

    def save(text: str) -> None:
        try:
            repo.write('MEMORY.md', text, etag, 'app')
            results.append('saved')
        except MemoryConflict:
            results.append('conflict')

    threads = [threading.Thread(target=save, args=(t,)) for t in ('a\n', 'b\n')]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ['conflict', 'saved']
    assert len(git_log(repo.root)) == 2


# The rest of the contract ----------------------------------------------------------------


def test_reading_a_file_returns_its_etag_in_the_body_and_the_header(client: TestClient) -> None:
    client.put(url('memory/2026-10-04.md'), json={'content': 'Family dinner is at 18:30.\n'})

    res = client.get(url('memory/2026-10-04.md'))

    assert res.status_code == 200
    FILE.validate(res.json())
    body = res.json()
    assert body['path'] == 'memory/2026-10-04.md'
    assert body['content'] == 'Family dinner is at 18:30.\n'
    assert body['etag'] == blob_id(b'Family dinner is at 18:30.\n')
    assert res.headers['ETag'] == f'"{body["etag"]}"'  # HTTP quotes it; the body stays bare
    assert body['updated_by'] == 'app'


def test_if_match_accepts_a_quoted_etag(client: TestClient) -> None:
    etag = client.put(url('MEMORY.md'), json={'content': 'a\n'}).json()['etag']
    res = client.put(url('MEMORY.md'), json={'content': 'b\n'}, headers={'If-Match': f'"{etag}"'})
    assert res.status_code == 200


def test_creating_a_file_that_exists_is_a_409_never_an_overwrite(client: TestClient) -> None:
    client.put(url('MEMORY.md'), json={'content': 'mine\n'})

    res = client.put(url('MEMORY.md'), json={'content': 'theirs\n'})

    assert res.status_code == 409
    assert res.json()['content'] == 'mine\n'


def test_a_file_changed_on_disk_by_the_brain_changes_its_etag(
    client: TestClient, tmp_path: Path
) -> None:
    etag = client.put(url('MEMORY.md'), json={'content': 'v1\n'}).json()['etag']
    (mine(tmp_path) / 'MEMORY.md').write_text('brain wrote this\n')

    res = client.put(url('MEMORY.md'), json={'content': 'app\n'}, headers={'If-Match': etag})

    assert res.status_code == 409
    assert res.json()['content'] == 'brain wrote this\n'
    assert 'updated_by' not in res.json()  # not committed by the hub, so nobody to name


def test_saving_only_commits_the_file_that_was_saved(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / 'memory'
    client.put(url('MEMORY.md'), json={'content': 'v1\n'})
    (mine(tmp_path) / 'scratch.md').write_text('the brain is still writing\n')

    client.put(url('other.md'), json={'content': 'x\n'})

    assert git(root, 'status', '--porcelain').strip() == '?? agents/marcel/scratch.md'


def test_an_unchanged_save_makes_no_commit(client: TestClient, tmp_path: Path) -> None:
    etag = client.put(url('MEMORY.md'), json={'content': 'same\n'}).json()['etag']
    res = client.put(url('MEMORY.md'), json={'content': 'same\n'}, headers={'If-Match': etag})
    assert res.status_code == 200
    assert len(git_log(tmp_path / 'memory')) == 1


def test_if_match_on_a_missing_file_is_a_404(client: TestClient) -> None:
    res = client.put(url('gone.md'), json={'content': 'x\n'}, headers={'If-Match': 'abc'})
    assert res.status_code == 404
    ERROR.validate(res.json())
    assert res.json()['code'] == 'memory_file_not_found'


def test_a_missing_file_is_a_404(client: TestClient) -> None:
    res = client.get(url('nope.md'))
    assert res.status_code == 404
    assert res.json()['code'] == 'memory_file_not_found'


def test_the_tree_lists_every_markdown_file(client: TestClient, tmp_path: Path) -> None:
    client.put(url('MEMORY.md'), json={'content': '# Memory\n'})
    client.put(url('memory/2026-10-04.md'), json={'content': 'note\n'})
    root = mine(tmp_path)
    (root / 'notes.txt').write_text('not markdown\n')
    (root / '.hidden').mkdir()
    (root / '.hidden' / 'x.md').write_text('hidden\n')
    (root / 'draft.md').write_text('not committed yet\n')

    res = client.get('/api/memory')

    assert res.status_code == 200
    TREE.validate(res.json())
    files = {f['path']: f for f in res.json()['files']}
    assert sorted(files) == ['MEMORY.md', 'draft.md', 'memory/2026-10-04.md']
    assert files['MEMORY.md']['size_bytes'] == len('# Memory\n')


def test_the_tree_of_an_empty_memory_is_empty(client: TestClient) -> None:
    res = client.get('/api/memory')
    assert res.status_code == 200
    assert res.json() == {'files': []}


def test_a_bad_body_is_a_400(client: TestClient) -> None:
    for body in ({}, {'content': 3}, {'content': 'x', 'extra': 1}):
        res = client.put(url('MEMORY.md'), json=body)
        assert res.status_code == 400, body
        assert res.json()['code'] == 'bad_request'
    assert client.put(url('MEMORY.md'), content=b'').status_code == 400
    assert client.put(url('MEMORY.md'), content=b'{nope').status_code == 400


def test_a_file_over_the_size_limit_is_refused(client: TestClient) -> None:
    res = client.put(url('MEMORY.md'), json={'content': 'x' * (memory.MAX_FILE_BYTES + 1)})
    assert res.status_code == 400
    assert 'too long' in res.json()['message']


def test_a_failed_commit_puts_the_old_file_back(
    repo: MemoryRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    etag = repo.write('MEMORY.md', 'old\n', None, 'app')['etag']
    real_git = repo._git

    def broken_git(*args: str, **kw: Any) -> str | None:
        if args[0] == 'commit':
            raise MemoryUnavailable('git said no.')
        return real_git(*args, **kw)

    monkeypatch.setattr(repo, '_git', broken_git)
    with pytest.raises(MemoryUnavailable):
        repo.write('MEMORY.md', 'new\n', etag, 'app')
    with pytest.raises(MemoryUnavailable):
        repo.write('fresh.md', 'new\n', None, 'app')

    assert (repo.root / 'MEMORY.md').read_text() == 'old\n'
    assert not (repo.root / 'fresh.md').exists()


def test_git_missing_answers_503_with_a_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(memory, 'GIT', 'git-that-does-not-exist')
    res = client.put(url('MEMORY.md'), json={'content': 'x\n'})
    assert res.status_code == 503
    assert res.json()['code'] == 'memory_unavailable'
    assert 'not saved' in res.json()['message']


def test_reading_without_git_answers_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db'))
    with TestClient(app) as c:
        with Session(app.state.engine) as s:
            ensure_owner_agent(s, 'Marcel')  # a person with memory to read
        c.headers['Authorization'] = f'Bearer {pair(c, app.state.engine)}'
        monkeypatch.setattr(memory, 'GIT', 'git-that-does-not-exist')
        for res in (c.get('/api/memory'), c.get(url('MEMORY.md'))):
            assert res.status_code == 503
            assert res.json()['code'] == 'memory_unavailable'
            assert 'cannot be read' in res.json()['message']


def test_an_agent_write_is_named_in_the_commit(repo: MemoryRepo) -> None:
    repo.write('MEMORY.md', 'x\n', None, 'agent')
    assert git_log(repo.root) == ['memory: MEMORY.md (agent)']
    assert repo.read('MEMORY.md')['updated_by'] == 'agent'


def test_a_commit_made_outside_the_hub_has_no_author_name(repo: MemoryRepo) -> None:
    repo.write('MEMORY.md', 'x\n', None, 'app')
    (repo.root / 'MEMORY.md').write_text('by hand\n')
    repo._git('commit', '-q', '-am', 'tidy up')
    assert 'updated_by' not in repo.read('MEMORY.md')


# The path guard: a path can never leave the memory folder. ----------------------------------


@pytest.mark.parametrize(
    'path',
    [
        '../outside.md',
        'memory/../../outside.md',
        '/etc/passwd.md',
        '.git/config.md',
        'memory/.secret.md',
        'notes.txt',
        'MEMORY',
        'a b.md',
        '',
    ],
)
def test_paths_outside_the_pattern_are_refused(client: TestClient, path: str) -> None:
    for res in (client.get(url(path)), client.put(url(path), json={'content': 'x'})):
        assert res.status_code in (400, 404), path  # '' is not a route at all
        if res.status_code == 400:
            ERROR.validate(res.json())
            assert res.json()['code'] == 'invalid_memory_path'


def test_a_dot_git_path_never_reaches_the_repo(repo: MemoryRepo) -> None:
    """The pattern check alone keeps `.git/` out: it resolves inside the root."""
    repo.write('MEMORY.md', 'x\n', None, 'app')
    with pytest.raises(InvalidMemoryPath):
        repo.write('.git/hooks/pre-commit.md', 'boom', None, 'app')


def test_a_symlinked_file_pointing_out_is_refused(repo: MemoryRepo, tmp_path: Path) -> None:
    secret = tmp_path / 'secret.md'
    secret.write_text('the hub database password\n')
    repo.write('MEMORY.md', 'x\n', None, 'app')
    os.symlink(secret, repo.root / 'evil.md')

    with pytest.raises(InvalidMemoryPath):
        repo.read('evil.md')
    with pytest.raises(InvalidMemoryPath):
        repo.write('evil.md', 'overwritten\n', blob_id(secret.read_bytes()), 'app')
    assert secret.read_text() == 'the hub database password\n'
    assert [f['path'] for f in repo.tree()] == ['MEMORY.md']


def test_a_symlinked_folder_pointing_out_is_refused(client: TestClient, tmp_path: Path) -> None:
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'x.md').write_text('outside\n')
    client.put(url('MEMORY.md'), json={'content': 'x\n'})
    os.symlink(outside, mine(tmp_path) / 'linked')

    assert client.get(url('linked/x.md')).status_code == 400
    assert client.put(url('linked/new.md'), json={'content': 'x'}).status_code == 400
    assert not (outside / 'new.md').exists()
    assert [f['path'] for f in client.get('/api/memory').json()['files']] == ['MEMORY.md']


def test_a_symlink_that_stays_inside_is_still_refused(repo: MemoryRepo) -> None:
    """Any symlink is refused, so a link cannot be swapped to point out between check and use."""
    repo.write('MEMORY.md', 'x\n', None, 'app')
    os.symlink(repo.root / 'MEMORY.md', repo.root / 'alias.md')
    with pytest.raises(InvalidMemoryPath):
        repo.read('alias.md')


def test_a_symlinked_folder_that_stays_inside_is_still_refused(repo: MemoryRepo) -> None:
    """No part of a path may be a symlink, even one that points inside: it could be re-pointed
    out between the check and the write."""
    repo.write('memory/a.md', 'x\n', None, 'app')
    os.symlink(repo.root / 'memory', repo.root / 'alias')
    with pytest.raises(InvalidMemoryPath):
        repo.read('alias/a.md')
    with pytest.raises(InvalidMemoryPath):
        repo.write('alias/b.md', 'x', None, 'app')


def test_a_folder_in_place_of_a_file_is_not_found(repo: MemoryRepo) -> None:
    (repo.root / 'dir.md').mkdir(parents=True)
    with pytest.raises(MemoryFileNotFound):
        repo.read('dir.md')


def test_the_repo_is_created_on_first_use(tmp_path: Path) -> None:
    repo = MemoryRepo(tmp_path / 'fresh')
    assert repo.tree() == []
    assert (tmp_path / 'fresh.git').is_dir()
    assert not (tmp_path / 'fresh' / '.git').exists()  # the brain cannot reach the git folder


def test_the_folder_check_holds_even_if_the_pattern_lets_dots_through(
    repo: MemoryRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two locks on one door: with a pattern that allows `..`, the path still cannot leave."""
    import re

    monkeypatch.setattr(memory, 'PATH_PATTERN', re.compile(r'.+\.md'))
    repo.write('MEMORY.md', 'x\n', None, 'app')
    with pytest.raises(InvalidMemoryPath):
        repo.write('../escaped.md', 'x', None, 'app')
    assert not (repo.root.parent / 'escaped.md').exists()


def test_the_last_part_of_a_path_is_opened_without_following_a_link(
    repo: MemoryRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a link appears after the path check (a race), opening the file still refuses it."""
    secret = tmp_path / 'secret.md'
    secret.write_text('secret\n')
    repo.write('MEMORY.md', 'x\n', None, 'app')
    os.symlink(secret, repo.root / 'late.md')
    monkeypatch.setattr(repo, 'resolve', lambda rel: repo.root / rel)  # the check already passed

    with pytest.raises(InvalidMemoryPath):
        repo.read('late.md')


def test_a_file_written_on_disk_over_the_limit_is_not_read_into_memory(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.put(url('MEMORY.md'), json={'content': 'x\n'})
    big = mine(tmp_path) / 'huge.md'
    big.write_bytes(b'x' * (memory.MAX_FILE_BYTES + 4096))

    read_sizes: list[int] = []
    real_fdopen = os.fdopen

    def recording_fdopen(fd: int, *args: Any, **kw: Any) -> Any:
        f = real_fdopen(fd, *args, **kw)
        real_read = f.read

        def read(*a: Any) -> bytes:
            data = real_read(*a)
            read_sizes.append(len(data))
            return data

        f.read = read
        return f

    monkeypatch.setattr(memory.os, 'fdopen', recording_fdopen)
    res = client.get(url('huge.md'))
    assert max(read_sizes) == memory.MAX_FILE_BYTES + 1  # never the whole file
    monkeypatch.undo()

    assert res.status_code == 400
    ERROR.validate(res.json())
    assert res.json()['code'] == 'memory_file_too_big'
    sizes = {f['path']: f['size_bytes'] for f in client.get('/api/memory').json()['files']}
    assert sizes['huge.md'] == memory.MAX_FILE_BYTES + 4096
    over = client.put(url('huge.md'), json={'content': 'short\n'}, headers={'If-Match': 'x'})
    assert over.status_code == 400
    assert big.stat().st_size == memory.MAX_FILE_BYTES + 4096


def test_a_body_without_a_length_is_cut_off_at_the_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chunked body (no Content-Length) is read only up to the limit, then refused."""
    monkeypatch.setattr(memory, 'MAX_BODY_BYTES', 100)

    def chunks() -> Iterator[bytes]:
        for _ in range(50):
            yield b'x' * 10

    res = client.put(url('MEMORY.md'), content=chunks())

    assert res.status_code == 400
    assert 'too long' in res.json()['message']


def test_a_body_with_a_large_length_is_refused_before_reading(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(memory, 'MAX_BODY_BYTES', 100)
    res = client.put(url('MEMORY.md'), json={'content': 'x' * 200})
    assert res.status_code == 400
    assert 'too long' in res.json()['message']


# Memory per buddy: the `agent_id` query parameter (app-api 0.8.1, S-22.27) -------------------


def pip(client: TestClient, tmp_path: Path) -> tuple[str, Path]:
    """A second buddy, Pip (slug `pip`), with a note of its own on disk."""
    res = client.post('/api/agents', json={'name': 'Pip'})
    assert res.status_code == 201, res.text
    folder = tmp_path / 'memory' / 'agents' / 'pip'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'MEMORY.md').write_text('Pip likes the garden.\n')
    return res.json()['id'], folder


def test_agent_id_picks_the_buddy_whose_memory_is_read(client: TestClient, tmp_path: Path) -> None:
    client.put(url('MEMORY.md'), json={'content': 'Marcel keeps the bills.\n'})
    pip_id, _ = pip(client, tmp_path)
    theirs = client.get('/api/memory', params={'agent_id': pip_id})
    assert theirs.status_code == 200, theirs.text
    TREE.validate(theirs.json())
    assert [f['path'] for f in theirs.json()['files']] == ['MEMORY.md']
    one = client.get(url('MEMORY.md'), params={'agent_id': pip_id})
    FILE.validate(one.json())
    assert one.json()['content'] == 'Pip likes the garden.\n'
    # Without it: the buddy the phone shows (Marcel).
    assert client.get(url('MEMORY.md')).json()['content'] == 'Marcel keeps the bills.\n'


def test_agent_id_picks_the_buddy_whose_memory_is_saved(client: TestClient, tmp_path: Path) -> None:
    pip_id, folder = pip(client, tmp_path)
    res = client.put(url('memory/2026-10-23.md'), params={'agent_id': pip_id},
                     json={'content': 'Watered the tomatoes.\n'})  # fmt: skip
    assert res.status_code == 200, res.text
    assert (folder / 'memory' / '2026-10-23.md').read_text() == 'Watered the tomatoes.\n'
    assert not (mine(tmp_path) / 'memory' / '2026-10-23.md').exists()


def test_an_id_that_is_no_buddy_is_404_on_all_three(client: TestClient, tmp_path: Path) -> None:
    # Control: delete the `agent is None` check in `memory._repo` and this goes red.
    client.put(url('MEMORY.md'), json={'content': 'Marcel keeps the bills.\n'})
    log_before = git_log(tmp_path / 'memory')
    params = {'agent_id': 'agt_nope'}
    for res in (
        client.get('/api/memory', params=params),
        client.get(url('MEMORY.md'), params=params),
        client.put(url('MEMORY.md'), params=params, json={'content': 'x\n'}),
    ):
        assert res.status_code == 404 and res.json()['code'] == 'agent_not_found', res.text
        ERROR.validate(res.json())
    assert git_log(tmp_path / 'memory') == log_before, 'nothing was committed'


def test_an_archived_buddys_memory_reads_but_takes_no_save(
    client: TestClient, tmp_path: Path
) -> None:
    # Control: delete the `saving and archived_at` check in `memory._repo` and this goes red.
    client.put(url('MEMORY.md'), json={'content': 'Marcel keeps the bills.\n'})
    pip_id, folder = pip(client, tmp_path)
    client.post('/api/agents', json={'name': 'Bo'})  # so Pip is not the last live buddy
    assert client.post(f'/api/agents/{pip_id}/archive').status_code == 200
    read = client.get(url('MEMORY.md'), params={'agent_id': pip_id})
    assert read.status_code == 200 and read.json()['content'] == 'Pip likes the garden.\n'
    assert client.get('/api/memory', params={'agent_id': pip_id}).status_code == 200
    log_before = git_log(tmp_path / 'memory')
    res = client.put(url('MEMORY.md'), params={'agent_id': pip_id},
                     headers={'If-Match': read.json()['etag']},
                     json={'content': 'Pip moved out.\n'})  # fmt: skip
    assert res.status_code == 404 and res.json()['code'] == 'agent_not_found', res.text
    assert (folder / 'MEMORY.md').read_text() == 'Pip likes the garden.\n'
    assert git_log(tmp_path / 'memory') == log_before, 'nothing was committed'
