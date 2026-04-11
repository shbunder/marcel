"""Tests for api/conversations.py — list and message retrieval endpoints."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from marcel_core.main import app
from marcel_core.memory.conversation import append_to_segment, ensure_channel
from marcel_core.memory.history import HistoryMessage
from marcel_core.storage import _root


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)


class TestListConversations:
    def test_empty_user_returns_empty_list(self):
        client = TestClient(app)
        resp = client.get('/conversations?user=shaun')
        assert resp.status_code == 200
        assert resp.json() == {'conversations': []}

    def test_invalid_user_slug_returns_empty_list(self):
        client = TestClient(app)
        resp = client.get('/conversations?user=INVALID USER!')
        assert resp.status_code == 200
        assert resp.json() == {'conversations': []}

    def test_returns_conversation_list(self):
        ensure_channel('shaun', 'cli')
        append_to_segment(
            'shaun',
            'cli',
            HistoryMessage(
                role='user',
                text='Hello',
                timestamp=datetime.now(tz=timezone.utc),
                conversation_id='cli-default',
            ),
        )

        client = TestClient(app)
        resp = client.get('/conversations?user=shaun')
        assert resp.status_code == 200
        data = resp.json()
        assert len(data['conversations']) == 1
        assert data['conversations'][0]['channel'] == 'cli'

    def test_respects_limit(self):
        for ch in ['cli', 'telegram', 'ios', 'web', 'sms']:
            ensure_channel('shaun', ch)

        client = TestClient(app)
        resp = client.get('/conversations?user=shaun&limit=3')
        assert resp.status_code == 200
        assert len(resp.json()['conversations']) <= 3

    def test_channel_in_response(self):
        ensure_channel('shaun', 'telegram')
        append_to_segment(
            'shaun',
            'telegram',
            HistoryMessage(
                role='user',
                text='hi',
                timestamp=datetime.now(tz=timezone.utc),
                conversation_id='telegram-123',
            ),
        )

        client = TestClient(app)
        resp = client.get('/conversations?user=shaun')
        data = resp.json()
        conv = next((c for c in data['conversations'] if c['channel'] == 'telegram'), None)
        assert conv is not None
        assert conv['channel'] == 'telegram'


class TestGetLastMessage:
    def test_no_initdata_returns_400(self):
        client = TestClient(app)
        resp = client.get('/api/message/some-conv')
        assert resp.status_code == 400

    def test_invalid_initdata_returns_401(self):
        client = TestClient(app)
        resp = client.get('/api/message/some-conv?initData=garbage')
        assert resp.status_code == 401

    def test_missing_conversation_returns_404(self, monkeypatch):
        import hashlib
        import hmac
        import json
        import time
        from urllib.parse import quote, urlencode

        from marcel_core.channels.telegram.sessions import link_user
        from marcel_core.config import settings

        bot_token = 'test-bot-token'
        monkeypatch.setattr(settings, 'telegram_bot_token', bot_token)

        # Build valid initData string
        auth_date = str(int(time.time()))
        user_json = json.dumps({'id': 12345})
        pairs = sorted([('auth_date', auth_date), ('user', user_json)])
        data_check = '\n'.join(f'{k}={v}' for k, v in pairs)
        secret = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
        hash_val = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        raw_init_data = urlencode(pairs + [('hash', hash_val)])

        link_user('shaun', 12345)

        client = TestClient(app)
        resp = client.get(f'/api/message/nonexistent-conv?initData={quote(raw_init_data)}')
        assert resp.status_code == 404
