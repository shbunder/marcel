"""Fork (`POST /sessions/{id}/fork`, SP7), through the socket, against the fake claude.

The runner here is `test_sessions.Rig`: the fake shim behind a recording wrapper.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_sessions import Rig

from marcel_runner.resume import BRAIN_TOOLS

GOLDEN = Path(__file__).parent / 'test_fork_golden'
SIDE = 'side-cnv_side_01J9ZR0'
OPENING = 'You are now a side thread forked from the main conversation. Wait for channel messages.'


def golden(rig: Rig, name: str, **values: str) -> list[str]:
    values = {'claude': str(rig.claude), 'state': str(rig.state), **values}
    out = []
    for arg in json.loads((GOLDEN / name).read_text()):
        for k, v in values.items():
            arg = arg.replace('{' + k + '}', v)
        out.append(arg)
    return out


@pytest.fixture
def up(tmp_path: Path) -> Iterator[Rig]:
    rig = Rig(tmp_path)
    rig.start()
    yield rig
    rig.close()


def brain(rig: Rig, **extra: Any) -> dict[str, Any]:
    body = {'role': 'brain', 'name': 'brain', 'model': 'opus', 'permission_mode': 'manual'}
    status, spawned = rig.call(
        'POST', '/sessions', {**body, 'prompt': 'Wait.', 'cwd': str(rig.work), **extra}
    )
    assert status == 201, spawned
    return spawned


def fork(rig: Rig, ref: str, **body: Any) -> tuple[int, Any]:
    return rig.call('POST', f'/sessions/{ref}/fork', {'name': SIDE, 'prompt': OPENING, **body})


# --- done when: a fork test runs against the shim ----------------------------------------------


def test_a_fork_is_a_new_side_session_with_the_golden_command_line(up: Rig) -> None:
    up.fake.use('worker-needs-approval')  # the brain stays live: SP7 forks a live brain
    source = brain(up)
    up.log.unlink()
    status, body = fork(up, source['session_id'])
    assert status == 201, body
    (call,) = up.calls_with('--fork-session')
    assert call['argv'] == golden(up, 'fork-side.json', session_id=source['session_id'])
    assert call['cwd'] == str(up.work.resolve())
    assert body['session_id'] != source['session_id']
    assert body == {
        'session_id': body['session_id'],
        'short_id': body['session_id'][:8],
        'name': SIDE,
        'cwd': str(up.work),
        'channel_registered': True,
    }
    status, session = up.call('GET', f'/sessions/{body["session_id"]}')
    assert (status, session['role']) == (200, 'side')
    status, original = up.call('GET', f'/sessions/{source["session_id"]}')
    assert (status, original['role']) == (200, 'brain')  # the original is untouched
    assert up.sessions is not None
    record = up.sessions.records[body['session_id']]
    assert (record['permission_mode'], record['model']) == ('manual', 'opus')


def test_a_fork_has_the_history_of_the_session_it_forks_from(up: Rig) -> None:
    up.fake.use('worker-happy-path')
    source = brain(up)
    up.fake.wait_for(source['short_id'], 'done')
    status, body = fork(up, source['short_id'])
    assert status == 201, body
    projects = up.fake.home / '.claude' / 'projects'
    (forked,) = projects.glob(f'*/{body["session_id"]}.jsonl')
    assert forked.read_text().count(body['session_id']) > 1  # the copied history, re-stamped


def test_a_fork_may_name_its_own_model(up: Rig) -> None:
    source = brain(up)
    up.log.unlink()
    assert fork(up, source['session_id'], model='sonnet')[0] == 201
    (call,) = up.calls_with('--fork-session')
    assert call['argv'][call['argv'].index('--model') + 1] == 'sonnet'


def test_a_fork_of_a_session_without_a_channel_has_none_either(up: Rig) -> None:
    source = brain(up, channel=False)
    up.log.unlink()
    status, body = fork(up, source['session_id'])
    assert (status, body['channel_registered']) == (201, False)
    (call,) = up.calls_with('--fork-session')
    assert '--channels' not in call['argv']


def test_a_fork_of_a_session_the_runner_did_not_start_is_manual_with_claudes_model(
    up: Rig,
) -> None:
    source = brain(up, permission_mode='auto')
    assert up.sessions is not None
    up.stop_runner()
    up.state.joinpath('sessions.json').unlink()  # the runner forgets it started the brain
    up.start()
    up.log.unlink()
    assert fork(up, source['session_id'])[0] == 201
    (call,) = up.calls_with('--fork-session')
    argv = call['argv']
    assert argv[argv.index('--permission-mode') + 1] == 'manual'
    assert '--model' not in argv


def test_forks_never_count_toward_the_worker_cap(up: Rig) -> None:
    up.fake.use('worker-needs-approval')
    first = up.spawn(name='w1')
    assert first[0] == up.spawn(name='w2')[0] == 201  # the cap (2) is reached
    status, body = fork(up, first[1]['session_id'])
    assert status == 201, body
    assert up.call('GET', '/health')[1]['active_workers'] == 2


def test_a_fork_gets_the_brain_tools_by_name(up: Rig) -> None:
    source = brain(up)
    up.log.unlink()
    fork(up, source['session_id'])
    (call,) = up.calls_with('--fork-session')
    argv = call['argv']
    tools = argv[argv.index('--allowedTools') + 1 : argv.index('--debug-file')]
    assert tools[2:] == list(BRAIN_TOOLS)


# --- failures ----------------------------------------------------------------------------------


def test_claude_refusing_to_fork_is_reported_with_its_words(up: Rig) -> None:
    source = brain(up)
    up.mode('bg-fails')
    status, body = fork(up, source['session_id'])
    assert (status, body['code'], body['detail']) == (422, 'exited', 'error: something went wrong')


def test_unreadable_fork_output_finds_the_fork_by_name_and_stops_it(up: Rig) -> None:
    up.fake.use('worker-needs-approval')
    source = brain(up, name=SIDE)  # the fake gives a fork its source's name
    up.mode('bg-unreadable')
    status, body = fork(up, source['session_id'])
    assert (status, body['code']) == (422, 'exited')
    assert 'stopped the session' in body['message']


def test_a_fork_that_never_shows_up_is_stopped(up: Rig) -> None:
    source = brain(up)
    assert up.sessions is not None
    real = up.sessions.roster
    calls = {'n': 0}

    def without_the_fork(all: bool = True) -> list[dict[str, Any]]:
        calls['n'] += 1
        rows = real(all)
        return (
            rows if calls['n'] == 1 else [r for r in rows if r['sessionId'] == source['session_id']]
        )

    up.sessions.roster = without_the_fork  # type: ignore[method-assign]
    status, body = fork(up, source['session_id'])
    assert (status, body['code']) == (422, 'exited')
    assert 'not in its own list' in body['message']
    assert up.calls_with('stop')  # the runner stopped it


def test_a_fork_that_resumes_the_original_never_stops_it(up: Rig) -> None:
    source = brain(up)
    wrapper = up.claude.read_text()
    answer = f"print('backgrounded · {source['short_id']} · brain'); sys.exit(0)"
    up.claude.write_text(
        wrapper.replace('os.execv(', f"if '--fork-session' in args: {answer}\nos.execv(", 1)
    )
    status, body = fork(up, source['session_id'])
    assert (status, body['code']) == (422, 'exited')
    assert 'instead of forking' in body['message']
    assert up.calls_with('stop') == []


def test_a_fork_is_refused_without_a_supervisor(up: Rig) -> None:
    source = brain(up)
    up.mode('supervisor-down')
    status, body = fork(up, source['session_id'])
    assert (status, body['code']) == (422, 'no_supervisor')
    assert up.calls_with('--fork-session') == []


def test_a_fork_that_times_out_is_found_and_stopped(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.start(spawn_timeout=2.0)
    try:
        rig.fake.use('worker-needs-approval')
        source = brain(rig, name=SIDE)
        rig.mode('bg-slow')
        status, body = fork(rig, source['session_id'])
        assert (status, body['code']) == (503, 'claude_timeout')
        assert 'stopped the session' in body['message']
    finally:
        rig.close()


@pytest.mark.parametrize(
    ('body', 'words'),
    [
        ({'prompt': 'p'}, 'Missing field(s): name'),
        ({'name': 's', 'prompt': 'p', 'role': 'worker'}, 'Unknown field(s): role'),
        ({'name': '-s', 'prompt': 'p'}, 'name must be'),
        ({'name': 's', 'prompt': ''}, 'prompt must be'),
        ({'name': 's', 'prompt': 'p', 'model': '--dangerously-skip-permissions'}, 'model must'),
        ({'name': 's', 'prompt': 'p', 'model': 7}, 'model must'),
    ],
)
def test_bad_fork_bodies_are_400_in_words(up: Rig, body: dict[str, Any], words: str) -> None:
    source = brain(up)
    status, answer = up.call('POST', f'/sessions/{source["session_id"]}/fork', body)
    assert (status, answer['code']) == (400, 'bad_request')
    assert words in answer['message']


def test_fork_of_an_unknown_session_is_404(up: Rig) -> None:
    status, body = fork(up, 'nope')
    assert (status, body['code']) == (404, 'not_found')
