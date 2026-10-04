from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from marcel_mock_hub import contract
from marcel_mock_hub.app import create_app

AUTH = {'Authorization': 'Bearer mrc_test'}


@pytest.fixture
def client() -> Iterator[TestClient]:
    """One event loop for the whole test, so the scenario keeps running between requests."""
    with TestClient(create_app(step_delay=0), headers=AUTH) as c:
        yield c


def frames_until(ws: WebSocketTestSession, stop: tuple[str, str | None]) -> list[dict[str, Any]]:
    """Read frames up to and including the first whose (type, tag) matches `stop`."""
    seen: list[dict[str, Any]] = []
    while True:
        frame = ws.receive_json()
        contract.validate_event(frame)
        seen.append(frame)
        if (frame['type'], tag(frame)) == stop:
            return seen


def tag(frame: dict[str, Any]) -> str | None:
    """What distinguishes frames of one type: the state, kind or event they carry."""
    data = frame['data']
    match frame['type']:
        case 'agent.state':
            return data['state']['kind']
        case 'task.updated':
            return data['state']
        case 'task.event':
            return data['type']
        case 'message.created':
            return data['milestone']['event'] if data['kind'] == 'milestone' else data['kind']
        case 'artifact.created':
            return data['kind']
        case _:
            return None
