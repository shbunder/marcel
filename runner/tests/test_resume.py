"""Resume (`POST /sessions/{id}/resume`), through the socket, against the fake claude.

The runner here is `test_sessions.Rig`: the fake shim behind a recording wrapper.
"""

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_sessions import Rig

from marcel_runner import resume
from marcel_runner.sessions import ALLOWED_TOOLS, BRAIN_TOOLS, SessionError, allowed_tools

GOLDEN = Path(__file__).parent / 'test_resume_golden'
FOLLOW_UP = 'Follow-up: also add a test for the empty case.'


def golden(rig: Rig, name: str, **values: str) -> list[str]:
    values = {'claude': str(rig.claude), 'state': str(rig.state), **values}
    out = []
    for arg in json.loads((GOLDEN / name).read_text()):
        for k, v in values.items():
            arg = arg.replace('{' + k + '}', v)
        out.append(arg)
    return out


@pytest.fixture
def up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Rig]:
    monkeypatch.setattr(resume, 'ROSTER_WAIT', 2.0)
    rig = Rig(tmp_path)
    rig.start()
    yield rig
    rig.close()


def stopped(rig: Rig, role: str = 'worker', name: str = 'w', **extra: Any) -> dict[str, Any]:
    """A session that ran, then was stopped: ready to resume."""
    status, spawned = rig.spawn(role=role, name=name, **extra)
    assert status == 201, spawned
    sid = spawned['session_id']
    assert rig.call('POST', f'/sessions/{sid}/stop')[0] == 200
    rig.log.unlink()
    return spawned


def resume_calls(rig: Rig) -> list[dict[str, Any]]:
    return rig.calls_with('--resume')


# --- done when: resume of a live session is 409 `running`; a copy is 409 `resume_copied` -----


def test_resume_of_a_live_session_is_409_running_and_starts_nothing(up: Rig) -> None:
    up.fake.use('worker-needs-approval')  # it stays blocked, so it keeps running
    _, spawned = up.spawn()
    status, body = up.call(
        'POST', f'/sessions/{spawned["session_id"]}/resume', {'prompt': FOLLOW_UP}
    )
    assert (status, body['code']) == (409, 'running')
    assert 'through its channel' in body['message']
    assert resume_calls(up) == []


def test_a_copy_started_by_resume_is_stopped_and_answers_409_resume_copied(
    up: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SP2's race: Claude's list already shows the session stopped, but its supervisor still holds
    it, so `--resume` starts a copy. The runner's view is made stale to play that."""
    up.fake.use('worker-needs-approval')
    _, spawned = up.spawn()
    sid = spawned['session_id']
    assert up.sessions is not None
    real = up.sessions.roster

    def stale(all: bool = True) -> list[dict[str, Any]]:
        rows = real(all)
        for row in rows:
            if row.get('sessionId') == sid:
                row.pop('status', None)
                row['state'] = 'done'
        return rows

    monkeypatch.setattr(up.sessions, 'roster', stale)
    status, body = up.call('POST', f'/sessions/{sid}/resume', {'prompt': FOLLOW_UP})
    assert (status, body['code']) == (409, 'resume_copied')
    assert 'The runner stopped the copy.' in body['message']
    assert 'started a copy as' in body['detail']
    copy = body['detail'].split('started a copy as ')[1][:8]
    assert copy != spawned['short_id']
    (stop,) = up.calls_with('stop')
    assert stop['argv'][1:] == ['stop', copy]  # the copy, never the original


def test_a_copy_that_cannot_be_stopped_says_so(up: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    up.fake.use('worker-needs-approval')
    _, spawned = up.spawn()
    sid = spawned['session_id']
    assert up.sessions is not None
    real = up.sessions.roster

    def stale(all: bool = True) -> list[dict[str, Any]]:
        return [
            {**r, 'status': None, 'state': 'done'} if r['sessionId'] == sid else r
            for r in real(all)
        ]

    monkeypatch.setattr(up.sessions, 'roster', stale)
    up.mode('stop-fails')
    status, body = up.call('POST', f'/sessions/{sid}/resume', {'prompt': FOLLOW_UP})
    assert (status, body['code']) == (409, 'resume_copied')
    assert 'could not stop the copy' in body['message']
    assert 'claude stop said: error: cannot stop' in body['detail']


# --- the command line, against golden files ----------------------------------------------------


def test_resume_wakes_the_same_session_with_the_golden_command_line(up: Rig) -> None:
    spawned = stopped(up)
    sid = spawned['session_id']
    status, body = up.call('POST', f'/sessions/{sid}/resume', {'prompt': FOLLOW_UP})
    assert status == 200, body
    assert body['id'] == sid
    assert body['role'] == 'worker'
    (call,) = resume_calls(up)
    assert call['argv'] == golden(
        up, 'resume-worker.json', session_id=sid, short=spawned['short_id']
    )
    assert call['cwd'] == str(up.work.resolve())  # `--resume` finds the conversation from there


def test_a_brain_resume_gets_the_brain_tools_and_keeps_its_channel_setting(up: Rig) -> None:
    spawned = stopped(up, role='brain', name='brain', channel=False)
    sid = spawned['session_id']
    prompt = 'Good morning. Anything new overnight?'
    status, body = up.call('POST', f'/sessions/{sid}/resume', {'prompt': prompt})
    assert status == 200, body
    (call,) = resume_calls(up)
    assert call['argv'] == golden(
        up, 'resume-brain-no-channel.json', session_id=sid, short=spawned['short_id']
    )


def test_the_short_id_works_too(up: Rig) -> None:
    spawned = stopped(up)
    status, body = up.call('POST', f'/sessions/{spawned["short_id"]}/resume', {'prompt': 'go'})
    assert (status, body['id']) == (200, spawned['session_id'])


# --- the race: wait until the id has left the supervisor's roster ----------------------------


def test_resume_waits_until_the_id_leaves_the_daemon_roster(up: Rig) -> None:
    spawned = stopped(up)
    roster = up.fake.home / '.claude' / 'daemon' / 'roster.json'
    roster.parent.mkdir(parents=True, exist_ok=True)
    roster.write_text(json.dumps({'workers': [{'id': spawned['short_id']}]}))
    gone_at: list[float] = []

    def leave() -> None:
        time.sleep(0.4)
        roster.write_text(json.dumps({'workers': []}))
        gone_at.append(time.time())

    threading.Thread(target=leave, daemon=True).start()
    began = time.time()
    status, _ = up.call('POST', f'/sessions/{spawned["session_id"]}/resume', {'prompt': 'go'})
    assert status == 200
    assert gone_at and time.time() - began >= 0.4  # it did not resume before the id left


def test_resume_goes_ahead_after_the_wait_when_the_roster_never_lets_go(
    up: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(resume, 'ROSTER_WAIT', 0.2)
    spawned = stopped(up)
    roster = up.fake.home / '.claude' / 'daemon' / 'roster.json'
    roster.parent.mkdir(parents=True, exist_ok=True)
    roster.write_text(spawned['session_id'])
    status, _ = up.call('POST', f'/sessions/{spawned["session_id"]}/resume', {'prompt': 'go'})
    assert status == 200


# --- the worker cap (a control) ----------------------------------------------------------------


def test_waking_a_worker_at_the_cap_is_refused_and_starts_nothing(up: Rig) -> None:
    """A woken worker runs again, so it counts. Delete `worker_slot`'s check and this goes red."""
    up.fake.use('worker-happy-path', 'worker-needs-approval')
    spawned = stopped(up, name='w0')
    assert up.spawn(name='w1')[0] == 201
    assert up.spawn(name='w2')[0] == 201  # the cap (2) is reached
    status, body = up.call('POST', f'/sessions/{spawned["session_id"]}/resume', {'prompt': 'go'})
    assert (status, body['code']) == (409, 'cap_reached')
    assert resume_calls(up) == []


def test_waking_a_brain_at_the_worker_cap_is_fine(up: Rig) -> None:
    up.fake.use('worker-happy-path', 'worker-needs-approval')
    spawned = stopped(up, role='brain', name='brain')
    assert up.spawn(name='w1')[0] == up.spawn(name='w2')[0] == 201
    status, _ = up.call('POST', f'/sessions/{spawned["session_id"]}/resume', {'prompt': 'go'})
    assert status == 200


def test_a_worker_resume_is_refused_when_the_workers_cannot_be_counted(
    up: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned = stopped(up)
    assert up.sessions is not None
    row = up.sessions._find(spawned['session_id'])

    def broken() -> int:
        raise SessionError(503, 'roster_unavailable', 'Claude could not list its sessions.')

    monkeypatch.setattr(up.sessions, '_find', lambda ref: row)
    monkeypatch.setattr(up.sessions, '_running_workers', broken)
    status, body = up.call('POST', f'/sessions/{spawned["session_id"]}/resume', {'prompt': 'go'})
    assert (status, body['code']) == (422, 'no_supervisor')


# --- failures ----------------------------------------------------------------------------------


def test_claude_refusing_to_resume_is_reported_with_its_words(up: Rig) -> None:
    spawned = stopped(up)
    up.mode('bg-fails')
    status, body = up.call('POST', f'/sessions/{spawned["session_id"]}/resume', {'prompt': 'go'})
    assert (status, body['code'], body['detail']) == (422, 'exited', 'error: something went wrong')


def test_unreadable_resume_output_is_reported(up: Rig) -> None:
    spawned = stopped(up)
    up.mode('bg-garbled')
    status, body = up.call('POST', f'/sessions/{spawned["session_id"]}/resume', {'prompt': 'go'})
    assert (status, body['code'], body['detail']) == (422, 'exited', 'hello')


def test_resume_is_refused_without_a_supervisor(up: Rig) -> None:
    spawned = stopped(up)
    up.mode('supervisor-down')
    status, body = up.call('POST', f'/sessions/{spawned["session_id"]}/resume', {'prompt': 'go'})
    assert (status, body['code']) == (422, 'no_supervisor')
    assert resume_calls(up) == []


def test_resume_says_so_when_the_sessions_folder_is_gone(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.start()
    try:
        spawned = stopped(rig)
        rig.work.rmdir()
        status, body = rig.call(
            'POST', f'/sessions/{spawned["session_id"]}/resume', {'prompt': 'go'}
        )
        assert (status, body['code']) == (422, 'exited')
        assert 'is gone' in body['message']
    finally:
        rig.close()


@pytest.mark.parametrize(
    ('raw', 'words'),
    [
        (b'{nope', 'not valid JSON'),
        (b'[]', 'JSON object'),
        (b'{}', 'Missing field(s): prompt'),
        (b'{"prompt":"go","x":1}', 'Unknown field(s): x'),
        (b'{"prompt":""}', 'prompt must be'),
        (b'{"prompt":7}', 'prompt must be'),
    ],
)
def test_bad_resume_bodies_are_400_in_words(up: Rig, raw: bytes, words: str) -> None:
    spawned = stopped(up)
    status, body = up.call('POST', f'/sessions/{spawned["session_id"]}/resume', raw=raw)
    assert (status, body['code']) == (400, 'bad_request')
    assert words in body['message']


def test_resume_of_an_unknown_session_is_404(up: Rig) -> None:
    status, body = up.call('POST', '/sessions/nope/resume', {'prompt': 'go'})
    assert (status, body['code']) == (404, 'not_found')


def test_resume_only_answers_post(up: Rig) -> None:
    status, body = up.call('GET', '/sessions/x/resume')
    assert (status, body['code']) == (405, 'method_not_allowed')


def test_allowed_tools_by_role() -> None:
    assert allowed_tools('worker') == ALLOWED_TOOLS
    assert allowed_tools('adopted') == ALLOWED_TOOLS
    assert allowed_tools('side')[2:] == allowed_tools('brain')[2:] == BRAIN_TOOLS
    assert all('*' not in t for t in BRAIN_TOOLS)  # by name, never a wildcard


def test_harrys_two_tools_are_pre_allowed_for_brain_and_side_only() -> None:
    harry = [
        'mcp__plugin_marcel_harry__harry_find_tools',
        'mcp__plugin_marcel_harry__harry_mark_done',
    ]
    assert list(allowed_tools('brain')[-2:]) == list(allowed_tools('side')[-2:]) == harry
    assert not any('harry' in t for t in allowed_tools('worker') + allowed_tools('adopted'))


def test_resume_passes_the_recorded_permission_mode(up: Rig) -> None:
    spawned = stopped(up, permission_mode='plan')
    assert up.call('POST', f'/sessions/{spawned["session_id"]}/resume', {'prompt': 'go'})[0] == 200
    (call,) = resume_calls(up)
    argv = call['argv']
    assert argv[argv.index('--permission-mode') + 1] == 'plan'


def test_resume_of_a_session_the_runner_did_not_start_leaves_its_mode_alone(up: Rig) -> None:
    spawned = stopped(up)
    up.stop_runner()
    up.state.joinpath('sessions.json').unlink()  # the runner forgets it started it
    up.start()
    assert up.call('POST', f'/sessions/{spawned["session_id"]}/resume', {'prompt': 'go'})[0] == 200
    (call,) = resume_calls(up)
    assert '--permission-mode' not in call['argv']
