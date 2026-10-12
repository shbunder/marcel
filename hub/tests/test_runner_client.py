"""The runner client's requests and answers, the session-state mapping and the event checks."""

import asyncio
import json
import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_runner_client_fakes import FakeRunnerServer
from test_tasks_fakes import run

from marcel_hub.models import Task, TaskLocation, TaskOrigin, TaskState
from marcel_hub.runner_client import (
    BadEvent,
    LogAlerts,
    OffsetStore,
    RunnerClient,
    StreamRefused,
    TailPosition,
    check_runner_event,
    check_transcript_event,
    map_session,
    slug,
    sse,
    usage_wait,
)
from marcel_hub.tasks.ports import CapReached, RunnerUnavailable, StartFailed


def task(**fields: Any) -> Task:
    return Task(
        id='tsk_0001',
        agent_id='agt_marcel',
        title=fields.pop('title', 'Fix the flaky login test!'),
        location=TaskLocation.nuc,
        state=TaskState.starting,
        model='sonnet',
        origin=TaskOrigin.marcel,
        **fields,
    )


def answering(status: int, body: Any = None) -> tuple[RunnerClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    return RunnerClient('/x.sock', worker_cwd='/w', transport=httpx.MockTransport(handler)), seen


def unreachable() -> RunnerClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('No such file or directory', request=request)

    return RunnerClient('/x.sock', worker_cwd='/w', transport=httpx.MockTransport(handler))


SPAWNED = {
    'session_id': 's-1',
    'short_id': 's-1',
    'name': 'tsk_0001',
    'cwd': '/w',
    'channel_registered': True,
}


# Starting and stopping --------------------------------------------------------------------------


def test_start_without_a_repo_runs_a_worker_in_the_worker_workspace() -> None:
    client, seen = answering(201, SPAWNED)
    started = run(client.start(task(), 'Do it.'))
    assert started.session_id == 's-1' and started.branch is None
    assert json.loads(seen[0].content) == {
        'role': 'worker',
        'name': 'tsk_0001',
        'model': 'sonnet',
        'prompt': 'Do it.',
        'cwd': '/w',
    }


def test_start_with_a_repo_asks_for_a_worktree() -> None:
    client, seen = answering(201, {**SPAWNED, 'branch': 'marcel/tsk_0001-fix-the-flaky-login-test'})
    started = run(client.start(task(repo='shbunder/marcel'), 'Do it.'))
    assert started.branch == 'marcel/tsk_0001-fix-the-flaky-login-test'
    body = json.loads(seen[0].content)
    assert 'cwd' not in body
    assert body['repo'] == {
        'url': 'https://github.com/shbunder/marcel',
        'task_id': 'tsk_0001',
        'slug': 'fix-the-flaky-login-test',
    }


def test_start_without_any_workspace_fails_with_a_reason() -> None:
    client = RunnerClient('/x.sock', transport=httpx.MockTransport(lambda r: httpx.Response(201)))
    with pytest.raises(StartFailed, match='MARCEL_WORKER_CWD'):
        run(client.start(task(), 'Do it.'))


def test_a_full_cap_is_cap_reached() -> None:
    client, _ = answering(409, {'code': 'cap_reached', 'message': 'The worker cap is reached.'})
    with pytest.raises(CapReached):
        run(client.start(task(), 'Do it.'))


@pytest.mark.parametrize(
    ('status', 'body', 'message'),
    [
        (
            422,
            {'code': 'not_trusted', 'message': 'The workspace is not trusted yet.'},
            'The workspace is not trusted yet.',
        ),
        (409, {'code': 'resume_copied', 'message': 'Copied.'}, 'Copied.'),
        (500, 'boom', 'The runner answered 500.'),
        (500, [1], 'The runner answered 500.'),
    ],
)
def test_a_refused_start_fails_with_the_runners_message(
    status: int, body: Any, message: str
) -> None:
    client, _ = answering(status, body)
    with pytest.raises(StartFailed) as err:
        run(client.start(task(), 'Do it.'))
    assert str(err.value) == message


def test_an_unreachable_runner_is_runner_unavailable() -> None:
    client = unreachable()
    with pytest.raises(RunnerUnavailable, match='not answering'):
        run(client.start(task(), 'Do it.'))
    with pytest.raises(RunnerUnavailable):
        run(client.stop('s-1'))
    with pytest.raises(RunnerUnavailable):
        run(client.health())
    with pytest.raises(RunnerUnavailable):
        run(client.open_stream('/events'))
    assert run(client.usage()) is None


def test_stop() -> None:
    client, seen = answering(200, {'id': 's-1'})
    run(client.stop('s-1'))
    assert (seen[0].method, seen[0].url.path) == ('POST', '/sessions/s-1/stop')
    gone, _ = answering(404, {'code': 'not_found', 'message': 'No session.'})
    run(gone.stop('s-1'))  # already gone: nothing to do
    broken, _ = answering(500, {'code': 'x', 'message': 'claude stop failed.'})
    with pytest.raises(RunnerUnavailable, match='claude stop failed'):
        run(broken.stop('s-1'))


def test_health_and_usage() -> None:
    client, _ = answering(200, {'ok': True})
    assert run(client.health()) == {'ok': True}
    assert run(client.usage()) == {'ok': True}
    sick, _ = answering(500, {'code': 'x', 'message': 'Broken.'})
    with pytest.raises(RunnerUnavailable, match='Broken.'):
        run(sick.health())
    none, _ = answering(503, {'code': 'no_reading', 'message': 'No reading.'})
    assert run(none.usage()) is None


def test_a_refused_stream_says_why() -> None:
    client, _ = answering(404, {'code': 'not_found', 'message': 'No session.'})
    with pytest.raises(StreamRefused, match='No session.') as err:
        run(client.open_stream('/sessions/x/tail'))
    assert err.value.status == 404


def test_slug() -> None:
    assert slug('Fix the flaky login test!') == 'fix-the-flaky-login-test'
    assert slug('???') == 'task'
    assert len(slug('a' * 80)) == 40


# Over a real unix socket ------------------------------------------------------------------------


@pytest.fixture
def short_dir() -> Iterator[Path]:
    # macOS caps socket paths at 104 bytes; pytest's tmp_path is longer.
    path = Path(tempfile.mkdtemp(prefix='mrc', dir='/tmp'))
    yield path
    for child in path.iterdir():
        child.unlink()
    path.rmdir()


def test_the_client_speaks_http_over_the_unix_socket(short_dir: Path) -> None:
    sock = short_dir / 'runner.sock'

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await reader.readuntil(b'\r\n\r\n')
        assert request.startswith(b'GET /health ')
        body = json.dumps({'ok': True, 'version': '0.1.0'}).encode()
        writer.write(
            b'HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n'
            b'content-length: %d\r\nconnection: close\r\n\r\n%s' % (len(body), body)
        )
        await writer.drain()
        writer.close()

    async def scenario() -> dict[str, Any]:
        server = await asyncio.start_unix_server(serve, path=str(sock))
        client = RunnerClient(sock)
        try:
            return await client.health()
        finally:
            await client.aclose()
            server.close()
            await server.wait_closed()

    assert run(scenario()) == {'ok': True, 'version': '0.1.0'}
    assert not os.path.exists(short_dir / 'missing.sock')
    with pytest.raises(RunnerUnavailable):
        run(RunnerClient(short_dir / 'missing.sock').health())


# Reading Server-Sent Events ---------------------------------------------------------------------


def test_sse_parsing() -> None:
    async def lines() -> AsyncIterator[str]:
        for line in [
            ': keep-alive',
            'id: 7',
            'event: transcript',
            'data: {"a":',
            'data: 1}',
            '',
            '',
            'retry: 10',
            'data:no-space',
            '',
        ]:
            yield line

    async def collect() -> list[Any]:
        return [m async for m in sse(lines())]

    first, second = run(collect())
    assert (first.id, first.event, first.data) == ('7', 'transcript', '{"a":\n1}')
    assert (second.id, second.event, second.data) == ('7', 'message', 'no-space')


# Checking events --------------------------------------------------------------------------------

SESSION = {'id': 's-1', 'short_id': 's-1', 'name': 'n', 'cwd': '/w', 'state': 'working'}
GOOD_EVENT = {
    'seq': 3,
    'type': 'session.state',
    'at': '2026-10-04T09:05:02.205Z',
    'session': SESSION,
}


def test_a_good_runner_event_passes() -> None:
    assert check_runner_event(json.dumps(GOOD_EVENT)) == GOOD_EVENT
    warning = {'seq': 4, 'type': 'runner.warning', 'at': '2026-10-04T09:05:02Z', 'message': 'Hm.'}
    assert check_runner_event(json.dumps(warning)) == warning


@pytest.mark.parametrize(
    ('change', 'why'),
    [
        ('[]', 'not an object'),
        ({'seq': 0}, 'seq'),
        ({'seq': True}, 'seq'),
        ({'type': 'session.exploded'}, 'unknown runner event type'),
        ({'at': None}, 'no time'),
        ({'at': 'yesterday'}, 'not ISO'),
        ({'session': None}, 'no session'),
        ({'session': {**SESSION, 'cwd': 3}}, 'no cwd'),
        ({'session': {**SESSION, 'state': 'confused'}}, 'confused'),
        ({'type': 'runner.warning'}, 'no message'),
    ],
)
def test_a_bad_runner_event_is_refused(change: Any, why: str) -> None:
    raw = change if isinstance(change, str) else json.dumps({**GOOD_EVENT, **change})
    with pytest.raises(BadEvent, match=why):
        check_runner_event(raw)
    with pytest.raises(BadEvent, match='not JSON'):
        check_runner_event('{')


GOOD_LINE = {
    'session_id': 's-1',
    'offset': 10,
    'index': 0,
    'at': '2026-10-04T09:05:02Z',
    'type': 'tool_call',
    'data': {'tool_use_id': 't', 'tool': 'Bash', 'summary': 'ls'},
}


@pytest.mark.parametrize(
    ('change', 'why'),
    [
        ('"text"', 'not an object'),
        ({'session_id': 's-2'}, "'s-2'"),
        ({'offset': -1}, 'offset'),
        ({'offset': '10'}, 'offset'),
        ({'index': -1}, 'index'),
        ({'at': 3}, 'no time'),
        ({'type': 'smoke'}, 'unknown transcript event type'),
        ({'data': []}, 'no data'),
        ({'data': {'tool': 'Bash'}}, 'lacks tool_use_id, summary'),
    ],
)
def test_a_bad_transcript_event_is_refused(change: Any, why: str) -> None:
    assert check_transcript_event(json.dumps(GOOD_LINE), 's-1') == GOOD_LINE
    raw = change if isinstance(change, str) else json.dumps({**GOOD_LINE, **change})
    with pytest.raises(BadEvent, match=why):
        check_transcript_event(raw, 's-1')
    with pytest.raises(BadEvent, match='not JSON'):
        check_transcript_event('nope', 's-1')


# Mapping session state --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('session', 'state', 'waiting_for'),
    [
        ({'state': 'working', 'status': 'busy'}, TaskState.working, None),
        ({'state': 'working'}, TaskState.working, None),
        (
            {'state': 'working', 'status': 'idle'},
            TaskState.needs_you,
            'Your direction: the last step was refused or interrupted.',
        ),
        (
            {'state': 'blocked', 'waiting_for': 'permission prompt'},
            TaskState.needs_you,
            'Your approval to use a tool.',
        ),
        (
            {'state': 'blocked', 'waiting_for': 'permission prompt', 'needs': 'approve Bash: ls'},
            TaskState.needs_you,
            'Your approval: approve Bash: ls',
        ),
        ({'state': 'blocked', 'status': 'idle'}, TaskState.needs_you, 'An answer from you.'),
        (
            {'state': 'blocked', 'needs': 'Which branch should I use?'},
            TaskState.needs_you,
            'Which branch should I use?',
        ),
        (
            {'state': 'blocked', 'waiting_for': 'login'},
            TaskState.needs_you,
            'Claude Code waits for: login.',
        ),
        (
            {'state': 'blocked', 'needs': 'rate limited — wait and retry'},
            TaskState.needs_you,
            'The usage limit is reached. Marcel could not read when it resets.',
        ),
        ({'state': 'done'}, TaskState.done, None),
        ({'state': 'failed'}, TaskState.failed, None),
        ({'state': 'stopped'}, TaskState.stopped, None),
    ],
)
def test_runner_states_map_onto_task_states(
    session: dict[str, Any], state: TaskState, waiting_for: str | None
) -> None:
    report = map_session(session)
    assert report.state == state
    assert report.fields.get('waiting_for') == waiting_for


def test_done_and_failed_carry_the_result() -> None:
    assert map_session({'state': 'done', 'result': 'All green.'}).fields == {
        'summary': 'All green.'
    }
    failed = map_session({'state': 'failed', 'detail': 'exit 1'})
    assert failed.reason == 'The runner reports failed: exit 1'


def test_usage_wait_names_the_fuller_window() -> None:
    reading = {
        'five_hour': {'used_pct': 40, 'resets_at': '2026-10-04T14:29:00Z'},
        'weekly': {'used_pct': 100, 'resets_at': '2026-10-09T01:59:00Z'},
        'source': 'cli_usage',
    }
    assert usage_wait(reading) == 'The usage limit is reached. It resets Fri 09 Oct, 01:59 UTC.'
    broken = {'five_hour': {'used_pct': 1, 'resets_at': 'soon'}}
    assert 'could not read' in usage_wait(broken)
    assert 'could not read' in usage_wait(None)


# Offsets and alerts -----------------------------------------------------------------------------


def test_tail_position_skips_what_it_has_and_resumes_from_the_last_whole_line() -> None:
    pos = TailPosition()
    assert pos.is_new(100, 0)
    pos.advance(100, 0)
    assert not pos.is_new(100, 0) and pos.is_new(100, 1)
    assert pos.committed == 0  # line 100 may have more events
    pos.advance(200, 0)
    assert pos.committed == 100 and not pos.is_new(150, 3)


def test_offsets_survive_a_restart_and_a_broken_file_is_not_fatal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / 'offsets.json'
    store = OffsetStore(path)
    store.get('s-1').advance(100, 2)
    store.save()
    assert OffsetStore(path).get('s-1') == TailPosition(0, 100, 2)
    path.write_text('{nope')
    assert OffsetStore(path).positions == {}
    assert 'read again from the start' in caplog.text
    OffsetStore().save()  # in memory only: nothing to write


def test_log_alerts_keep_one_open_alert_per_cause(caplog: pytest.LogCaptureFixture) -> None:
    alerts = LogAlerts()
    alerts.broke('runner_unreachable', 'Marcel cannot reach the NUC', 'It keeps trying.')
    assert alerts.open == {'runner_unreachable': 'It keeps trying.'}
    alerts.resolved('runner_unreachable')
    alerts.resolved('runner_unreachable')
    assert alerts.open == {}
    assert 'Something broke: Marcel cannot reach the NUC' in caplog.text


def test_the_fake_is_a_faithful_enough_runner() -> None:
    fake = FakeRunnerServer()

    async def scenario() -> None:
        client = fake.client()
        assert (await client.health())['ok'] is True
        with pytest.raises(StreamRefused):
            await client.open_stream('/sessions/nobody/tail')
        await client.aclose()

    run(scenario())
