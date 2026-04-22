"""Tests for tools/core.py — bash, file operations, and git tools."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from marcel_core.harness.context import MarcelDeps
from marcel_core.tools.core import (
    bash,
    edit_file,
    git_add,
    git_commit,
    git_diff,
    git_log,
    git_push,
    git_status,
    read_file,
    write_file,
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


class TestBash:
    @pytest.mark.asyncio
    async def test_basic_echo(self, tmp_path):
        result = await bash(_ctx(str(tmp_path)), 'echo hello')
        assert 'hello' in result

    @pytest.mark.asyncio
    async def test_nonzero_exit_includes_code(self, tmp_path):
        result = await bash(_ctx(str(tmp_path)), 'exit 42')
        assert '42' in result

    @pytest.mark.asyncio
    async def test_empty_output_returns_no_output(self, tmp_path):
        result = await bash(_ctx(str(tmp_path)), 'true')
        assert 'no output' in result.lower() or result == '(no output)'

    @pytest.mark.asyncio
    async def test_stderr_included_in_output(self, tmp_path):
        result = await bash(_ctx(str(tmp_path)), 'echo error >&2')
        assert 'error' in result

    @pytest.mark.asyncio
    async def test_falls_back_to_project_root_when_no_cwd(self):
        # No cwd → falls back to project root
        ctx = _ctx(None)
        result = await bash(ctx, 'echo from-root')
        assert 'from-root' in result

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, tmp_path):
        result = await bash(_ctx(str(tmp_path)), 'sleep 10', timeout=0)
        assert 'timed out' in result.lower() or 'timeout' in result.lower() or 'error' in result.lower()


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


class TestReadFile:
    @pytest.mark.asyncio
    async def test_reads_file_content(self, tmp_path):
        f = tmp_path / 'test.txt'
        f.write_text('line 1\nline 2\n', encoding='utf-8')
        result = await read_file(_ctx(str(tmp_path)), str(f))
        assert 'line 1' in result
        assert 'line 2' in result

    @pytest.mark.asyncio
    async def test_relative_path_resolved_from_cwd(self, tmp_path):
        f = tmp_path / 'relative.txt'
        f.write_text('relative content', encoding='utf-8')
        result = await read_file(_ctx(str(tmp_path)), 'relative.txt')
        assert 'relative content' in result

    @pytest.mark.asyncio
    async def test_missing_file_returns_error(self, tmp_path):
        result = await read_file(_ctx(str(tmp_path)), '/nonexistent/path.txt')
        assert 'not found' in result.lower() or 'error' in result.lower()

    @pytest.mark.asyncio
    async def test_directory_returns_error(self, tmp_path):
        result = await read_file(_ctx(str(tmp_path)), str(tmp_path))
        assert 'directory' in result.lower() or 'error' in result.lower()

    @pytest.mark.asyncio
    async def test_offset_and_limit_applied(self, tmp_path):
        f = tmp_path / 'multi.txt'
        f.write_text('\n'.join(f'line {i}' for i in range(10)), encoding='utf-8')
        result = await read_file(_ctx(str(tmp_path)), str(f), offset=3, limit=2)
        assert 'line 3' in result
        assert 'line 4' in result
        assert 'line 0' not in result


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


class TestWriteFile:
    @pytest.mark.asyncio
    async def test_creates_file(self, tmp_path):
        target = tmp_path / 'output.txt'
        result = await write_file(_ctx(str(tmp_path)), str(target), 'hello world')
        assert target.read_text() == 'hello world'
        assert '✓' in result or 'Wrote' in result

    @pytest.mark.asyncio
    async def test_creates_parent_directories(self, tmp_path):
        target = tmp_path / 'a' / 'b' / 'c.txt'
        await write_file(_ctx(str(tmp_path)), str(target), 'nested')
        assert target.read_text() == 'nested'

    @pytest.mark.asyncio
    async def test_overwrites_existing(self, tmp_path):
        target = tmp_path / 'existing.txt'
        target.write_text('old content')
        await write_file(_ctx(str(tmp_path)), str(target), 'new content')
        assert target.read_text() == 'new content'


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------


class TestEditFile:
    @pytest.mark.asyncio
    async def test_replaces_string(self, tmp_path):
        f = tmp_path / 'code.py'
        f.write_text('def foo():\n    pass\n', encoding='utf-8')
        result = await edit_file(_ctx(str(tmp_path)), str(f), 'def foo():', 'def bar():')
        assert 'def bar():' in f.read_text()
        assert '✓' in result or 'Replaced' in result

    @pytest.mark.asyncio
    async def test_file_not_found_returns_error(self, tmp_path):
        result = await edit_file(_ctx(str(tmp_path)), '/no/such/file.py', 'old', 'new')
        assert 'not found' in result.lower() or 'error' in result.lower()

    @pytest.mark.asyncio
    async def test_old_string_not_found_returns_error(self, tmp_path):
        f = tmp_path / 'code.py'
        f.write_text('def foo(): pass\n', encoding='utf-8')
        result = await edit_file(_ctx(str(tmp_path)), str(f), 'NOTHERE', 'replacement')
        assert 'not found' in result.lower() or 'error' in result.lower()

    @pytest.mark.asyncio
    async def test_multiple_occurrences_returns_error(self, tmp_path):
        f = tmp_path / 'dup.py'
        f.write_text('foo = 1\nfoo = 2\n', encoding='utf-8')
        result = await edit_file(_ctx(str(tmp_path)), str(f), 'foo', 'bar')
        assert 'times' in result.lower() or 'multiple' in result.lower() or 'error' in result.lower()

    @pytest.mark.asyncio
    async def test_relative_path_resolved_from_cwd(self, tmp_path):
        f = tmp_path / 'code.py'
        f.write_text('old text\n', encoding='utf-8')
        result = await edit_file(_ctx(str(tmp_path)), 'code.py', 'old text', 'new text')
        assert 'new text' in f.read_text()
        assert '✓' in result or 'Replaced' in result


# ---------------------------------------------------------------------------
# git tools (thin wrappers around bash)
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


# ---------------------------------------------------------------------------
# output truncation
# ---------------------------------------------------------------------------


class TestExceptionPaths:
    @pytest.mark.asyncio
    async def test_bash_catches_subprocess_error(self, tmp_path):
        from unittest.mock import patch

        with patch('asyncio.create_subprocess_shell', side_effect=OSError('pipe failed')):
            result = await bash(_ctx(str(tmp_path)), 'echo hi')
        assert 'error' in result.lower()

    @pytest.mark.asyncio
    async def test_read_file_catches_io_error(self, tmp_path):
        f = tmp_path / 'readable.txt'
        f.write_text('data', encoding='utf-8')
        from unittest.mock import patch

        with patch('builtins.open', side_effect=PermissionError('denied')):
            result = await read_file(_ctx(str(tmp_path)), str(f))
        assert 'error' in result.lower()

    @pytest.mark.asyncio
    async def test_write_file_catches_io_error(self, tmp_path):
        from pathlib import Path
        from unittest.mock import patch

        with patch.object(Path, 'write_text', side_effect=PermissionError('denied')):
            result = await write_file(_ctx(str(tmp_path)), str(tmp_path / 'out.txt'), 'content')
        assert 'error' in result.lower()

    @pytest.mark.asyncio
    async def test_write_file_relative_path_resolves(self, tmp_path):
        result = await write_file(_ctx(str(tmp_path)), 'relative_out.txt', 'hello')
        target = tmp_path / 'relative_out.txt'
        assert target.read_text() == 'hello'
        assert '✓' in result or 'Wrote' in result

    @pytest.mark.asyncio
    async def test_edit_file_catches_io_error(self, tmp_path):
        f = tmp_path / 'code.py'
        f.write_text('foo = 1\n', encoding='utf-8')
        from pathlib import Path
        from unittest.mock import patch

        with patch.object(Path, 'read_text', side_effect=PermissionError('denied')):
            result = await edit_file(_ctx(str(tmp_path)), str(f), 'foo', 'bar')
        assert 'error' in result.lower()


class TestOutputTruncation:
    @pytest.mark.asyncio
    async def test_bash_truncates_huge_output(self, tmp_path):
        # Generate more than MAX_OUTPUT_LENGTH chars
        result = await bash(_ctx(str(tmp_path)), 'python3 -c "print(\'x\' * 100000)"')
        assert 'truncated' in result.lower() or len(result) < 110000

    @pytest.mark.asyncio
    async def test_read_file_truncates_huge_file(self, tmp_path):
        f = tmp_path / 'big.txt'
        f.write_text('x' * 60000, encoding='utf-8')
        result = await read_file(_ctx(str(tmp_path)), str(f))
        assert 'truncated' in result.lower() or len(result) < 70000
