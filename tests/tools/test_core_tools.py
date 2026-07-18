"""Tests for tools/core.py — bash, file operations, and git tools."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from marcel_core.harness.context import MarcelDeps
from marcel_core.tools.core import (
    git_add,
    git_commit,
    git_diff,
    git_log,
    git_push,
    git_status,
)


def _ctx(cwd: str | None = None) -> MagicMock:
    """Return a minimal mock RunContext with MarcelDeps."""
    deps = MarcelDeps(user_slug='shaun', conversation_id='conv-1', channel='cli', cwd=cwd)
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """tmp_path initialized as a standalone git repo, with GIT_* env vars scrubbed.

    When pytest runs inside a pre-commit hook, git exports GIT_DIR,
    GIT_WORK_TREE, and GIT_INDEX_FILE into the hook environment. Those
    leak through to subprocesses spawned by bash() — causing shelled-out
    `git add` calls to stage files into the hook's in-flight commit index
    rather than this isolated tmp repo. Scrub them. See ISSUE-83ee76.
    """
    for var in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
        monkeypatch.delenv(var, raising=False)
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=tmp_path, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=tmp_path, check=True)
    return tmp_path


# ---------------------------------------------------------------------------
# bash
# ---------------------------------------------------------------------------


class TestGitTools:
    @pytest.mark.asyncio
    async def test_git_status_returns_string(self, git_repo):
        result = await git_status(_ctx(str(git_repo)))
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_git_diff_returns_string(self, git_repo):
        result = await git_diff(_ctx(str(git_repo)))
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_git_log_returns_string(self, git_repo):
        result = await git_log(_ctx(str(git_repo)))
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_git_add_returns_string(self, git_repo):
        f = git_repo / 'newfile.txt'
        f.write_text('hello')
        result = await git_add(_ctx(str(git_repo)), 'newfile.txt')
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_git_commit_returns_string(self, git_repo):
        result = await git_commit(_ctx(str(git_repo)), 'test commit')
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_git_push_returns_string(self, git_repo):
        result = await git_push(_ctx(str(git_repo)))
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_git_add_does_not_leak_under_hook_env(self, tmp_path, monkeypatch):
        """Regression for ISSUE-83ee76: simulate a pre-commit hook setting GIT_*
        env vars to point at an outer repo, then do what the git_repo fixture
        does (init an inner repo + scrub env), and confirm git_add on a file in
        the inner repo does NOT stage into the outer repo's index.
        """
        # Scrub at the very top — any subprocess.run below would otherwise
        # itself hit the bug (its `git init`/`add`/`commit` would target the
        # hook's in-flight index, not the test's outer repo).
        for v in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
            monkeypatch.delenv(v, raising=False)

        outer = tmp_path / 'outer'
        inner = tmp_path / 'inner'
        for d in (outer, inner):
            d.mkdir()
            subprocess.run(['git', 'init', '-q'], cwd=d, check=True)
            subprocess.run(['git', 'config', 'user.email', 't@e.com'], cwd=d, check=True)
            subprocess.run(['git', 'config', 'user.name', 'T'], cwd=d, check=True)
        (outer / 'seed.txt').write_text('seed')
        subprocess.run(['git', '-C', str(outer), 'add', 'seed.txt'], check=True)
        subprocess.run(['git', '-C', str(outer), 'commit', '-q', '-m', 'seed'], check=True)

        # Now simulate the hook env that would cause the bug
        monkeypatch.setenv('GIT_DIR', str(outer / '.git'))
        monkeypatch.setenv('GIT_WORK_TREE', str(outer))
        monkeypatch.setenv('GIT_INDEX_FILE', str(outer / '.git' / 'index'))

        # And apply the fix (what the git_repo fixture does for normal tests)
        for v in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
            monkeypatch.delenv(v, raising=False)

        (inner / 'leak.txt').write_text('x')
        await git_add(_ctx(str(inner)), 'leak.txt')

        staged = subprocess.check_output(['git', '-C', str(outer), 'diff', '--cached', '--name-only']).decode()
        assert 'leak.txt' not in staged, f'leaked into outer index: {staged!r}'
