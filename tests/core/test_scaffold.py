"""Tests for ISSUE-001: marcel-core server scaffold.

WebSocket protocol tests have moved to test_agent.py now that the echo
stub has been replaced with the real agent endpoint (ISSUE-003).
"""


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
