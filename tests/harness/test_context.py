"""Tests for harness/context.py — MarcelDeps, build_instructions, and server context."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from marcel_core.harness.context import (
    MarcelDeps,
    _host_home,
    build_instructions,
    build_instructions_async,
    build_server_context,
)
from marcel_core.storage import _root

# ---------------------------------------------------------------------------
# _host_home
# ---------------------------------------------------------------------------


class TestHostHome:
    def test_returns_host_home_env(self, monkeypatch):
        monkeypatch.setenv('HOST_HOME', '/host/home/user')
        monkeypatch.delenv('MARCEL_DATA_DIR', raising=False)
        assert _host_home() == '/host/home/user'

    def test_derives_from_data_dir(self, monkeypatch):
        monkeypatch.delenv('HOST_HOME', raising=False)
        monkeypatch.setenv('MARCEL_DATA_DIR', '/data/user/.marcel')
        result = _host_home()
        assert result == '/data/user'

    def test_falls_back_to_home(self, monkeypatch):
        monkeypatch.delenv('HOST_HOME', raising=False)
        monkeypatch.delenv('MARCEL_DATA_DIR', raising=False)
        monkeypatch.setenv('HOME', '/home/testuser')
        result = _host_home()
        assert result == '/home/testuser'


# ---------------------------------------------------------------------------
# build_server_context
# ---------------------------------------------------------------------------


class TestBuildServerContext:
    def test_returns_string(self):
        result = build_server_context()
        assert isinstance(result, str)
        assert '## Server Context (Admin)' in result

    def test_includes_working_directory(self):
        result = build_server_context(cwd='/some/path')
        assert '/some/path' in result

    def test_includes_home_directory(self, monkeypatch):
        monkeypatch.setenv('HOST_HOME', '/host/shaun')
        monkeypatch.delenv('MARCEL_DATA_DIR', raising=False)
        result = build_server_context()
        assert '/host/shaun' in result

    def test_mentions_bare_metal_when_not_in_docker(self, tmp_path, monkeypatch):
        # /.dockerenv doesn't exist in test environment
        result = build_server_context()
        assert 'Bare metal' in result or 'Docker' in result

    def test_in_docker_path(self, monkeypatch):
        """When /.dockerenv exists, shows Docker runtime info."""
        from pathlib import Path

        original_exists = Path.exists

        def patched_exists(self):
            if str(self) == '/.dockerenv':
                return True
            if str(self) in ('/_host/etc/hostname', '/_host', '/var/run/docker.sock'):
                return False
            return original_exists(self)

        monkeypatch.setattr(Path, 'exists', patched_exists)
        result = build_server_context()
        assert 'Docker container' in result

    def test_in_docker_with_hostname_and_host_fs(self, monkeypatch):
        """Tests docker path with hostname file and /_host and docker.sock."""
        from pathlib import Path

        original_exists = Path.exists
        original_read_text = Path.read_text

        def patched_exists(self):
            if str(self) == '/.dockerenv':
                return True
            if str(self) == '/_host/etc/hostname':
                return True
            if str(self) == '/_host':
                return True
            if str(self) == '/var/run/docker.sock':
                return True
            return original_exists(self)

        def patched_read_text(self, *args, **kwargs):
            if str(self) == '/_host/etc/hostname':
                return 'myhost\n'
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, 'exists', patched_exists)
        monkeypatch.setattr(Path, 'read_text', patched_read_text)
        result = build_server_context()
        assert 'Docker container' in result
        assert 'myhost' in result
        assert 'Host filesystem' in result
        assert 'docker.sock' in result

    def test_docker_hostname_oserror_is_swallowed(self, monkeypatch):
        """OSError reading /_host/etc/hostname in Docker is silently ignored."""
        from pathlib import Path

        original_exists = Path.exists
        original_read_text = Path.read_text

        def patched_exists(self):
            if str(self) == '/.dockerenv':
                return True
            if str(self) == '/_host/etc/hostname':
                return True
            if str(self) in ('/_host', '/var/run/docker.sock'):
                return False
            return original_exists(self)

        def patched_read_text(self, *args, **kwargs):
            if str(self) == '/_host/etc/hostname':
                raise OSError('no permission')
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, 'exists', patched_exists)
        monkeypatch.setattr(Path, 'read_text', patched_read_text)
        result = build_server_context()
        assert 'Docker container' in result

    def test_bare_metal_hostname_oserror(self, monkeypatch):
        """OSError reading /etc/hostname on bare metal is silently skipped."""
        from pathlib import Path

        original_exists = Path.exists
        original_read_text = Path.read_text

        def patched_exists(self):
            if str(self) == '/.dockerenv':
                return False
            return original_exists(self)

        def patched_read_text(self, *args, **kwargs):
            if str(self) == '/etc/hostname':
                raise OSError('no permission')
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, 'exists', patched_exists)
        monkeypatch.setattr(Path, 'read_text', patched_read_text)
        result = build_server_context()
        assert 'Bare metal' in result


# ---------------------------------------------------------------------------
# build_instructions
# ---------------------------------------------------------------------------


class TestBuildInstructions:
    def test_includes_user_slug(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='alice', conversation_id='conv-1', channel='cli')
        result = build_instructions(deps)
        assert 'alice' in result

    def test_includes_channel_hint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='bob', conversation_id='conv-1', channel='telegram')
        result = build_instructions(deps)
        assert 'telegram' in result.lower()

    def test_cli_channel_hint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='bob', conversation_id='conv-1', channel='cli')
        result = build_instructions(deps)
        assert 'markdown' in result.lower()

    def test_admin_role_includes_server_context(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='admin', conversation_id='conv-1', channel='cli', role='admin')
        result = build_instructions(deps)
        assert 'Server Context' in result

    def test_user_role_excludes_server_context(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='alice', conversation_id='conv-1', channel='cli', role='user')
        result = build_instructions(deps)
        assert 'Server Context' not in result

    def test_includes_profile_when_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        # Write a profile file
        user_dir = tmp_path / 'users' / 'carol'
        user_dir.mkdir(parents=True)
        (user_dir / 'profile.md').write_text('Carol is a data scientist.', encoding='utf-8')

        deps = MarcelDeps(user_slug='carol', conversation_id='conv-1', channel='app')
        result = build_instructions(deps)
        assert 'Carol is a data scientist.' in result

    def test_unknown_channel_falls_back_to_cli_hint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='bob', conversation_id='conv-1', channel='unknown-channel')
        result = build_instructions(deps)
        assert isinstance(result, str)
        assert 'bob' in result

    @pytest.mark.parametrize('channel', ['cli', 'app', 'ios', 'telegram', 'websocket'])
    def test_all_known_channels(self, tmp_path, monkeypatch, channel):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='user', conversation_id='conv-1', channel=channel)
        result = build_instructions(deps)
        assert isinstance(result, str)
        assert len(result) > 10


# ---------------------------------------------------------------------------
# build_instructions_async
# ---------------------------------------------------------------------------


class TestBuildInstructionsAsync:
    @pytest.mark.asyncio
    async def test_includes_user_slug(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='dan', conversation_id='conv-1', channel='cli')
        with patch('marcel_core.memory.selector.select_relevant_memories', AsyncMock(return_value=[])):
            result = await build_instructions_async(deps, query='hello')
        assert 'dan' in result

    @pytest.mark.asyncio
    async def test_includes_selected_memories(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='dan', conversation_id='conv-1', channel='cli')
        memories = [('mem1', 'Dan likes cats.'), ('mem2', 'Dan is a developer.')]
        with patch('marcel_core.memory.selector.select_relevant_memories', AsyncMock(return_value=memories)):
            result = await build_instructions_async(deps, query='what does Dan like?')
        assert 'cats' in result
        assert 'developer' in result
        assert '## Memory' in result

    @pytest.mark.asyncio
    async def test_graceful_fallback_when_memory_selection_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='dan', conversation_id='conv-1', channel='cli')
        with patch(
            'marcel_core.memory.selector.select_relevant_memories',
            AsyncMock(side_effect=Exception('selector down')),
        ):
            result = await build_instructions_async(deps, query='anything')
        # Should still return a valid prompt (no crash)
        assert 'dan' in result
        assert '## Memory' not in result  # Memory section skipped on failure

    @pytest.mark.asyncio
    async def test_no_query_skips_memory_selection(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='dan', conversation_id='conv-1', channel='cli')
        with patch('marcel_core.memory.selector.select_relevant_memories', AsyncMock()) as mock_select:
            result = await build_instructions_async(deps, query='')
        # Without a query, memory selection is skipped
        mock_select.assert_not_called()
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_admin_includes_server_context(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='admin', conversation_id='conv-1', channel='cli', role='admin')
        # No query — memory selection skipped, no need to mock
        result = await build_instructions_async(deps)
        assert 'Server Context' in result
