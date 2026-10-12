"""The usage guard wired into the app (S-10.1): spawns, `GET /api/usage`, the brain's
`usage_get`, `usage.updated` and limit hits through the registry's publisher chain. The runner
socket is absent, so the guard reads nothing on its own; tests hand it readings."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from test_runner_client_app import settings, short_dir  # noqa: F401  (fixture)
from test_tasks_fakes import AGENT, add_agent, run
from test_tasks_routes import pair

from marcel_hub.app import Settings, create_app
from marcel_hub.models import AppEvent, QueuedReason, TaskState
from marcel_hub.usage import LimitHits, UsageGuard


def reading(five: float) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        'five_hour': {'used_pct': five, 'resets_at': (now + timedelta(hours=2)).isoformat()},
        'weekly': {'used_pct': 40.0, 'resets_at': (now + timedelta(days=2)).isoformat()},
        'source': 'cli_usage',
        'taken_at': now.isoformat(),
        'stale': False,
    }


class Reader:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value

    async def usage(self) -> dict[str, Any] | None:
        return self.value


@pytest.fixture
def app_client(tmp_path: Path, short_dir: Path) -> Iterator[TestClient]:  # noqa: F811
    app = create_app(settings(tmp_path, short_dir / 'runner.sock'))
    with TestClient(app) as client:
        add_agent(app.state.engine)
        client.headers['Authorization'] = f'Bearer {pair(client, app.state.engine)}'
        yield client


def chain(publisher: Any) -> list[Any]:
    found = []
    while publisher is not None:
        found.append(publisher)
        publisher = getattr(publisher, 'inner', None)
    return found


def test_the_app_holds_spawns_and_serves_usage(app_client: TestClient) -> None:
    app: Any = app_client.app
    guard: UsageGuard = app.state.usage_guard
    assert app_client.get('/api/usage').json()['code'] == 'no_reading'
    assert app_client.get('/api/usage').status_code == 503
    assert guard in app.state.runner_watch.listeners
    assert app.state.brain_ports.usage is guard
    assert app.state.tasks.hold_new_work == guard.holding

    guard.reader = Reader(reading(91))
    run(guard.tick())
    task = run(app.state.tasks.spawn(AGENT, 'Tidy the photos', prompt='Tidy them.'))

    assert (task.state, task.queued_reason) == (TaskState.queued, QueuedReason.usage)
    body = app_client.get('/api/usage').json()
    assert body['guard']['active'] is True and body['guard']['queued'] == 1
    assert body['guard']['reason'].startswith('Plan usage is at 91 %.')
    with Session(app.state.engine) as s:
        types = [e.type for e in s.exec(select(AppEvent))]
    assert 'usage.updated' in types  # the app hears about it live


def test_limit_hits_reach_the_guard_through_the_registry(app_client: TestClient) -> None:
    app: Any = app_client.app
    guard: UsageGuard = app.state.usage_guard
    assert any(isinstance(p, LimitHits) for p in chain(app.state.tasks.publisher))
    guard.reader = Reader(reading(20))
    run(guard.tick())
    task = run(app.state.tasks.spawn(AGENT, 'Sort the mail', prompt='Sort it.'))

    app.state.tasks.append_transcript(
        task.id,
        {'type': 'error', 'data': {'message': "You've hit your limit", 'usage_limit': True}},
    )

    assert guard.holding() is not None


def test_without_a_runner_usage_is_not_read(tmp_path: Path) -> None:
    app: Any = create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db'))
    with TestClient(app) as client:
        add_agent(app.state.engine)
        client.headers['Authorization'] = f'Bearer {pair(client, app.state.engine)}'
        res = client.get('/api/usage')
    assert res.status_code == 503 and 'no plan usage reading' in res.json()['message']
    assert app.state.usage_guard._job is None  # pyright: ignore[reportPrivateUsage]
