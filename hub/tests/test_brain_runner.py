"""The runner calls the brain keeper makes (S-06.2), and the runner watcher's pokes that wake it.
A fake runner behind an httpx transport; nothing reaches a real runner or the network."""

import json
import logging

import httpx
import pytest
from test_runner_client_fakes import (
    FakeRunnerServer,
    RecordingAlerts,
    Sleeps,
    settle,
    until,
    watch_for,
)
from test_tasks_fakes import (  # noqa: F401  (fixtures)
    outbox_fixture,
    publisher_fixture,
    registry_fixture,
    run,
    runner_fixture,
)

from marcel_hub.runner_client import RunnerClient
from marcel_hub.tasks.ports import RunnerUnavailable, StartFailed
from marcel_hub.tasks.registry import TaskRegistry

SPAWNED = {
    'session_id': 'b6f0c1aa-0000-4000-8000-000000000001',
    'short_id': 'b6f0c1aa',
    'name': 'brain-agt_1',
    'cwd': '/home/owner/marcel/brain',
    'channel_registered': True,
}


def client_answering(*answers: httpx.Response) -> tuple[RunnerClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    queue = list(answers)

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    return RunnerClient('/fake.sock', transport=httpx.MockTransport(handle)), seen


def test_a_brain_is_spawned_with_role_brain_in_its_workspace() -> None:
    client, seen = client_answering(httpx.Response(201, json=SPAWNED))
    sid = run(
        client.spawn_brain(
            name='brain-agt_1', model='opus', cwd='/home/owner/marcel/brain', prompt='Hello.'
        )
    )
    assert sid == SPAWNED['session_id']
    assert seen[0].method == 'POST' and seen[0].url.path == '/sessions'
    assert json.loads(seen[0].content) == {
        'role': 'brain',
        'name': 'brain-agt_1',
        'model': 'opus',
        'permission_mode': 'auto',
        'cwd': '/home/owner/marcel/brain',
        'prompt': 'Hello.',
    }


def test_a_brain_spawn_the_runner_refuses_says_why() -> None:
    body = {'code': 'channel_timeout', 'message': 'The channel never registered.'}
    client, _ = client_answering(httpx.Response(422, json=body))
    with pytest.raises(StartFailed, match='The channel never registered.'):
        run(client.spawn_brain(name='b', model='opus', cwd='/w', prompt='x'))


def test_sessions_are_listed_and_a_runner_that_cannot_list_is_unavailable() -> None:
    listed = [{'id': 's1', 'short_id': 's1', 'name': 'n', 'state': 'working', 'cwd': '/w'}]
    busy = {'code': 'roster_unavailable', 'message': 'claude agents --json failed.'}
    client, seen = client_answering(
        httpx.Response(200, json=listed), httpx.Response(503, json=busy)
    )
    assert run(client.sessions()) == listed
    assert seen[0].url.path == '/sessions'
    with pytest.raises(RunnerUnavailable, match='claude agents --json failed.'):
        run(client.sessions())


def test_every_runner_event_and_connect_pokes_the_brain_keeper(
    registry: TaskRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    fake, alerts, sleeps = FakeRunnerServer(), RecordingAlerts(), Sleeps()
    pokes: list[int] = []

    def broken() -> None:
        raise RuntimeError('a bug in a listener')

    async def scenario() -> None:
        watch = watch_for(fake, registry, alerts, sleeps)
        watch.pokes.append(broken)  # one bad listener does not stop the feed
        watch.pokes.append(lambda: pokes.append(1))
        watch.start()
        await until(lambda: watch.reachable is True)
        await until(lambda: len(pokes) >= 1)  # the connect
        before = len(pokes)
        brain = fake.add_session('blocked', name='brain-agt_1', role='brain')
        fake.push_state(brain, state='failed')  # not a task: the watcher itself ignores it
        await until(lambda: len(pokes) > before)
        fake.push_raw(json.dumps({'seq': fake.seq + 1, 'type': 'runner.recovered',
                                  'at': '2026-10-12T04:00:00Z', 'message': 'ok'}))  # fmt: skip
        await until(lambda: len(pokes) > before + 1)
        await settle()
        await watch.close()

    with caplog.at_level(logging.ERROR):
        run(scenario())
    assert any('A runner-event listener failed.' in r.message for r in caplog.records)
