"""Tests for ISSUE-006: Marcel CLI — config loading and WebSocket client parsing."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from marcel_cli.config import Config, load_config, _DEFAULTS


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_defaults_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr('marcel_cli.config._CONFIG_PATH', tmp_path / 'config.toml')
        cfg = load_config()
        assert cfg.host == _DEFAULTS['host']
        assert cfg.port == _DEFAULTS['port']
        assert cfg.user == _DEFAULTS['user']

    def test_creates_default_file_when_missing(self, tmp_path, monkeypatch):
        path = tmp_path / 'config.toml'
        monkeypatch.setattr('marcel_cli.config._CONFIG_PATH', path)
        load_config()
        assert path.exists()

    def test_reads_values_from_file(self, tmp_path, monkeypatch):
        path = tmp_path / 'config.toml'
        path.write_text('host = "192.168.1.50"\nport = 9000\nuser = "alice"\ntoken = "abc"\n')
        monkeypatch.setattr('marcel_cli.config._CONFIG_PATH', path)
        cfg = load_config()
        assert cfg.host == '192.168.1.50'
        assert cfg.port == 9000
        assert cfg.user == 'alice'
        assert cfg.token == 'abc'

    def test_flag_overrides_file(self, tmp_path, monkeypatch):
        path = tmp_path / 'config.toml'
        path.write_text('host = "nuc"\nport = 8000\nuser = "shaun"\n')
        monkeypatch.setattr('marcel_cli.config._CONFIG_PATH', path)
        cfg = load_config(host='override-host', port=9999)
        assert cfg.host == 'override-host'
        assert cfg.port == 9999
        assert cfg.user == 'shaun'  # not overridden

    def test_ws_url_format(self, tmp_path, monkeypatch):
        monkeypatch.setattr('marcel_cli.config._CONFIG_PATH', tmp_path / 'c.toml')
        cfg = load_config(host='myhost', port=1234)
        assert cfg.ws_url == 'ws://myhost:1234/ws/chat'


# ---------------------------------------------------------------------------
# ChatClient message parsing (no real network)
# ---------------------------------------------------------------------------

class TestChatClientParsing:
    """Test the internal _receive_tokens logic by feeding it fake WS messages."""

    @pytest.mark.asyncio
    async def test_yields_tokens_from_stream(self):
        from unittest.mock import AsyncMock, MagicMock
        from marcel_cli.chat import ChatClient

        client = ChatClient('ws://localhost:8000/ws/chat', 'shaun')

        messages = [
            json.dumps({'type': 'started', 'conversation': 'conv-1'}),
            json.dumps({'type': 'token', 'text': 'Hello'}),
            json.dumps({'type': 'token', 'text': ' world'}),
            json.dumps({'type': 'done'}),
        ]

        async def fake_iter():
            for m in messages:
                yield m

        mock_conn = MagicMock()
        mock_conn.__aiter__ = lambda self: fake_iter()
        client._conn = mock_conn

        tokens = []
        async for token in client._receive_tokens():
            tokens.append(token)

        assert tokens == ['Hello', ' world']
        assert client._conversation_id == 'conv-1'

    @pytest.mark.asyncio
    async def test_yields_error_message(self):
        from unittest.mock import MagicMock
        from marcel_cli.chat import ChatClient

        client = ChatClient('ws://localhost:8000/ws/chat', 'shaun')

        messages = [
            json.dumps({'type': 'error', 'message': 'Service unavailable'}),
        ]

        async def fake_iter():
            for m in messages:
                yield m

        mock_conn = MagicMock()
        mock_conn.__aiter__ = lambda self: fake_iter()
        client._conn = mock_conn

        tokens = []
        async for token in client._receive_tokens():
            tokens.append(token)

        assert any('Service unavailable' in t for t in tokens)

    @pytest.mark.asyncio
    async def test_conversation_id_persists_across_calls(self):
        from unittest.mock import MagicMock
        from marcel_cli.chat import ChatClient

        client = ChatClient('ws://localhost:8000/ws/chat', 'shaun')

        msgs1 = [
            json.dumps({'type': 'started', 'conversation': 'conv-abc'}),
            json.dumps({'type': 'token', 'text': 'Hi'}),
            json.dumps({'type': 'done'}),
        ]

        async def fake_iter1():
            for m in msgs1:
                yield m

        mock_conn = MagicMock()
        mock_conn.__aiter__ = lambda self: fake_iter1()
        client._conn = mock_conn

        async for _ in client._receive_tokens():
            pass

        assert client._conversation_id == 'conv-abc'
