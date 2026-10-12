"""`/api/schedules` through the real app, checked against the contract. Covers the controls: only
the owner of a schedule's agent sees or changes it, and a built-in schedule cannot be deleted."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_conversations import Phone, add_agent, add_phone
from test_tasks_fakes import validator

from marcel_hub.app import Settings, create_app
from marcel_hub.schedule import Scheduler

SCHEDULE = validator('app-api.yaml#/components/schemas/Schedule')
ERROR = validator('app-api.yaml#/components/schemas/Error')
MONDAYS = {'name': 'Dependency report', 'cron': '0 8 * * 1', 'tz': 'Europe/Brussels',
           'location': 'cloud', 'prompt': 'List outdated dependencies.',
           'deadline_minutes': 120}  # fmt: skip


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'm.db'))) as c:
        yield c


class Api:
    def __init__(self, client: TestClient, phone: Phone) -> None:
        self.client, self.phone = client, phone

    def __call__(self, method: str, path: str, **kw: Any) -> Any:
        return self.client.request(method, f'/api{path}', headers=self.phone.headers, **kw)


@pytest.fixture
def api(client: TestClient) -> Api:
    return Api(client, add_phone(client))


@pytest.fixture
def agent(client: TestClient) -> str:
    return add_agent(client)[0]


def create(api: Api, agent: str, **fields: Any) -> dict[str, Any]:
    res = api('POST', '/schedules', json={'agent_id': agent, **MONDAYS, **fields})
    assert res.status_code == 201, res.text
    SCHEDULE.validate(res.json())
    return res.json()


def scheduler(client: TestClient) -> Scheduler:
    return client.app.state.scheduler  # type: ignore[attr-defined]


def test_create_list_pause_resume_and_delete(api: Api, agent: str) -> None:
    made = create(api, agent)
    assert made['paused'] is False and made['builtin'] is False and 'next_run_at' in made

    listed = api('GET', '/schedules', params={'agent_id': agent}).json()
    assert [s['id'] for s in listed] == [made['id']]

    paused = api('PATCH', f'/schedules/{made["id"]}', json={'paused': True})
    assert paused.status_code == 200 and paused.json()['paused'] is True
    SCHEDULE.validate(paused.json())
    assert 'next_run_at' not in paused.json()
    resumed = api('PATCH', f'/schedules/{made["id"]}', json={'paused': False}).json()
    assert resumed['next_run_at'] == made['next_run_at']

    assert api('DELETE', f'/schedules/{made["id"]}').status_code == 204
    assert api('GET', '/schedules', params={'agent_id': agent}).json() == []
    gone = api('DELETE', f'/schedules/{made["id"]}')
    assert gone.status_code == 404 and gone.json()['code'] == 'schedule_not_found'


def test_a_builtin_schedule_can_be_paused_not_deleted(
    api: Api, client: TestClient, agent: str
) -> None:
    builtin = scheduler(client).create(agent, {**MONDAYS, 'name': 'Morning digest'}, builtin=True)
    res = api('DELETE', f'/schedules/{builtin["id"]}')
    assert res.status_code == 409 and res.json()['code'] == 'schedule_builtin'
    ERROR.validate(res.json())
    assert 'paused' in res.json()['message']
    assert api('PATCH', f'/schedules/{builtin["id"]}', json={'paused': True}).status_code == 200


def test_only_the_owner_sees_or_changes_a_schedule(
    api: Api, client: TestClient, agent: str
) -> None:
    made = create(api, agent)
    stranger = Api(client, add_phone(client, user_id='usr_stranger'))
    assert stranger('GET', '/schedules', params={'agent_id': agent}).json() == []
    assert stranger('PATCH', f'/schedules/{made["id"]}', json={'paused': True}).status_code == 404
    assert stranger('DELETE', f'/schedules/{made["id"]}').status_code == 404
    res = stranger('POST', '/schedules', json={'agent_id': agent, **MONDAYS})
    assert res.status_code == 400 and 'not one of your agents' in res.json()['message']
    assert api('GET', '/schedules', params={'agent_id': agent}).json()[0]['paused'] is False


def test_the_routes_need_a_device_token(client: TestClient) -> None:
    assert client.get('/api/schedules', params={'agent_id': 'agt_1'}).status_code == 401


@pytest.mark.parametrize(
    ('change', 'says'),
    [
        ({'cron': '0 8 * *'}, 'five fields'),
        ({'tz': 'Mars/Olympus'}, 'Europe/Brussels'),
        ({'location': 'moon'}, 'location'),
        ({'prompt': ''}, 'prompt'),
        ({'deadline_minutes': 0}, 'deadline_minutes'),
        ({'shell': 'rm -rf /'}, 'shell'),
    ],
)
def test_a_bad_new_schedule_is_a_400(
    api: Api, agent: str, change: dict[str, Any], says: str
) -> None:
    res = api('POST', '/schedules', json={'agent_id': agent, **MONDAYS, **change})
    assert res.status_code == 400 and says in res.json()['message'], res.text


@pytest.mark.parametrize(
    ('change', 'says'),
    [
        ({}, 'at least one field'),
        ({'paused': None}, 'paused cannot be empty'),
        ({'cron': '99 * * * *'}, 'minute field'),
        ({'builtin': False}, 'builtin'),
    ],
)
def test_a_bad_change_is_a_400(api: Api, agent: str, change: dict[str, Any], says: str) -> None:
    made = create(api, agent)
    res = api('PATCH', f'/schedules/{made["id"]}', json=change)
    assert res.status_code == 400 and says in res.json()['message'], res.text


def test_listing_needs_an_agent(api: Api) -> None:
    assert api('GET', '/schedules').status_code == 400


def test_the_scheduler_runs_with_the_app_and_stops_with_it(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'm.db'))
    with TestClient(app):
        job = app.state.scheduler._job  # pyright: ignore[reportPrivateUsage]
        assert job is not None and not job.done()
    assert app.state.scheduler._job is None  # pyright: ignore[reportPrivateUsage]
