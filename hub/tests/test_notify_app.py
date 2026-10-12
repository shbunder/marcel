"""Pushes wired into the hub: task processing with a dead ntfy server, the runner alert, `/me`,
and the `ntfy_server` setting."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, select
from test_auth import bearer, pair, validate
from test_notify_fakes import NTFY, TOPIC, FakeNtfy, add_device, pusher_for
from test_runner_client_fakes import FakeRunnerServer, Sleeps, until, watch_for
from test_tasks_fakes import (  # noqa: F401  (fixtures)
    AGENT,
    FakeRunner,
    MemoryOutbox,
    RecordingPublisher,
    add_agent,
    run,
)

from marcel_hub import db
from marcel_hub.app import Settings, SettingsError, create_app, load_settings
from marcel_hub.models import Agent, AppEvent, TaskState
from marcel_hub.notify import Pusher, TaskPushes
from marcel_hub.tasks.registry import TaskRegistry


def create_engine_for(settings: Settings) -> Engine:
    return db.make_engine(settings.db_path)


def registry_with(engine: Engine, pusher: Pusher, inner: RecordingPublisher) -> TaskRegistry:
    add_agent(engine)
    add_device(engine)
    return TaskRegistry(engine, FakeRunner(), MemoryOutbox(), pusher.task_publisher(inner))


def test_a_dead_ntfy_server_does_not_break_task_processing(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    ntfy = FakeNtfy('down')
    pusher = pusher_for(engine, ntfy.notifier())
    app_events = RecordingPublisher()
    registry = registry_with(engine, pusher, app_events)

    async def go() -> None:
        task = await registry.spawn(AGENT, 'Check the disk', prompt='Check disk usage.')
        await registry.report_state(task.id, TaskState.working, 'The runner reports working')
        await registry.report_state(task.id, TaskState.needs_you, 'A question')
        await registry.steer(task.id, 'Use main.')
        done = await registry.report_state(task.id, TaskState.done, 'The runner reports done')
        assert done.state == TaskState.done
        await pusher.wait()

    run(go())
    assert registry.queue() == []
    # Every state change still reached the app, and both pushes were tried and given up.
    states = [
        d['data']['to'] for t, d in app_events.sent if t == 'task.event' and d['type'] == 'state'
    ]
    assert states == ['starting', 'working', 'needs_you', 'working', 'done']
    clicks = [b['click'] for b in ntfy.bodies]
    assert len(clicks) == 3 * 3  # needs_you, its "did not arrive" alert, done: three tries each
    assert 'did not go out after 3 tries' in caplog.text


def test_task_state_changes_push_through_the_registry(engine: Engine) -> None:
    ntfy = FakeNtfy()
    pusher = pusher_for(engine, ntfy.notifier())
    registry = registry_with(engine, pusher, RecordingPublisher())

    async def go() -> str:
        task = await registry.spawn(AGENT, 'Check the disk', prompt='Check disk usage.')
        await registry.report_state(task.id, TaskState.working, 'working')
        await registry.report_state(task.id, TaskState.done, 'done', summary='12 GB free')
        await registry.report_state(task.id, TaskState.done, 'done again')  # a repeat: no event
        await pusher.wait()
        return task.id

    task_id = run(go())
    assert [(b['title'], b['click']) for b in ntfy.bodies] == [
        ('Done: Check the disk', f'marcel://task/{task_id}')
    ]


def test_the_runner_alert_is_pushed_and_cleared(engine: Engine) -> None:
    ntfy = FakeNtfy()
    pusher = pusher_for(engine, ntfy.notifier())
    registry = registry_with(engine, pusher, RecordingPublisher())
    fake = FakeRunnerServer()

    async def go() -> None:
        watch = watch_for(fake, registry, pusher, Sleeps())  # type: ignore[arg-type]
        watch.start()
        await until(lambda: watch.reachable is True)
        task = await registry.spawn(AGENT, 'Busy', prompt='Work.')
        await until(lambda: registry.get(task.id).state == TaskState.working)
        fake.kill()
        await until(lambda: registry.get(task.id).state == TaskState.unknown)
        await until(lambda: len(ntfy.requests) == 1)
        fake.revive()
        await until(lambda: watch.reachable is True)
        await until(lambda: len(ntfy.requests) == 2)
        await watch.close()
        await pusher.wait()

    run(go())
    assert [(b['title'], b['priority'], b['click']) for b in ntfy.bodies] == [
        ('Marcel cannot reach the NUC', 4, 'marcel://status'),
        ('The NUC is back', 3, 'marcel://status'),
    ]


# The app ---------------------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db', ntfy_server=NTFY)


def test_me_reports_the_configured_ntfy_server(settings: Settings, tmp_path: Path) -> None:
    with TestClient(create_app(settings)) as client:
        me = pair(client)
        body = client.get('/api/me', headers=bearer(me['token'])).json()
    validate('Me', body)
    assert body['push'] == {'apns': False, 'ntfy': True, 'ntfy_server': NTFY}

    off = Settings(data_dir=tmp_path / 'b', db_path=tmp_path / 'off.db')
    with TestClient(create_app(off)) as client:
        me = pair(client)
        body = client.get('/api/me', headers=bearer(me['token'])).json()
    validate('Me', body)
    assert body['push'] == {'apns': False, 'ntfy': False}


def test_the_app_pushes_to_a_registered_ntfy_topic(settings: Settings) -> None:
    ntfy = FakeNtfy()
    with TestClient(create_app(settings)) as client:
        app = client.app
        pusher: Pusher = app.state.pusher  # type: ignore[attr-defined]
        registry: TaskRegistry = app.state.tasks  # type: ignore[attr-defined]
        assert isinstance(registry.publisher.inner, TaskPushes)  # type: ignore[attr-defined]
        old = pusher.notifiers['ntfy'].client  # type: ignore[attr-defined]
        pusher.notifiers['ntfy'].client = httpx.AsyncClient(  # type: ignore[attr-defined]
            transport=httpx.MockTransport(ntfy.handle)
        )
        client.portal.call(old.aclose)  # type: ignore[union-attr]
        me = pair(client)
        res = client.put(
            f'/api/devices/{me["device_id"]}/push',
            json={'kind': 'ntfy', 'ntfy_topic': TOPIC},
            headers=bearer(me['token']),
        )
        assert res.status_code == 200
        with Session(registry.engine) as s:
            s.add(Agent(id=AGENT, owner_user_id=me['user_id'], name='Marcel', animal='giraffe',
                        palette='pink', seed='1'))  # fmt: skip
            s.commit()

        async def go() -> None:
            # No runner is set up, so the task fails to start: a `failed` push.
            task = await registry.spawn(AGENT, 'Check the disk', prompt='Go.')
            assert task.state == TaskState.failed
            await pusher.wait()

        client.portal.call(go)  # type: ignore[union-attr]
    assert [(b['topic'], b['title']) for b in ntfy.bodies] == [(TOPIC, 'Failed: Check the disk')]
    # The app's live event log still gets every task event: the pusher passes them on.
    with Session(create_engine_for(settings)) as s:
        types = list(s.exec(select(AppEvent.type)))
    assert 'task.created' in types and 'task.updated' in types and 'task.event' in types


def test_the_ntfy_server_setting(tmp_path: Path) -> None:
    env = {'MARCEL_CONFIG': str(tmp_path / 'none.toml')}
    assert load_settings(env).ntfy_server is None
    assert load_settings({**env, 'MARCEL_NTFY_SERVER': 'https://ntfy.sh/'}).ntfy_server == (
        'https://ntfy.sh'
    )
    config = tmp_path / 'marcel.toml'
    config.write_text('ntfy_server = "http://ntfy.lan:8080"\n')
    assert load_settings({'MARCEL_CONFIG': str(config)}).ntfy_server == 'http://ntfy.lan:8080'
    with pytest.raises(SettingsError, match='ntfy_server must be a web address'):
        load_settings({**env, 'MARCEL_NTFY_SERVER': 'ntfy.sh'})
