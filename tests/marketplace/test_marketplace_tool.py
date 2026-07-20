"""The marketplace admin tool (STORY-260719-775965): gating + round-trips."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from marcel_core.harness.context import MarcelDeps
from marcel_core.marketplace.tool import marketplace


def _ctx(role: str = 'admin') -> MagicMock:
    ctx = MagicMock()
    ctx.deps = MarcelDeps(user_slug='shaun', conversation_id='shaun:cli', channel='cli', role=role)
    return ctx


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


class TestRoleGating:
    def test_structurally_invisible_to_non_admins(self):
        """Acceptance scenario 4: browse/install flows never reach a
        non-admin pool; dispatch is covered by the admin_tool_names bus gate."""
        from marcel_core.harness.agent import admin_tool_names, available_tool_names

        assert 'marketplace' not in available_tool_names('user')
        assert 'marketplace' in available_tool_names('admin')
        assert 'marketplace' in admin_tool_names()


class TestToolRoundTrip:
    @pytest.mark.asyncio
    async def test_sources_browse_review_install(self, roots):
        assert 'fixture' in await marketplace(_ctx(), 'sources')

        listing = await marketplace(_ctx(), 'browse', source='fixture')
        assert '**greeter** (skill): Says hello.' in listing

        reviewed = await marketplace(_ctx(), 'review', source='fixture', name='greeter')
        assert 'Confirm with the admin' in reviewed
        token = reviewed.rsplit('`', 2)[-2]

        result = await marketplace(_ctx(), 'install', source='fixture', name='greeter', token=token)
        assert 'Installed to' in result and 'Revert the commit to uninstall' in result
        assert (roots / 'skills' / 'greeter' / 'SKILL.md').is_file()

        removed = await marketplace(_ctx(), 'remove', kind='skill', name='greeter')
        assert 'Removed greeter' in removed
        assert not (roots / 'skills' / 'greeter').exists()

    @pytest.mark.asyncio
    async def test_errors_come_back_readable(self, roots):
        assert 'requires source' in await marketplace(_ctx(), 'browse')
        assert 'unknown action' in await marketplace(_ctx(), 'dance')
        result = await marketplace(_ctx(), 'review', source='ghost', name='x')
        assert 'marketplace error' in result and 'fixture' in result

    @pytest.mark.asyncio
    async def test_install_without_review_token_refused(self, roots):
        result = await marketplace(_ctx(), 'install', source='fixture', name='greeter', token='bogus')
        assert 'marketplace error' in result and 'Review token' in result
