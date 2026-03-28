"""Tests for ISSUE-001: marcel-core server scaffold."""

import json

import pytest
from fastapi.testclient import TestClient

from marcel_core import __version__
from marcel_core.main import app

client = TestClient(app)


def test_health_returns_200() -> None:
    response = client.get('/health')
    assert response.status_code == 200


def test_health_body() -> None:
    response = client.get('/health')
    body = response.json()
    assert body['status'] == 'ok'
    assert body['version'] == __version__


def test_websocket_echo() -> None:
    with client.websocket_connect('/ws/chat') as ws:
        ws.send_text(json.dumps({'text': 'hello'}))

        token_msg = json.loads(ws.receive_text())
        assert token_msg['type'] == 'token'
        assert token_msg['text'] == 'echo: hello'

        done_msg = json.loads(ws.receive_text())
        assert done_msg['type'] == 'done'


def test_websocket_empty_text() -> None:
    with client.websocket_connect('/ws/chat') as ws:
        ws.send_text(json.dumps({'text': ''}))

        token_msg = json.loads(ws.receive_text())
        assert token_msg['type'] == 'token'
        assert token_msg['text'] == 'echo: '
