"""Scenario-based tests for the REST API endpoints.

Covers: api/artifacts.py, api/conversations.py endpoints through
the FastAPI test client with mocked auth.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from marcel_core.api.artifacts import router as artifacts_router
from marcel_core.api.conversations import router as conversations_router
from marcel_core.storage import _root


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)


@pytest.fixture
def art_client():
    app = FastAPI()
    app.include_router(artifacts_router)
    return TestClient(app)


@pytest.fixture
def conv_client():
    app = FastAPI()
    app.include_router(conversations_router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Artifacts API
# ---------------------------------------------------------------------------


class TestArtifactsAPI:
    def test_get_artifact_unauthorized(self, art_client):
        with (
            patch('marcel_core.api.artifacts.verify_telegram_init_data', return_value=None),
            patch('marcel_core.api.artifacts.verify_api_token', return_value=False),
        ):
            resp = art_client.get('/api/artifact/abc123', params={'initData': 'bad'})
        assert resp.status_code == 401

    def test_get_artifact_not_found(self, art_client):
        with (
            patch('marcel_core.api.artifacts.verify_telegram_init_data', return_value={'id': 111}),
            patch('marcel_core.api.artifacts.get_telegram_user_slug', return_value='alice'),
            patch('marcel_core.api.artifacts.load_artifact', return_value=None),
        ):
            resp = art_client.get('/api/artifact/abc123', params={'initData': 'valid'})
        assert resp.status_code == 404

    def test_get_artifact_success(self, art_client):
        from marcel_core.storage.artifacts import Artifact

        artifact = Artifact(
            id='abc123',
            user_slug='alice',
            conversation_id='conv-1',
            content_type='markdown',
            content='# Hello',
            title='Test',
            created_at=datetime.now(timezone.utc),
        )
        with (
            patch('marcel_core.api.artifacts.verify_telegram_init_data', return_value={'id': 111}),
            patch('marcel_core.api.artifacts.get_telegram_user_slug', return_value='alice'),
            patch('marcel_core.api.artifacts.load_artifact', return_value=artifact),
        ):
            resp = art_client.get('/api/artifact/abc123', params={'initData': 'valid'})
        assert resp.status_code == 200
        data = resp.json()
        assert data['id'] == 'abc123'
        assert data['content_type'] == 'markdown'

    def test_get_artifact_wrong_user(self, art_client):
        from marcel_core.storage.artifacts import Artifact

        artifact = Artifact(
            id='abc123',
            user_slug='bob',
            conversation_id='conv-1',
            content_type='markdown',
            content='secret',
            title='Bob only',
        )
        with (
            patch('marcel_core.api.artifacts.verify_telegram_init_data', return_value={'id': 111}),
            patch('marcel_core.api.artifacts.get_telegram_user_slug', return_value='alice'),
            patch('marcel_core.api.artifacts.load_artifact', return_value=artifact),
        ):
            resp = art_client.get('/api/artifact/abc123', params={'initData': 'valid'})
        assert resp.status_code == 404

    def test_list_artifacts(self, art_client):
        from marcel_core.storage.artifacts import ArtifactSummary

        items = [
            ArtifactSummary(id='a1', title='Art 1', content_type='markdown', created_at=datetime.now(timezone.utc)),
        ]
        with (
            patch('marcel_core.api.artifacts.verify_telegram_init_data', return_value={'id': 111}),
            patch('marcel_core.api.artifacts.get_telegram_user_slug', return_value='alice'),
            patch('marcel_core.api.artifacts.list_artifacts', return_value=items),
        ):
            resp = art_client.get('/api/artifacts', params={'initData': 'valid'})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data['artifacts']) == 1

    def test_artifact_file_success(self, art_client, tmp_path):
        from marcel_core.storage.artifacts import Artifact

        artifact = Artifact(
            id='img1',
            user_slug='alice',
            conversation_id='conv-1',
            content_type='image',
            content='img1.png',
            title='Chart',
        )
        # Create the file
        files_dir = tmp_path / 'artifacts' / 'files'
        files_dir.mkdir(parents=True)
        (files_dir / 'img1.png').write_bytes(b'fake png')

        with (
            patch('marcel_core.api.artifacts.verify_telegram_init_data', return_value={'id': 111}),
            patch('marcel_core.api.artifacts.get_telegram_user_slug', return_value='alice'),
            patch('marcel_core.api.artifacts.load_artifact', return_value=artifact),
            patch('marcel_core.api.artifacts.files_dir', return_value=files_dir),
        ):
            resp = art_client.get('/api/artifact/img1/file', params={'initData': 'valid'})
        assert resp.status_code == 200

    def test_artifact_file_not_image(self, art_client):
        from marcel_core.storage.artifacts import Artifact

        artifact = Artifact(
            id='md1',
            user_slug='alice',
            conversation_id='conv-1',
            content_type='markdown',
            content='text',
            title='Doc',
        )
        with (
            patch('marcel_core.api.artifacts.verify_telegram_init_data', return_value={'id': 111}),
            patch('marcel_core.api.artifacts.get_telegram_user_slug', return_value='alice'),
            patch('marcel_core.api.artifacts.load_artifact', return_value=artifact),
        ):
            resp = art_client.get('/api/artifact/md1/file', params={'initData': 'valid'})
        assert resp.status_code == 400

    def test_auth_via_bearer_requires_initdata(self, art_client):
        with (
            patch('marcel_core.api.artifacts.verify_api_token', return_value=True),
        ):
            resp = art_client.get(
                '/api/artifact/abc',
                headers={'Authorization': 'Bearer valid'},
            )
        assert resp.status_code == 400

    def test_tg_user_not_linked(self, art_client):
        with (
            patch('marcel_core.api.artifacts.verify_telegram_init_data', return_value={'id': 111}),
            patch('marcel_core.api.artifacts.get_telegram_user_slug', return_value=None),
        ):
            resp = art_client.get('/api/artifact/abc', params={'initData': 'valid'})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Conversations API
# ---------------------------------------------------------------------------


class TestConversationsAPI:
    def test_list_conversations_unauthorized(self, conv_client):
        with patch('marcel_core.api.conversations.verify_api_token', return_value=False):
            resp = conv_client.get('/conversations', params={'user': 'alice'}, headers={'Authorization': 'Bearer bad'})
        assert resp.status_code == 200
        assert resp.json()['conversations'] == []

    def test_list_conversations_invalid_user(self, conv_client):
        with (
            patch('marcel_core.api.conversations.verify_api_token', return_value=True),
            patch('marcel_core.api.conversations.valid_user_slug', return_value=False),
        ):
            resp = conv_client.get('/conversations', params={'user': '../hack'}, headers={'Authorization': 'Bearer ok'})
        assert resp.json()['conversations'] == []

    def test_list_conversations_success(self, conv_client):
        from marcel_core.memory.conversation import ChannelMeta

        now = datetime.now(timezone.utc)
        channels = [
            ChannelMeta(
                channel='telegram', active_segment='seg-1', created_at=now, last_active=now, next_segment_num=2
            ),
        ]
        with (
            patch('marcel_core.api.conversations.verify_api_token', return_value=True),
            patch('marcel_core.api.conversations.valid_user_slug', return_value=True),
            patch('marcel_core.api.conversations.list_channels', return_value=channels),
        ):
            resp = conv_client.get('/conversations', params={'user': 'alice'}, headers={'Authorization': 'Bearer ok'})
        data = resp.json()
        assert len(data['conversations']) == 1

    def test_get_history_success(self, conv_client):
        from marcel_core.memory.history import HistoryMessage

        messages = [
            HistoryMessage(role='user', text='Hello', timestamp=datetime.now(timezone.utc), conversation_id='test'),
            HistoryMessage(role='assistant', text='Hi!', timestamp=datetime.now(timezone.utc), conversation_id='test'),
        ]
        with (
            patch('marcel_core.api.conversations.verify_api_token', return_value=True),
            patch('marcel_core.api.conversations.valid_user_slug', return_value=True),
            patch('marcel_core.api.conversations.load_latest_summary', return_value=None),
            patch('marcel_core.api.conversations.read_active_segment', return_value=messages),
        ):
            resp = conv_client.get(
                '/api/history', params={'user': 'alice', 'channel': 'cli'}, headers={'Authorization': 'Bearer ok'}
            )
        data = resp.json()
        assert len(data['messages']) == 2
        assert data['summary'] is None

    def test_get_history_unauthorized(self, conv_client):
        with patch('marcel_core.api.conversations.verify_api_token', return_value=False):
            resp = conv_client.get('/api/history', params={'user': 'alice'}, headers={'Authorization': 'Bearer bad'})
        assert resp.status_code == 401

    def test_get_history_invalid_user(self, conv_client):
        with (
            patch('marcel_core.api.conversations.verify_api_token', return_value=True),
            patch('marcel_core.api.conversations.valid_user_slug', return_value=False),
        ):
            resp = conv_client.get('/api/history', params={'user': '../x'}, headers={'Authorization': 'Bearer ok'})
        assert resp.status_code == 400

    def test_forget_endpoint_success(self, conv_client):
        from marcel_core.memory.conversation import SegmentSummary

        summary = SegmentSummary(
            segment_id='seg-1',
            created_at=datetime.now(timezone.utc),
            trigger='manual',
            message_count=10,
            time_span_from=datetime.now(timezone.utc),
            time_span_to=datetime.now(timezone.utc),
            summary='The user asked about weather.',
        )
        with (
            patch('marcel_core.api.conversations.verify_api_token', return_value=True),
            patch('marcel_core.api.conversations.valid_user_slug', return_value=True),
            patch('marcel_core.memory.conversation.has_active_content', return_value=True),
            patch('marcel_core.memory.summarizer.summarize_active_segment', new_callable=AsyncMock, return_value=True),
            patch('marcel_core.api.conversations.load_latest_summary', return_value=summary),
        ):
            resp = conv_client.post(
                '/api/forget', params={'user': 'alice', 'channel': 'cli'}, headers={'Authorization': 'Bearer ok'}
            )
        data = resp.json()
        assert data['success'] is True
        assert 'Compressed' in data['message']

    def test_forget_endpoint_no_content(self, conv_client):
        with (
            patch('marcel_core.api.conversations.verify_api_token', return_value=True),
            patch('marcel_core.api.conversations.valid_user_slug', return_value=True),
            patch('marcel_core.memory.conversation.has_active_content', return_value=False),
        ):
            resp = conv_client.post('/api/forget', params={'user': 'alice'}, headers={'Authorization': 'Bearer ok'})
        data = resp.json()
        assert data['success'] is True
        assert 'Nothing to compress' in data['message']

    def test_forget_endpoint_failure(self, conv_client):
        with (
            patch('marcel_core.api.conversations.verify_api_token', return_value=True),
            patch('marcel_core.api.conversations.valid_user_slug', return_value=True),
            patch('marcel_core.memory.conversation.has_active_content', return_value=True),
            patch('marcel_core.memory.summarizer.summarize_active_segment', new_callable=AsyncMock, return_value=False),
        ):
            resp = conv_client.post('/api/forget', params={'user': 'alice'}, headers={'Authorization': 'Bearer ok'})
        data = resp.json()
        assert data['success'] is False

    def test_get_message_deprecated(self, conv_client):
        from marcel_core.memory.history import HistoryMessage

        messages = [
            HistoryMessage(
                role='assistant', text='Response text', timestamp=datetime.now(timezone.utc), conversation_id='test'
            ),
        ]
        with (
            patch('marcel_core.api.conversations.verify_telegram_init_data', return_value={'id': 111}),
            patch('marcel_core.api.conversations.get_telegram_user_slug', return_value='alice'),
            patch('marcel_core.api.conversations.read_active_segment', return_value=messages),
        ):
            resp = conv_client.get('/api/message/conv-1', params={'initData': 'valid'})
        data = resp.json()
        assert data['content'] == 'Response text'

    def test_get_message_not_found(self, conv_client):
        with (
            patch('marcel_core.api.conversations.verify_telegram_init_data', return_value={'id': 111}),
            patch('marcel_core.api.conversations.get_telegram_user_slug', return_value='alice'),
            patch('marcel_core.api.conversations.read_active_segment', return_value=[]),
        ):
            resp = conv_client.get('/api/message/conv-1', params={'initData': 'valid'})
        assert resp.status_code == 404

    def test_get_message_by_turn(self, conv_client):
        from marcel_core.memory.history import HistoryMessage

        messages = [
            HistoryMessage(
                role='assistant', text='First', timestamp=datetime.now(timezone.utc), conversation_id='test'
            ),
            HistoryMessage(
                role='assistant', text='Second', timestamp=datetime.now(timezone.utc), conversation_id='test'
            ),
        ]
        with (
            patch('marcel_core.api.conversations.verify_telegram_init_data', return_value={'id': 111}),
            patch('marcel_core.api.conversations.get_telegram_user_slug', return_value='alice'),
            patch('marcel_core.api.conversations.read_active_segment', return_value=messages),
        ):
            resp = conv_client.get('/api/message/conv-1', params={'initData': 'valid', 'turn': 0})
        assert resp.json()['content'] == 'First'

    def test_get_message_turn_out_of_range(self, conv_client):
        from marcel_core.memory.history import HistoryMessage

        messages = [
            HistoryMessage(role='assistant', text='Only', timestamp=datetime.now(timezone.utc), conversation_id='test'),
        ]
        with (
            patch('marcel_core.api.conversations.verify_telegram_init_data', return_value={'id': 111}),
            patch('marcel_core.api.conversations.get_telegram_user_slug', return_value='alice'),
            patch('marcel_core.api.conversations.read_active_segment', return_value=messages),
        ):
            resp = conv_client.get('/api/message/conv-1', params={'initData': 'valid', 'turn': 5})
        assert resp.status_code == 404

    def test_get_message_auth_via_bearer(self, conv_client):
        with (
            patch('marcel_core.api.conversations.verify_api_token', return_value=True),
        ):
            resp = conv_client.get(
                '/api/message/conv-1',
                headers={'Authorization': 'Bearer ok'},
            )
        # Bearer auth requires initData for this endpoint
        assert resp.status_code == 400
