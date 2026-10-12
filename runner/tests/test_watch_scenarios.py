"""Scenario tests: the real `Sessions` and `Watcher` against the fake claude
(runner/tests/fake_claude), read through `GET /events` on a socket. Nothing here reaches the real
claude or the network."""

import json
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fake_claude import EXECUTABLE, FakeClaude
from test_watch import Stream

from marcel_runner.health import Settings
from marcel_runner.server import UnixHTTPServer, bind_socket
from marcel_runner.sessions import Sessions
from marcel_runner.usage import UsageProbe
from marcel_runner.watch import Feed, Watcher

# worker-happy-path with each step held long enough for a poll to see it.
SLOW_HAPPY_PATH = """\
description: worker-happy-path, slowed down so every state is seen by the poll.
transcript: transcripts/t1-happy-path.jsonl
job: t1-done
steps:
  - wait: 0.05
    state: working
    status: busy
    transcript: 55
    job:
      detail: Finding buggy function
  - wait: 1.0
    state: done
    status: idle
    transcript: 78
    text: done
    job:
      state: done
      tempo: idle
      result: fixed sub() bug in calc.py (commit 772d9f4); verified with check
"""


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        home = tmp_path / 'home'
        home.mkdir()
        self.fake = FakeClaude(home)
        self.work = tmp_path / 'work'
        self.work.mkdir()
        self.tmp = tmp_path
        self.sessions = Sessions(Settings(claude=EXECUTABLE, cap=4), tmp_path / 'state', home=home)
        self.watcher = Watcher(self.sessions, Feed(), interval=0.05, keepalive=0.2)
        self.dir = Path(tempfile.mkdtemp(prefix='mws-'))
        self.sock = self.dir / 'runner.sock'
        self.srv = UnixHTTPServer(
            bind_socket(self.sock),
            lambda: {'ok': True},
            UsageProbe(EXECUTABLE),
            self.sessions,
            self.watcher,
        )
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.watcher.start()

    def spawn(self, scenario: str) -> str:
        if '\n' in scenario:
            path = self.tmp / 'scenario.yaml'
            path.write_text(scenario)
            scenario = str(path)
        self.fake.use(scenario)
        p = self.fake.run(
            '--bg', '--name', 'w1', '--permission-mode', 'manual', '--', 'go', cwd=self.work
        )
        assert p.returncode == 0, p.stderr
        return p.stdout.split(' · ')[1]

    def until(self, stream: Stream, done: Any, limit: int = 50) -> list[dict[str, Any]]:
        """Read events until `done(event)` is true."""
        out = []
        for _ in range(limit):
            event = stream.next()
            assert event is not None, out
            out.append(event)
            if done(event):
                return out
        raise AssertionError(f'never saw the end; got {out}')

    def close(self) -> None:
        self.watcher.stop()
        self.srv.shutdown()
        self.srv.server_close()
        self.sock.unlink(missing_ok=True)
        self.dir.rmdir()
        self.fake.stop_all()


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path)
    yield r
    r.close()


def mine(short: str, state: str) -> Any:
    return lambda e: (
        e.get('session', {}).get('short_id') == short and e['session']['state'] == state
    )


def changes(events: list[dict[str, Any]], short: str) -> list[str]:
    return [e['session']['state'] for e in events if e.get('session', {}).get('short_id') == short]


def no_repeats(events: list[dict[str, Any]]) -> None:
    seqs = [e['seq'] for e in events]
    assert seqs == sorted(set(seqs)), seqs
    sessions = [json.dumps(e['session'], sort_keys=True) for e in events if 'session' in e]
    assert all(a != b for a, b in zip(sessions, sessions[1:], strict=False)), 'an unchanged repeat'


def test_happy_path_goes_working_then_done(rig: Rig) -> None:
    stream = Stream(rig.sock)
    short = rig.spawn(SLOW_HAPPY_PATH)
    events = rig.until(stream, mine(short, 'done'))
    stream.close()
    states = changes(events, short)
    assert states[0] == 'working'
    assert states[-1] == 'done'
    assert set(states) == {'working', 'done'}
    no_repeats(events)
    done = events[-1]
    assert done['type'] == 'session.state'
    assert done['previous_state'] == 'working'
    assert done['session']['result'].startswith('fixed sub() bug')
    assert (
        done['session']['remote_url'] == 'https://claude.ai/code/session_01T3zPDEiVuVzXeb4G5pZz3Q'
    )
    assert done['session']['remote_url'] == rig.sessions.get(short).get('remote_url')


def test_needs_approval_is_blocked_on_a_permission_prompt(rig: Rig) -> None:
    stream = Stream(rig.sock)
    short = rig.spawn('worker-needs-approval')
    events = rig.until(stream, mine(short, 'blocked'))
    blocked = events[-1]['session']
    assert blocked['waiting_for'] == 'permission prompt'
    assert blocked['status'] == 'waiting'
    assert blocked['needs'] == 'approve Bash: touch made-by-t2.txt && ls'
    rig.fake.answer(short, 'allow')
    events += rig.until(stream, mine(short, 'done'))
    stream.close()
    no_repeats(events)
    states = changes(events, short)
    assert states[-1] == 'done'
    assert 'blocked' in states
    assert states.index('blocked') < len(states) - 1


def test_limit_reached_is_blocked_without_waiting_for(rig: Rig) -> None:
    stream = Stream(rig.sock)
    short = rig.spawn('limit-reached')
    [*_, last] = rig.until(stream, mine(short, 'blocked'))
    stream.close()
    session = last['session']
    assert 'waiting_for' not in session
    assert session['needs'] == 'usage limit reached — check plan'


def test_an_api_error_is_failed_with_the_reason(rig: Rig) -> None:
    stream = Stream(rig.sock)
    short = rig.spawn('worker-fails')
    [*_, last] = rig.until(stream, mine(short, 'failed'))
    stream.close()
    assert last['session']['needs'] == 'API error'
    assert 'pid' not in last['session']


def test_a_reconnect_with_last_event_id_gets_no_duplicates_and_misses_nothing(rig: Rig) -> None:
    first = Stream(rig.sock)
    short = rig.spawn('worker-needs-approval')
    seen = rig.until(first, mine(short, 'blocked'))
    first.close()  # the hub goes away while the session waits

    rig.fake.answer(short, 'allow')
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        events, _ = rig.watcher.feed.connect(str(seen[-1]['seq']))
        if any(mine(short, 'done')(e) for e in events):
            break
        time.sleep(0.05)

    second = Stream(rig.sock, last_event_id=str(seen[-1]['seq']))
    seen += rig.until(second, mine(short, 'done'))
    [fresh] = second.take(1)  # then the fresh view: the session's current state, newest id
    second.close()
    assert fresh['seq'] == seen[-1]['seq'] == rig.watcher.feed.head
    assert fresh['session'] == seen[-1]['session']
    assert 'previous_state' not in fresh

    history = list(rig.watcher.feed._events)  # everything the runner made, in order
    upto = [e for e in history if e['seq'] <= seen[-1]['seq']]
    assert seen == upto  # every event once, none missing
    no_repeats(seen)


def test_an_unreadable_job_file_does_not_stop_the_watcher(rig: Rig) -> None:
    stream = Stream(rig.sock)
    short = rig.spawn('worker-needs-approval')
    rig.until(stream, mine(short, 'blocked'))
    (rig.fake.home / '.claude' / 'jobs' / short / 'state.json').write_text('{ not json')
    [*_, last] = rig.until(
        stream,
        lambda e: e.get('session', {}).get('short_id') == short and 'needs' not in e['session'],
    )
    stream.close()
    assert last['session']['state'] == 'blocked'
    assert last['session']['waiting_for'] == 'permission prompt'
    assert last['previous_state'] == 'blocked'
