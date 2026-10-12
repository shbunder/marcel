"""Nothing planted where the brain (or anyone on the host) can write may make the hub's git run a
program. Each test plants a program that leaves a marker file, saves through the hub, and checks
the marker never appears."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

from marcel_hub.memory import MemoryRepo, MemoryUnavailable


@pytest.fixture
def repo(tmp_path: Path) -> MemoryRepo:
    repo = MemoryRepo(tmp_path / 'memory')
    repo.write('MEMORY.md', 'start\n', None, 'app')
    return repo


@pytest.fixture
def marker(tmp_path: Path) -> Path:
    return tmp_path / 'PWNED'


def program(tmp_path: Path, marker: Path, name: str = 'evil') -> Path:
    """A program that leaves `marker` and passes stdin through (so it also works as a filter)."""
    script = tmp_path / f'{name}.sh'
    script.write_text(f'#!/bin/sh\ntouch {marker}\ncat\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def save(repo: MemoryRepo, text: str = 'next\n') -> None:
    etag = repo.read('MEMORY.md')['etag']
    repo.write('MEMORY.md', text, etag, 'app')


def test_the_git_folder_is_outside_the_memory_folder(repo: MemoryRepo) -> None:
    assert repo.git_dir == repo.root.with_name('memory.git')
    assert (repo.git_dir / 'HEAD').is_file()
    assert not (repo.root / '.git').exists()


def test_a_git_config_planted_in_the_memory_folder_is_never_read(
    repo: MemoryRepo, tmp_path: Path, marker: Path
) -> None:
    """The brain can write the memory folder; a `.git/` it plants there is not the hub's repo."""
    planted = repo.root / '.git'
    subprocess.run(['git', 'init', '-q', str(repo.root)], check=True, env={**os.environ,
                   'GIT_CONFIG_GLOBAL': os.devnull})  # fmt: skip
    (planted / 'config').write_text(
        f'[core]\n\tfsmonitor = {program(tmp_path, marker)}\n'
        f'[filter "evil"]\n\tclean = {program(tmp_path, marker, "clean")}\n'
    )
    (planted / 'info').mkdir(exist_ok=True)
    (planted / 'info' / 'attributes').write_text('* filter=evil\n')
    hook = planted / 'hooks' / 'pre-commit'
    hook.write_bytes(program(tmp_path, marker, 'hook').read_bytes())
    hook.chmod(0o755)

    save(repo)

    assert not marker.exists()
    assert repo.read('MEMORY.md')['updated_by'] == 'app'


def test_a_planted_fsmonitor_never_runs(repo: MemoryRepo, tmp_path: Path, marker: Path) -> None:
    """Even in the hub's own git config: `core.fsmonitor=false` beats every config file."""
    with (repo.git_dir / 'config').open('a') as f:
        f.write(f'[core]\n\tfsmonitor = {program(tmp_path, marker)}\n')

    save(repo)
    repo.tree()

    assert not marker.exists()


def test_a_planted_hook_never_runs(repo: MemoryRepo, tmp_path: Path, marker: Path) -> None:
    for name in ('pre-commit', 'commit-msg', 'post-commit'):
        hook = repo.git_dir / 'hooks' / name
        hook.parent.mkdir(exist_ok=True)
        hook.write_bytes(program(tmp_path, marker, name).read_bytes())
        hook.chmod(0o755)

    save(repo)

    assert not marker.exists()


def test_a_planted_gitattributes_filter_never_runs(
    repo: MemoryRepo, tmp_path: Path, marker: Path
) -> None:
    """A `.gitattributes` in the memory folder can name a filter program; the hub refuses to
    commit next to one, and says how to fix it."""
    with (repo.git_dir / 'config').open('a') as f:
        f.write(f'[filter "evil"]\n\tclean = {program(tmp_path, marker)}\n')
    (repo.root / 'memory').mkdir()
    (repo.root / 'memory' / '.gitattributes').write_text('*.md filter=evil\n')

    with pytest.raises(MemoryUnavailable, match=r'memory/\.gitattributes'):
        save(repo)

    assert not marker.exists()
    assert repo.read('MEMORY.md')['content'] == 'start\n'  # nothing was written


def test_a_global_git_config_is_never_read(
    repo: MemoryRepo, tmp_path: Path, marker: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host user's ~/.gitconfig (or one named by GIT_CONFIG_GLOBAL) does not reach the hub."""
    home = tmp_path / 'home'
    home.mkdir()
    (home / '.gitconfig').write_text(
        f'[filter "evil"]\n\tclean = {program(tmp_path, marker)}\n[marcel]\n\tplanted = yes\n'
    )
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(home))
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(home / '.gitconfig'))
    (repo.git_dir / 'info').mkdir(exist_ok=True)
    (repo.git_dir / 'info' / 'attributes').write_text('*.md filter=evil\n')

    save(repo)

    assert not marker.exists()
    assert repo._git('config', '--get', 'marcel.planted', ok=(1,)) is None


def test_a_git_folder_from_an_older_hub_is_moved_out_with_a_fresh_config(
    tmp_path: Path, marker: Path
) -> None:
    """An older hub kept `.git` inside the memory folder, where the brain could have edited it.
    The first start moves it out, keeps the history, and drops its config, hooks and attributes."""
    root = tmp_path / 'memory'
    root.mkdir()
    who = {f'GIT_{role}_{field}': 'x@x' for role in ('AUTHOR', 'COMMITTER')
           for field in ('NAME', 'EMAIL')}  # fmt: skip
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, **who}
    (root / 'MEMORY.md').write_text('old\n')
    for args in (['init', '-q', '-b', 'main'], ['add', 'MEMORY.md'],
                 ['commit', '-q', '-m', 'memory: MEMORY.md (app)']):  # fmt: skip
        subprocess.run(['git', '-C', str(root), *args], check=True, env=env)
    with (root / '.git' / 'config').open('a') as f:
        f.write(f'[core]\n\tfsmonitor = {program(tmp_path, marker)}\n')
        f.write(f'[filter "evil"]\n\tclean = {program(tmp_path, marker, "clean")}\n')
    (root / '.git' / 'info' / 'attributes').write_text('* filter=evil\n')
    hook = root / '.git' / 'hooks' / 'pre-commit'
    hook.write_bytes(program(tmp_path, marker, 'hook').read_bytes())
    hook.chmod(0o755)

    repo = MemoryRepo(root)
    save(repo, 'new\n')

    assert not marker.exists()
    # What came along is gone, not just outvoted by the hub's own settings:
    assert repo._git('config', '--get', 'filter.evil.clean', ok=(1,)) is None
    assert not (repo.git_dir / 'hooks' / 'pre-commit').exists()
    assert not (repo.git_dir / 'info' / 'attributes').exists()
    assert not (root / '.git').exists()
    assert repo.git_dir.is_dir()
    log = repo._git('log', '--format=%s') or ''
    assert log.splitlines() == ['memory: MEMORY.md (app)', 'memory: MEMORY.md (app)']


def test_a_planted_attributes_file_setting_never_applies(
    repo: MemoryRepo, tmp_path: Path, marker: Path
) -> None:
    """`core.attributesFile` can point at a file outside the memory folder that names a filter;
    `-c core.attributesFile=/dev/null` beats it."""
    attributes = tmp_path / 'attributes'
    attributes.write_text('*.md filter=evil\n')
    with (repo.git_dir / 'config').open('a') as f:
        f.write(f'[core]\n\tattributesFile = {attributes}\n')
        f.write(f'[filter "evil"]\n\tclean = {program(tmp_path, marker)}\n')

    save(repo)

    assert not marker.exists()
