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
import yaml
from jsonschema import Draft202012Validator
from test_runner_client_fakes import FakeRunnerServer
from test_tasks_fakes import CONTRACTS, run, validator

from marcel_hub.models import Task, TaskLocation, TaskOrigin, TaskState
from marcel_hub.runner_client import (
    RUNNER_EVENT_TYPES,
    TRANSCRIPT_DATA,
    TRANSCRIPT_FIELDS,
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
    for event in (CHANGED, RESTARTED, {**RESTARTED, 'supervisor_restart': {'session_ids': []}}):
        assert check_runner_event(json.dumps(event)) == event


CHANGED = {'seq': 5, 'type': 'runner.claude_changed', 'at': '2026-10-14T03:00:00Z',
           'message': 'Claude Code changed.',
           'claude_change': {'from': '2.1.296 (Claude Code)', 'to': '2.1.301 (Claude Code)',
                             'expected': '2.1.296'}}  # fmt: skip
RESTARTED = {'seq': 6, 'type': 'runner.supervisor_restarted', 'at': '2026-10-14T03:00:01Z',
             'message': 'The supervisor restarted.',
             'supervisor_restart': {'cause': 'upgrade', 'live_workers': 2,
                                    'session_ids': ['s-1', 's-2']}}  # fmt: skip


@pytest.mark.parametrize(
    ('event', 'why'),
    [
        ({**CHANGED, 'claude_change': None}, 'no claude_change'),
        ({**CHANGED, 'claude_change': {'from': 'a', 'to': 'b'}}, 'exactly from, to and expected'),
        ({**CHANGED, 'claude_change': {'from': 'a', 'to': 2, 'expected': 'c'}}, 'not text'),
        ({**CHANGED, 'message': None}, 'no message'),
        ({**RESTARTED, 'supervisor_restart': None}, 'no supervisor_restart'),
        ({**RESTARTED, 'supervisor_restart': {'session_ids': [], 'pid': 1}}, 'unknown fields pid'),
        ({**RESTARTED, 'supervisor_restart': {'session_ids': 's-1'}}, 'no list of session_ids'),
        ({**RESTARTED, 'supervisor_restart': {'session_ids': [3]}}, 'no list of session_ids'),
        ({**RESTARTED, 'supervisor_restart': {'session_ids': [], 'cause': 1}}, 'cause'),
        (
            {**RESTARTED, 'supervisor_restart': {'session_ids': [], 'live_workers': -1}},
            'live_workers',
        ),  # fmt: skip
    ],
)
def test_a_bad_claude_event_is_refused(event: dict[str, Any], why: str) -> None:
    with pytest.raises(BadEvent, match=why):
        check_runner_event(json.dumps(event))


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
    for corrupt in [
        '{nope',
        '[]',
        'null',
        '5',
        '{"s": ["a", "b", "c"]}',
        '{"s": [1, 2]}',
        '{"s": [true, 2, 0]}',
        '{"s": [200, 100, 0]}',
        '{"s": [0, 1, -2]}',
    ]:
        caplog.clear()
        path.write_text(corrupt)
        assert OffsetStore(path).positions == {}, corrupt
        assert 'read again from the start' in caplog.text, corrupt
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


# Transcript data against the contract ------------------------------------------------------------

SCHEMA = json.loads((CONTRACTS / 'transcript.schema.json').read_text())
TRANSCRIPT_EVENT = Draft202012Validator(SCHEMA)
TASK_EVENT = validator('app-api.yaml#/components/schemas/TaskEvent')
DEF_NAMES = {'text': 'Text', 'thinking': 'Thinking', 'tool_call': 'ToolCall',
             'tool_result': 'ToolResult', 'diff': 'Diff', 'subagent_start': 'SubagentStart',
             'subagent_stop': 'SubagentStop', 'turn_end': 'TurnEnd', 'error': 'Error',
             'raw': 'Raw', 'workflow_start': 'WorkflowStart', 'workflow_stop': 'WorkflowStop',
             'workflow_agent_start': 'WorkflowAgentStart',
             'workflow_agent_stop': 'WorkflowAgentStop'}  # fmt: skip


def _contract_kind(prop: dict[str, Any]) -> Any:
    if 'enum' in prop:
        return frozenset(prop['enum'])
    if prop['type'] == 'array':
        return 'hunks'
    if prop['type'] == 'integer' and prop.get('minimum') == 0:
        return 'count'
    return prop['type']


# Contract types and fields the hub does not read yet. Empty since S-04.16 read a workflow's; the
# test after this one fails as soon as one is read, so these sets can only shrink.
PENDING_TRANSCRIPT_TYPES: set[str] = set()
PENDING_TRANSCRIPT_FIELDS: set[str] = set()


def test_the_hubs_transcript_table_matches_the_contract() -> None:
    defs = SCHEMA['$defs']
    contract_types = set(defs['TranscriptEvent']['properties']['type']['enum'])
    assert set(TRANSCRIPT_DATA) == contract_types - PENDING_TRANSCRIPT_TYPES
    assert (
        set(defs['TranscriptEvent']['properties']) - PENDING_TRANSCRIPT_FIELDS == TRANSCRIPT_FIELDS
    )
    for kind, name in DEF_NAMES.items():
        shape = defs[name]
        assert shape['additionalProperties'] is False
        expected = {
            key: (_contract_kind(prop), key in shape['required'])
            for key, prop in shape['properties'].items()
        }
        assert TRANSCRIPT_DATA[kind] == expected, kind


def test_pending_transcript_entries_are_still_pending() -> None:
    defs = SCHEMA['$defs']
    assert set(defs['TranscriptEvent']['properties']['type']['enum']) >= PENDING_TRANSCRIPT_TYPES
    assert set(defs['TranscriptEvent']['properties']) >= PENDING_TRANSCRIPT_FIELDS
    read = PENDING_TRANSCRIPT_TYPES & set(TRANSCRIPT_DATA)
    assert not read, f'the hub reads {read} now: remove it from PENDING_TRANSCRIPT_TYPES'
    read = PENDING_TRANSCRIPT_FIELDS & TRANSCRIPT_FIELDS
    assert not read, f'the hub reads {read} now: remove it from PENDING_TRANSCRIPT_FIELDS'


GOOD_DATA = {
    'text': {'role': 'user', 'text': 'Hi', 'source': 'channel'},
    'thinking': {'duration_ms': 3},
    'tool_call': {'tool_use_id': 't', 'tool': 'Bash', 'summary': 'ls', 'input': {'command': 'ls'}},
    'tool_result': {'tool_use_id': 't', 'is_error': False, 'denied': True, 'text': '',
                    'truncated': False},
    'diff': {'tool_use_id': 't', 'file_path': 'a.py', 'hunks': [
        {'old_start': 1, 'old_lines': 1, 'new_start': 1, 'new_lines': 2, 'lines': ['-a', '+b']}]},
    'subagent_start': {'tool_use_id': 't', 'agent_id': 'a1', 'description': 'd',
                       'subagent_type': 'x'},
    'subagent_stop': {'agent_id': 'a1', 'status': 'completed', 'summary': 's'},
    'turn_end': {'duration_ms': 1, 'message_count': 2, 'pending_background_agents': 0},
    'error': {'message': 'm', 'api_error_status': 429, 'usage_limit': True},
    'raw': {'entry_type': 'x', 'entry': {}},
    'workflow_start': {'tool_use_id': 't', 'run_id': 'wf_1', 'task_id': 'k', 'name': 'n',
                       'description': 'd'},
    'workflow_stop': {'tool_use_id': 't', 'run_id': 'wf_1', 'status': 'completed', 'summary': 's'},
    'workflow_agent_start': {'run_id': 'wf_1', 'agent_id': 'a1', 'label': 'l', 'phase': 'p'},
    'workflow_agent_stop': {'run_id': 'wf_1', 'agent_id': 'a1', 'summary': 's'},
}  # fmt: skip


def line(kind: str, data: Any, **extra: Any) -> str:
    return json.dumps({'session_id': 's-1', 'offset': 5, 'at': '2026-10-04T09:05:02Z',
                       'type': kind, 'data': data, **extra})  # fmt: skip


@pytest.mark.parametrize('kind', sorted(GOOD_DATA))
def test_every_event_the_hub_accepts_is_in_the_contract(kind: str) -> None:
    event = check_transcript_event(line(kind, GOOD_DATA[kind], subagent_id='a1'), 's-1')
    TRANSCRIPT_EVENT.validate(event)
    if kind not in ('thinking', 'turn_end'):  # the two the hub never stores
        TASK_EVENT.validate({'task_id': 't', 'seq': 1, 'type': kind, 'at': event['at'],
                             'data': event['data']})  # fmt: skip


@pytest.mark.parametrize(
    ('kind', 'data', 'why'),
    [
        ('text', {'role': 7, 'text': ['x'], 'extra': 1}, 'unknown fields extra'),
        ('text', {'role': 7, 'text': ['x']}, 'wrong type for role, text'),
        ('text', {'role': 'robot', 'text': 'x'}, 'wrong type for role'),
        ('thinking', {'duration_ms': -1}, 'wrong type for duration_ms'),
        ('tool_call', {'tool_use_id': 't', 'tool': 'B', 'summary': 's', 'input': 'ls'}, 'input'),
        ('tool_result', {'tool_use_id': 't', 'is_error': 'no', 'text': ''}, 'is_error'),
        ('diff', {'tool_use_id': 't', 'file_path': 'a', 'hunks': [{'lines': []}]}, 'hunks'),
        ('diff', {'tool_use_id': 't', 'file_path': 'a', 'hunks': 'x'}, 'hunks'),
        ('subagent_stop', {'agent_id': 'a', 'status': 'exploded'}, 'status'),
        ('error', {'message': 'm', 'api_error_status': True}, 'api_error_status'),
        ('raw', {'entry_type': 'x', 'entry': []}, 'entry'),
    ],
)
def test_out_of_contract_data_is_refused(kind: str, data: Any, why: str) -> None:
    with pytest.raises(BadEvent, match=why):
        check_transcript_event(line(kind, data), 's-1')
    assert not TRANSCRIPT_EVENT.is_valid(json.loads(line(kind, data)))


@pytest.mark.parametrize(
    ('kind', 'data', 'extra', 'why'),
    [
        ('workflow_agent_start', {'run_id': 'wf_1', 'agent_id': 'a1', 'label': 'l'},
         {'workflow_id': '../x'}, 'bad workflow_id'),
        ('workflow_agent_start', {'run_id': '../x', 'agent_id': 'a1', 'label': 'l'}, {},
         'bad run_id'),
        ('workflow_agent_stop', {'run_id': 'wf_1', 'agent_id': 'NOT/HEX'}, {}, 'bad agent_id'),
        ('workflow_stop', {'tool_use_id': 't', 'status': 'done'}, {}, 'status'),
        ('workflow_start', {'tool_use_id': 't', 'run_id': 'wf_1', 'name': 'n', 'x': 1}, {},
         'unknown fields x'),
    ],
)  # fmt: skip
def test_a_bad_workflow_event_is_refused(kind: str, data: Any, extra: Any, why: str) -> None:
    with pytest.raises(BadEvent, match=why):
        check_transcript_event(line(kind, data, **extra), 's-1')


def test_unknown_or_bad_event_fields_are_refused() -> None:
    with pytest.raises(BadEvent, match='unknown fields secret'):
        check_transcript_event(line('thinking', {'duration_ms': 1}, secret=1), 's-1')
    with pytest.raises(BadEvent, match='subagent_id'):
        check_transcript_event(line('thinking', {'duration_ms': 1}, subagent_id=3), 's-1')


# Runner event types against the contract -------------------------------------------------------

# A type the contract adds before the hub reads it waits here; the second test fails as soon as one
# is read, so this set can only shrink. Empty since S-04.17.
PENDING_RUNNER_EVENT_TYPES: set[str] = set()


def _runner_event_types() -> set[str]:
    spec = yaml.safe_load((CONTRACTS / 'runner-api.yaml').read_text())
    return set(spec['components']['schemas']['RunnerEvent']['properties']['type']['enum'])


def test_the_hubs_runner_event_types_match_the_contract() -> None:
    assert set(RUNNER_EVENT_TYPES) == _runner_event_types() - PENDING_RUNNER_EVENT_TYPES


def test_pending_runner_event_types_are_still_pending() -> None:
    assert _runner_event_types() >= PENDING_RUNNER_EVENT_TYPES
    read = PENDING_RUNNER_EVENT_TYPES & set(RUNNER_EVENT_TYPES)
    assert not read, f'the hub reads {read} now: remove it from PENDING_RUNNER_EVENT_TYPES'


# Runner /health and /usage fields against the contract ------------------------------------------

# The fields of runner-api `Health` and `UsageReading` the hub knows: it reads them
# (`path_health.py`, `claude_updates.py`, `usage.py`), or, for `Health.version`, `cap` and
# `active_workers`, leaves them alone on purpose.
RUNNER_FIELDS_KNOWN: dict[str, set[str]] = {
    'Health': {
        'ok',
        'version',
        'claude_version',
        'claude_version_expected',
        'supervisor',
        'cap',
        'active_workers',
        'reason',
    },
    'UsageReading': {'five_hour', 'weekly', 'source', 'taken_at', 'stale'},
}
# Added by S-02.12 (runner-api 0.1.13) before the hub reads them. S-04.18 moves each to
# RUNNER_FIELDS_KNOWN when the hub tells the owner; the second test fails as soon as a reader
# below names one, so this can only shrink.
PENDING_RUNNER_FIELDS: dict[str, set[str]] = {'Health': {'login'}, 'UsageReading': {'reason'}}
# Where the hub would read each pending field, and how that read would look there.
QUOTED = ("'{}'", '"{}"')
# `reason` is a common word, so only a read off a usage reading counts.
USAGE_READ = ("reading.get('{}'", "reading['{}']", "usage.get('{}'", "usage['{}']")
PENDING_READS: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    'login': [('path_health.py', QUOTED), ('claude_updates.py', QUOTED)],
    'reason': [('claude_updates.py', USAGE_READ), ('usage.py', USAGE_READ)],
}


def _runner_fields(schema: str) -> set[str]:
    spec = yaml.safe_load((CONTRACTS / 'runner-api.yaml').read_text())
    return set(spec['components']['schemas'][schema]['properties'])


@pytest.mark.parametrize('schema', sorted(RUNNER_FIELDS_KNOWN))
def test_every_runner_field_is_known_or_pending(schema: str) -> None:
    pending = PENDING_RUNNER_FIELDS.get(schema, set())
    assert _runner_fields(schema) == RUNNER_FIELDS_KNOWN[schema] | pending


def test_pending_runner_fields_are_still_pending() -> None:
    src = Path(__file__).resolve().parent.parent / 'src' / 'marcel_hub'
    for schema, fields in PENDING_RUNNER_FIELDS.items():
        assert not fields & RUNNER_FIELDS_KNOWN[schema]
        for field in fields:
            for name, reads in PENDING_READS[field]:
                text = (src / name).read_text()
                assert not any(r.format(field) in text for r in reads), (
                    f'{name} reads {schema}.{field} now: remove it from PENDING_RUNNER_FIELDS'
                )
