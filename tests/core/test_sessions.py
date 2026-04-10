"""Tests for agent/sessions.py — SessionManager lifecycle."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marcel_core.agent.sessions import SessionManager


def _make_mock_client():
    """Return a mock ClaudeSDKClient that connects and disconnects cleanly."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    return client


@pytest.fixture
def manager():
    return SessionManager(idle_timeout=3600)


class TestSessionManagerDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_existing_session(self, manager):
        # Manually insert a session
        from marcel_core.agent.sessions import ActiveSession

        client = _make_mock_client()
        session = ActiveSession(client=client, user_slug='alice', conversation_id='c1', channel='cli')
        manager._sessions[('alice', 'c1')] = session

        await manager.disconnect('alice', 'c1')
        client.disconnect.assert_called_once()
        assert ('alice', 'c1') not in manager._sessions

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent_session_is_noop(self, manager):
        # Should not raise
        await manager.disconnect('nobody', 'missing-conv')
        assert manager.active_count == 0

    @pytest.mark.asyncio
    async def test_disconnect_all(self, manager):
        from marcel_core.agent.sessions import ActiveSession

        clients = [_make_mock_client() for _ in range(3)]
        for i, client in enumerate(clients):
            session = ActiveSession(client=client, user_slug='u', conversation_id=f'c{i}', channel='cli')
            manager._sessions[('u', f'c{i}')] = session

        await manager.disconnect_all()
        assert manager.active_count == 0
        for client in clients:
            client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_error_is_swallowed(self, manager):
        from marcel_core.agent.sessions import ActiveSession

        client = _make_mock_client()
        client.disconnect = AsyncMock(side_effect=Exception('disconnect error'))
        session = ActiveSession(client=client, user_slug='u', conversation_id='c1', channel='cli')
        manager._sessions[('u', 'c1')] = session

        # Should not raise
        await manager.disconnect_all()
        assert manager.active_count == 0


class TestSessionManagerCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_idle_removes_stale(self, manager):
        import time

        from marcel_core.agent.sessions import ActiveSession

        client = _make_mock_client()
        session = ActiveSession(client=client, user_slug='u', conversation_id='c1', channel='cli')
        # Make it look like it's been idle for a long time
        session.last_active = time.monotonic() - 7200  # 2 hours ago
        manager._sessions[('u', 'c1')] = session

        removed = await manager.cleanup_idle()
        assert removed == 1
        assert manager.active_count == 0

    @pytest.mark.asyncio
    async def test_cleanup_idle_keeps_fresh(self, manager):
        from marcel_core.agent.sessions import ActiveSession

        client = _make_mock_client()
        session = ActiveSession(client=client, user_slug='u', conversation_id='c1', channel='cli')
        # Just created — very fresh
        manager._sessions[('u', 'c1')] = session

        removed = await manager.cleanup_idle()
        assert removed == 0
        assert manager.active_count == 1

    @pytest.mark.asyncio
    async def test_start_and_stop_cleanup_loop(self, manager):
        manager.start_cleanup_loop()
        assert manager._cleanup_task is not None
        assert not manager._cleanup_task.done()

        manager.stop_cleanup_loop()
        await asyncio.sleep(0)  # allow cancellation
        assert manager._cleanup_task.cancelled() or manager._cleanup_task.done()

    @pytest.mark.asyncio
    async def test_start_cleanup_loop_idempotent(self, manager):
        manager.start_cleanup_loop()
        task1 = manager._cleanup_task
        manager.start_cleanup_loop()
        task2 = manager._cleanup_task
        # Should not create a second task
        assert task1 is task2
        manager.stop_cleanup_loop()

    @pytest.mark.asyncio
    async def test_stop_cleanup_loop_noop_when_not_started(self, manager):
        # Should not raise
        manager.stop_cleanup_loop()


class TestSessionManagerGetOrCreate:
    @pytest.mark.asyncio
    async def test_returns_existing_session(self, manager):
        from marcel_core.agent.sessions import ActiveSession

        client = _make_mock_client()
        session = ActiveSession(client=client, user_slug='u', conversation_id='c1', channel='cli')
        manager._sessions[('u', 'c1')] = session

        with patch('marcel_core.agent.sessions.build_system_prompt', return_value='sys'):
            with patch('marcel_core.agent.sessions.ClaudeSDKClient', return_value=_make_mock_client()):
                result = await manager.get_or_create('u', 'c1', 'cli')

        assert result is session

    @pytest.mark.asyncio
    async def test_creates_new_session(self, manager):
        mock_client = _make_mock_client()
        with patch('marcel_core.agent.sessions.build_system_prompt', return_value='sys'):
            with patch('marcel_core.agent.sessions.ClaudeSDKClient', return_value=mock_client):
                with patch('marcel_core.agent.sessions.build_skills_mcp_server', return_value=MagicMock()):
                    result = await manager.get_or_create('alice', 'conv-new', 'cli')

        assert result.user_slug == 'alice'
        assert result.conversation_id == 'conv-new'
        mock_client.connect.assert_called_once()
