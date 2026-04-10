"""Tests for api/conversations.py — list and message retrieval endpoints."""

import pytest
from fastapi.testclient import TestClient

from marcel_core.main import app
from marcel_core.storage import _root, append_turn, new_conversation


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

    def test_returns_conversation_list(self, tmp_path):
        conv_id = new_conversation('shaun', 'cli')
        append_turn('shaun', conv_id, 'user', 'Hello')
        append_turn('shaun', conv_id, 'assistant', 'Hi there!')

        client = TestClient(app)
        resp = client.get('/conversations?user=shaun')
        assert resp.status_code == 200
        data = resp.json()
        assert len(data['conversations']) == 1
        assert data['conversations'][0]['id'] == conv_id

    def test_respects_limit(self, tmp_path):
        for _ in range(5):
            conv_id = new_conversation('shaun', 'cli')
            append_turn('shaun', conv_id, 'user', 'msg')

        client = TestClient(app)
        resp = client.get('/conversations?user=shaun&limit=3')
        assert resp.status_code == 200
        assert len(resp.json()['conversations']) <= 3

    def test_channel_parsed_from_header(self, tmp_path):
        conv_id = new_conversation('shaun', 'telegram')
        append_turn('shaun', conv_id, 'user', 'hi')

        client = TestClient(app)
        resp = client.get('/conversations?user=shaun')
        data = resp.json()
        conv = next((c for c in data['conversations'] if c['id'] == conv_id), None)
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

    def test_missing_conversation_returns_404(self, tmp_path, monkeypatch):
        import hashlib
        import hmac
        import json
        import time
        from urllib.parse import quote, urlencode

        from marcel_core.channels.telegram.sessions import link_user
        from marcel_core.config import settings

        bot_token = 'test-bot-token'
        monkeypatch.setattr(settings, 'telegram_bot_token', bot_token)

        # Build valid initData string (same as in test_auth.py helper)
        auth_date = str(int(time.time()))
        user_json = json.dumps({'id': 12345})
        pairs = sorted([('auth_date', auth_date), ('user', user_json)])
        data_check = '\n'.join(f'{k}={v}' for k, v in pairs)
        secret = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
        hash_val = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        raw_init_data = urlencode(pairs + [('hash', hash_val)])

        # Link user before making request
        link_user('shaun', 12345)

        client = TestClient(app)
        # URL-encode initData so the embedded & and = are preserved as a single param
        resp = client.get(f'/api/message/nonexistent-conv?initData={quote(raw_init_data)}')
        assert resp.status_code == 404
