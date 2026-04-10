"""Tests for harness/context.py — MarcelDeps, build_instructions, and server context."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from marcel_core.harness.context import (
    MarcelDeps,
    build_instructions,
    build_instructions_async,
    build_server_context,
)
from marcel_core.storage import _root

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

    def test_includes_home_directory(self):
        from pathlib import Path

        result = build_server_context()
        assert str(Path.home()) in result

    def test_includes_hostname_when_available(self, monkeypatch):
        from pathlib import Path

        original_read_text = Path.read_text

        def patched_read_text(self, *args, **kwargs):
            if str(self) == '/etc/hostname':
                return 'myserver\n'
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, 'read_text', patched_read_text)
        result = build_server_context()
        assert 'myserver' in result

    def test_hostname_oserror_is_swallowed(self, monkeypatch):
        """OSError reading /etc/hostname is silently skipped."""
        from pathlib import Path

        original_read_text = Path.read_text

        def patched_read_text(self, *args, **kwargs):
            if str(self) == '/etc/hostname':
                raise OSError('no permission')
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, 'read_text', patched_read_text)
        result = build_server_context()
        assert '## Server Context (Admin)' in result

    def test_docker_socket_shown_when_available(self, monkeypatch):
        from pathlib import Path

        original_exists = Path.exists

        def patched_exists(self):
            if str(self) == '/var/run/docker.sock':
                return True
            return original_exists(self)

        monkeypatch.setattr(Path, 'exists', patched_exists)
        result = build_server_context()
        assert 'docker' in result.lower()


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
