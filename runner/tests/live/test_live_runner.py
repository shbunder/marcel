"""S-03.8: the runner with the real `claude`: spawn → done, tail, stop, the cap, and the pre-allowed
`reply` in `manual` mode after a spawn, a fork and a resume (S-05.1)."""

import time
from collections.abc import Iterator

import pytest
from live_support import Runner, live_runner

pytestmark = pytest.mark.live

REPLY = 'mcp__plugin_marcel_marcel__reply'


@pytest.fixture(scope='module')
def runner(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Runner]:
    with live_runner(tmp_path_factory.mktemp('live-runner')) as live:
        yield live


def stamp() -> str:
    return time.strftime('%H%M%S')


def test_health_sees_the_supervisor_in_its_own_unit(runner: Runner) -> None:
    status, health = runner.call('GET', '/health')
    assert status == 200
    assert health['ok'] is True, health
    assert health['supervisor'] == {'running': True, 'own_unit': True}
    assert health['cap'] == 1


def test_spawn_runs_to_done_and_the_tail_shows_the_answer(runner: Runner) -> None:
    status, spawned = runner.spawn(
        f'live-pong-{stamp()}', 'Answer with exactly the word PONG and nothing else.', channel=False
    )
    assert status == 201, spawned
    done = runner.wait(spawned['session_id'], {'done', 'failed', 'stopped'})
    assert done['state'] == 'done', done

    events = runner.events(spawned['session_id'])
    said = [
        e['data']['text']
        for e in events
        if e['type'] == 'text' and e['data']['role'] == 'assistant'
    ]
    assert any('PONG' in text for text in said), events
    offsets = [e['offset'] for e in events]
    assert offsets == sorted(offsets)
    assert {e['session_id'] for e in events} == {spawned['session_id']}

    # A done worker keeps its process, so it still counts toward the cap until it is stopped,
    # as the hub does once its task is finished (docs/runner/sessions.md).
    assert runner.call('GET', '/health')[1]['active_workers'] == 1
    assert runner.call('POST', f'/sessions/{spawned["session_id"]}/stop')[0] == 200
    runner.wait_for_workers(0)  # an idle worker that is stopped stays `done`, with no process


def test_the_cap_refuses_a_second_worker_and_stop_ends_the_first(runner: Runner) -> None:
    status, first = runner.spawn(
        f'live-sleep-{stamp()}',
        'Use the Bash tool to run `sleep 120`, then answer DONE.',
        channel=False,
    )
    assert status == 201, first
    runner.wait(first['session_id'], {'working'}, timeout=60)
    assert runner.call('GET', '/health')[1]['active_workers'] == 1

    status, refused = runner.spawn(f'live-second-{stamp()}', 'Answer OK.', channel=False)
    assert (status, refused['code']) == (409, 'cap_reached'), refused

    status, _ = runner.call('POST', f'/sessions/{first["session_id"]}/stop')
    assert status == 200
    stopped = runner.wait(first['session_id'], {'stopped', 'done', 'failed'}, timeout=60)
    assert stopped['state'] == 'stopped', stopped
    runner.wait_for_workers(0)  # the list can lag the stop by a poll


def reply_ran_without_a_prompt(runner: Runner, session_id: str, after: int = 0) -> None:
    """The session called `reply` and got its result back with no permission prompt: it ran to
    `done` (a prompt in `manual` mode leaves it `blocked`) and the call has an undenied result."""
    done = runner.wait(session_id, {'done', 'blocked', 'failed', 'stopped'})
    assert done['state'] == 'done', done
    events = [e for e in runner.events(session_id) if e['offset'] > after]
    calls = [
        e['data']['tool_use_id']
        for e in events
        if e['type'] == 'tool_call' and e['data']['tool'] == REPLY
    ]
    assert calls, f'no {REPLY} call: {events}'
    results = [
        e['data']
        for e in events
        if e['type'] == 'tool_result' and e['data']['tool_use_id'] in calls
    ]
    assert results and not any(r.get('denied') for r in results), results


ASK = f'Call the {REPLY} tool once with the text "live check {{n}}". Then answer DONE.'


def test_reply_is_pre_allowed_in_manual_mode_after_spawn_fork_and_resume(runner: Runner) -> None:
    # A brain, as the hub starts it (only a brain or a side thread can be forked), in `manual`.
    status, spawned = runner.spawn(
        f'live-reply-{stamp()}', ASK.format(n=1), role='brain', permission_mode='manual'
    )
    assert status == 201, spawned
    assert spawned['channel_registered'] is True
    sid = spawned['session_id']
    reply_ran_without_a_prompt(runner, sid)

    # A side thread's first prompt only says to wait (the hub's); its replies come on later turns.
    status, forked = runner.call(
        'POST', f'/sessions/{sid}/fork', {'name': f'live-fork-{stamp()}', 'prompt': 'Answer READY.'}
    )
    assert status == 201, forked
    fid = forked['session_id']
    runner.started.append(fid)
    assert runner.wait(fid, {'done', 'blocked', 'failed'})['state'] == 'done'
    woke(runner, fid, ASK.format(n=2))  # the fork, woken: its mode is the brain's `manual`

    woke(runner, sid, ASK.format(n=3))  # the brain, woken


def woke(runner: Runner, session_id: str, prompt: str) -> None:
    """Stop the session, wake it with `prompt`, and check its `reply` ran with no prompt."""
    end = max(e['offset'] for e in runner.events(session_id))
    assert runner.call('POST', f'/sessions/{session_id}/stop')[0] == 200
    time.sleep(3)  # SP2: the id leaves the supervisor's roster a few seconds after a stop
    status, body = runner.call('POST', f'/sessions/{session_id}/resume', {'prompt': prompt})
    assert status == 200, body
    assert body['id'] == session_id  # the same session, not a copy
    reply_ran_without_a_prompt(runner, session_id, after=end)
