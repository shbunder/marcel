"""The marketplace CLI (make-target path, STORY-260719-775965)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from marcel_core.marketplace.cli import main


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    for var in ('GIT_AUTHOR_NAME', 'GIT_COMMITTER_NAME'):
        monkeypatch.setenv(var, 't')
    for var in ('GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_EMAIL'):
        monkeypatch.setenv(var, 't@t')


@pytest.fixture
def roots(tmp_path, monkeypatch):
    from marcel_core.config import settings
    from marcel_core.storage import _root

    repo = tmp_path / 'source-repo'
    skill = repo / 'skills' / 'greeter'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: greeter\ndescription: Says hello.\n---\n\nBody.\n')
    _git(repo, 'init', '-q')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'seed')

    zoo = tmp_path / 'zoo'
    zoo.mkdir()
    (zoo / 'sources.yaml').write_text(
        f'sources:\n  - name: fixture\n    type: agentskills-git\n    url: {repo}\n    subdir: skills\n'
    )
    _git(zoo, 'init', '-q')
    _git(zoo, 'add', 'sources.yaml')
    _git(zoo, 'commit', '-qm', 'zoo seed')

    monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))
    monkeypatch.setattr(settings, 'marcel_data_dir', str(tmp_path / 'data'))
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path / 'data')
    return zoo


class TestCli:
    def test_sources_and_browse(self, roots, capsys):
        assert main(['sources']) == 0
        assert 'fixture' in capsys.readouterr().out
        assert main(['browse', '--source', 'fixture']) == 0
        assert 'greeter' in capsys.readouterr().out

    def test_install_with_yes_renders_review_first(self, roots, capsys):
        assert main(['install', '--source', 'fixture', '--name', 'greeter', '--yes']) == 0
        out = capsys.readouterr().out
        assert 'Install **greeter**' in out  # the review summary hit the scrollback
        assert 'installed:' in out
        assert (roots / 'skills' / 'greeter' / 'SKILL.md').is_file()

    def test_install_declined_aborts(self, roots, capsys, monkeypatch):
        monkeypatch.setattr('builtins.input', lambda _prompt: 'n')
        assert main(['install', '--source', 'fixture', '--name', 'greeter']) == 1
        assert 'aborted' in capsys.readouterr().out
        assert not (roots / 'skills' / 'greeter').exists()

    def test_remove_and_errors(self, roots, capsys):
        main(['install', '--source', 'fixture', '--name', 'greeter', '--yes'])
        capsys.readouterr()
        assert main(['remove', '--kind', 'skill', '--name', 'greeter']) == 0
        assert 'removed' in capsys.readouterr().out
        assert main(['remove', '--kind', 'skill', '--name', 'ghost']) == 1
        assert 'error:' in capsys.readouterr().err
