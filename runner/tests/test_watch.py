"""The feed, the watcher and the event stream, against a stub roster (no claude at all).

Each control has a test here that fails when the control is removed: dedupe
(`test_an_unchanged_session_gives_no_event`), the bounded history (`test_history_is_bounded`),
resume from an id (`test_resume_gets_exactly_the_missed_events`), and recovery after errors
(`test_three_failed_polls_warn_once_and_the_watcher_recovers`,
`test_the_watcher_thread_survives_an_unexpected_error`).
"""

import json
import socket
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from marcel_runner import watch
from marcel_runner.server import UnixHTTPServer, bind_socket
from marcel_runner.sessions import SessionError
from marcel_runner.usage import UsageProbe
from marcel_runner.watch import Feed, RemoteUrls, Watcher

SID = '7aa6a32d-dc9c-43e3-b170-ee90995ba343'
SID2 = 'b18d7c1c-0f0e-4c3a-8a35-3b8d2f1e9a10'


def row(sid: str = SID, state: str = 'working', **extra: Any) -> dict[str, Any]:
    return {
        'kind': 'background',
        'id': sid[:8],
        'sessionId': sid,
        'name': 't1',
        'state': state,
        'cwd': '/work',
        **extra,
    }


class StubRoster:
    """`roster()` returns `rows`, or raises `error` (once per poll) while `errors` > 0."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.rows: list[dict[str, Any]] = []
        self.errors = 0
        self.error: Exception = SessionError(
            503, 'roster_unavailable', 'Claude could not list its sessions.', 'daemon unreachable'
        )

    def roster(self, all: bool = True) -> list[dict[str, Any]]:
        assert all
        if self.errors:
            self.errors -= 1
            raise self.error
        return self.rows

    def normalise(self, r: dict[str, Any]) -> dict[str, Any]:
        out = {
            'id': r['sessionId'],
            'short_id': r['id'],
            'name': r['name'],
            'state': r['state'],
            'cwd': r['cwd'],
        }
        if r.get('waitingFor'):
            out['waiting_for'] = r['waitingFor']
        return out


@pytest.fixture
def stub(tmp_path: Path) -> StubRoster:
    return StubRoster(tmp_path / 'home')


def states(events: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(e['type'], e.get('session', {}).get('state', '')) for e in events]


# --- the feed -----------------------------------------------------------------------------------


def test_an_unchanged_session_gives_no_event(stub: StubRoster) -> None:
    w = Watcher(stub, Feed(first_seq=1))
    stub.rows = [row()]
    assert states(w.poll()) == [('session.state', 'working')]
    assert w.poll() == []  # the control: no duplicate for an unchanged session
    assert w.poll() == []
    stub.rows = [row(state='blocked', waitingFor='permission prompt')]
    [event] = w.poll()
    assert event['previous_state'] == 'working'
    assert event['session']['waiting_for'] == 'permission prompt'
    assert [e['seq'] for e in w.feed.connect('0')[0]] == [1, 2]


def test_a_new_session_has_no_previous_state_and_a_change_of_any_field_counts(
    stub: StubRoster,
) -> None:
    w = Watcher(stub, Feed(first_seq=1))
    stub.rows = [row()]
    [first] = w.poll()
    assert 'previous_state' not in first
    stub.rows = [row(waitingFor='permission prompt')]  # same state, new field
    [second] = w.poll()
    assert second['previous_state'] == 'working'


def test_a_session_that_leaves_the_list_is_gone(stub: StubRoster) -> None:
    w = Watcher(stub, Feed(first_seq=1))
    stub.rows = [row(), row(SID2, state='done')]
    w.poll()
    stub.rows = [row()]
    [gone] = w.poll()
    assert gone['type'] == 'session.gone'
    assert gone['session']['id'] == SID2
    assert gone['session']['state'] == 'done'


def test_rows_the_contract_cannot_carry_are_left_out(stub: StubRoster) -> None:
    w = Watcher(stub, Feed(first_seq=1))
    stub.rows = [row(state='starting'), {**row(SID2), 'sessionId': None}]
    assert w.poll() == []


def test_events_follow_the_contract_shape(stub: StubRoster) -> None:
    w = Watcher(stub, Feed(first_seq=1))
    stub.rows = [row()]
    [event] = w.poll()
    assert set(event) == {'seq', 'type', 'at', 'session'}
    assert event['at'].endswith('Z')
    assert time.strptime(event['at'][:19], '%Y-%m-%dT%H:%M:%S')
    warning = w.feed.warn('hello')
    assert set(warning) == {'seq', 'type', 'at', 'message'}


def test_history_is_bounded() -> None:
    feed = Feed(keep=3, first_seq=1)
    for n in range(10):
        feed.update([{'id': SID, 'state': 'working', 'name': str(n)}])
    assert feed.head == 10
    assert [e['seq'] for e in feed._events] == [8, 9, 10]  # the control: only the last 3 kept
    # Too old to resume from: a fresh view instead, carrying the newest id.
    first, cursor = feed.connect('5')
    assert cursor == 10
    assert [(e['seq'], e['session']['name']) for e in first] == [(10, '9')]
    # Just old enough: everything after it is still there.
    first, cursor = feed.connect('7')
    assert [e['seq'] for e in first] == [8, 9, 10]


def test_the_default_history_matches_the_contract() -> None:
    assert watch.KEEP == 1000  # runner-api.yaml: "keeps the last 1000 events"
    assert watch.INTERVAL == 2.0  # runner-api.yaml: "polled every 2 s"
    assert watch.WARN_AFTER == 3  # runner-api.yaml: "a poll failed three times"


def test_resume_gets_exactly_the_missed_events() -> None:
    feed = Feed(first_seq=1)
    feed.update([{'id': SID, 'state': 'working'}])
    feed.update([{'id': SID, 'state': 'blocked'}])
    feed.update([{'id': SID, 'state': 'done'}])
    first, cursor = feed.connect('1')
    assert [e['seq'] for e in first] == [2, 3]  # the control: replay, not a fresh view
    assert cursor == 3
    first, cursor = feed.connect('3')
    assert first == []
    assert cursor == 3


def test_a_fresh_connection_gets_one_state_per_session_with_the_newest_id() -> None:
    feed = Feed(first_seq=1)
    feed.update([{'id': SID, 'state': 'working'}, {'id': SID2, 'state': 'done'}])
    feed.update([{'id': SID, 'state': 'blocked'}, {'id': SID2, 'state': 'done'}])
    first, cursor = feed.connect(None)
    assert cursor == 3
    assert [(e['seq'], e['type'], e['session']['state']) for e in first] == [
        (3, 'session.state', 'blocked'),
        (3, 'session.state', 'done'),
    ]


def test_an_empty_feed_resumes_from_its_own_head() -> None:
    feed = Feed(first_seq=1)
    assert feed.connect('0') == ([], 0)
    assert feed.connect(None) == ([], 0)


@pytest.mark.parametrize('bad', ['', 'abc', '99', '-5'])
def test_an_id_the_feed_cannot_resume_from_gives_a_fresh_view(bad: str) -> None:
    feed = Feed(first_seq=10)
    feed.update([{'id': SID, 'state': 'working'}])
    first, cursor = feed.connect(bad)
    assert [(e['seq'], e['session']['id']) for e in first] == [(10, SID)]
    assert cursor == 10


def test_ids_from_before_a_runner_restart_are_never_resumed() -> None:
    old = Feed()
    for n in range(5):
        old.update([{'id': SID, 'state': 'working', 'n': n}])
    time.sleep(0.05)  # a real restart takes seconds; a run makes far fewer than 1 event per ms
    new = Feed()  # the restarted runner
    new.update([{'id': SID, 'state': 'done'}])
    assert new.head > old.head  # ids keep going up across a restart
    first, _ = new.connect(str(old.head))
    assert [e['session']['state'] for e in first] == ['done']
    assert first[0]['seq'] == new.head


def test_after_waits_for_new_events_and_says_when_to_reconnect() -> None:
    feed = Feed(keep=2, first_seq=1)
    assert feed.after(0, timeout=0.01) == []
    threading.Timer(0.05, lambda: feed.update([{'id': SID, 'state': 'working'}])).start()
    got = feed.after(0, timeout=5)
    assert got is not None and [e['seq'] for e in got] == [1]
    for n in range(3):
        feed.update([{'id': SID, 'state': 'working', 'n': n}])
    assert feed.after(1, timeout=0) is None  # fell behind the history: reconnect
    feed.close()
    assert feed.after(4, timeout=5) is None


# --- the watcher --------------------------------------------------------------------------------


def test_three_failed_polls_warn_once_and_the_watcher_recovers(
    stub: StubRoster, capsys: pytest.CaptureFixture[str]
) -> None:
    w = Watcher(stub, Feed(first_seq=1))
    stub.rows = [row()]
    w.poll()
    stub.errors = 5  # a supervisor restart: `claude agents` fails for a while
    made = [w.poll() for _ in range(5)]
    assert [len(m) for m in made] == [0, 0, 1, 0, 0]  # one warning per outage, on the third
    warning = made[2][0]
    assert warning['type'] == 'runner.warning'
    assert "has not been able to see Claude's sessions for 3 tries" in warning['message']
    assert 'daemon unreachable' in warning['message']
    assert 'marcel-claude-daemon.service' in warning['message']
    stub.rows = []  # the restart stopped the session
    [gone] = w.poll()
    assert gone['type'] == 'session.gone'
    assert w.failures == 0
    log = capsys.readouterr().err
    assert log.count('Could not read the session list') == 1
    assert "can see Claude's sessions again" in log
    stub.errors = 3  # a second outage warns again
    assert [len(w.poll()) for _ in range(3)] == [0, 0, 1]


def test_a_short_hiccup_is_logged_but_not_a_warning(
    stub: StubRoster, capsys: pytest.CaptureFixture[str]
) -> None:
    w = Watcher(stub, Feed(first_seq=1))
    stub.errors = 1
    assert w.poll() == []
    stub.rows = [row()]
    assert len(w.poll()) == 1
    assert 'again after 1 failed tries' in capsys.readouterr().err


def test_the_watcher_thread_survives_an_unexpected_error(stub: StubRoster) -> None:
    stub.error = RuntimeError('boom')
    stub.errors = 3
    stub.rows = [row()]
    w = Watcher(stub, Feed(first_seq=1), interval=0.01)
    w.start()
    try:
        deadline = time.monotonic() + 5
        while w.feed.head < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        w.stop()
    first, _ = w.feed.connect('0')
    assert [e['type'] for e in first] == ['runner.warning', 'session.state']
    assert 'Something unexpected went wrong' in first[0]['message']
    assert 'boom' in first[0]['message']


def test_stop_without_start_closes_the_feed(stub: StubRoster) -> None:
    w = Watcher(stub)
    w.stop()
    assert w.feed.closed


# --- remote urls --------------------------------------------------------------------------------


def attachment(url: Any) -> str:
    return json.dumps(
        {'type': 'attachment', 'attachment': {'type': 'remote_session_change', 'url': url}}
    )


def test_remote_url_is_read_incrementally(tmp_path: Path) -> None:
    path = tmp_path / 't.jsonl'
    urls = RemoteUrls()
    assert urls.get(SID, path) is None  # no transcript yet
    path.write_text(attachment('https://claude.ai/code/session_1') + '\n' + '{"half": ')
    assert urls.get(SID, path) == 'https://claude.ai/code/session_1'
    offset = urls._seen[SID][0]
    assert offset == len(attachment('https://claude.ai/code/session_1')) + 1  # not the half line
    with path.open('a') as f:
        f.write('1}\n' + attachment('https://claude.ai/code/session_2') + '\n')
    assert urls.get(SID, path) == 'https://claude.ai/code/session_2'
    with path.open('a') as f:
        f.write(attachment(None) + '\n')
    assert urls.get(SID, path) is None  # Remote Control was switched off


def test_remote_url_ignores_what_it_cannot_read_and_starts_over_on_a_rewrite(
    tmp_path: Path,
) -> None:
    path = tmp_path / 't.jsonl'
    urls = RemoteUrls()
    path.write_text(
        attachment('https://claude.ai/code/session_1')
        + '\n'
        + 'not json remote_session_change\n'
        + json.dumps({'attachment': 'remote_session_change'})
        + '\n'
        + json.dumps({'attachment': {'type': 'other', 'x': 'remote_session_change'}})
        + '\n'
        + attachment('http://insecure')
        + '\n'
    )
    assert urls.get(SID, path) is None  # the last one is not https
    path.write_text(attachment('https://claude.ai/code/session_3') + '\n')  # shorter: rewritten
    assert urls.get(SID, path) == 'https://claude.ai/code/session_3'
    urls.keep_only([SID2])
    assert urls._seen == {}


def test_the_watcher_adds_the_remote_url_and_forgets_gone_sessions(stub: StubRoster) -> None:
    transcript = stub.home / '.claude' / 'projects' / '-work' / f'{SID}.jsonl'
    transcript.parent.mkdir(parents=True)
    transcript.write_text(attachment('https://claude.ai/code/session_1') + '\n')
    w = Watcher(stub, Feed(first_seq=1))
    stub.rows = [row()]
    [event] = w.poll()
    assert event['session']['remote_url'] == 'https://claude.ai/code/session_1'
    stub.rows = []
    w.poll()
    assert w.remote._seen == {}


# --- GET /events over the socket ----------------------------------------------------------------


class Stream:
    """A minimal SSE client on the runner's unix socket."""

    def __init__(self, sock: Path, last_event_id: str | None = None, path: str = '/events') -> None:
        self.s = socket.socket(socket.AF_UNIX)
        self.s.settimeout(5)
        self.s.connect(str(sock))
        extra = f'Last-Event-ID: {last_event_id}\r\n' if last_event_id is not None else ''
        self.s.sendall(f'GET {path} HTTP/1.1\r\nHost: runner\r\n{extra}\r\n'.encode())
        self.f = self.s.makefile('rb')
        self.status = self.f.readline().decode()
        self.headers: dict[str, str] = {}
        while (line := self.f.readline().decode().strip()) != '':
            name, _, value = line.partition(':')
            self.headers[name.lower()] = value.strip()
        self.comments: list[str] = []

    def next(self) -> dict[str, Any] | None:
        """The next event, or None when the stream ended. Fails after 10 s of keepalives only."""
        event_id, data = None, None
        deadline = time.monotonic() + 10
        while True:
            assert time.monotonic() < deadline, 'no event in 10 s'
            line = self.f.readline().decode()
            if not line:
                return None
            line = line.rstrip('\n')
            if line.startswith(':'):
                self.comments.append(line)
            elif line.startswith('id: '):
                event_id = line[4:]
            elif line.startswith('data: '):
                data = json.loads(line[6:])
            elif line == '' and data is not None:
                assert event_id == str(data['seq'])
                return data

    def take(self, n: int) -> list[dict[str, Any]]:
        out = []
        for _ in range(n):
            event = self.next()
            assert event is not None
            out.append(event)
        return out

    def close(self) -> None:
        self.f.close()
        self.s.close()


class Server:
    def __init__(self, watcher: Watcher | None) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix='mw-'))
        self.sock = self.dir / 'runner.sock'
        claude = Path('/nonexistent/claude')
        self.srv = UnixHTTPServer(
            bind_socket(self.sock), lambda: {'ok': True}, UsageProbe(claude), None, watcher
        )
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.sock.unlink(missing_ok=True)
        self.dir.rmdir()


@pytest.fixture
def served(stub: StubRoster) -> Iterator[tuple[Server, Watcher]]:
    w = Watcher(stub, Feed(first_seq=1), keepalive=0.05)
    s = Server(w)
    yield s, w
    w.stop()
    s.close()


def test_events_stream_resumes_without_duplicates_or_gaps(
    served: tuple[Server, Watcher], stub: StubRoster
) -> None:
    server, w = served
    stub.rows = [row()]
    w.poll()
    a = Stream(server.sock)
    assert a.status.startswith('HTTP/1.0 200')
    assert a.headers['content-type'] == 'text/event-stream'
    seen = a.take(1)  # the fresh view
    stub.rows = [row(state='blocked', waitingFor='permission prompt')]
    w.poll()
    seen += a.take(1)  # live
    a.close()
    stub.rows = [row(state='working')]
    w.poll()
    stub.rows = [row(state='done')]
    w.poll()
    b = Stream(server.sock, last_event_id=str(seen[-1]['seq']))
    seen += b.take(2)
    b.close()
    assert [e['seq'] for e in seen] == [1, 2, 3, 4]
    assert [e['session']['state'] for e in seen] == ['working', 'blocked', 'working', 'done']


def test_a_quiet_stream_sends_keepalives_and_ends_when_the_runner_stops(
    served: tuple[Server, Watcher],
) -> None:
    server, w = served
    a = Stream(server.sock)
    time.sleep(0.2)
    w.feed.close()
    assert a.next() is None
    assert a.comments  # ': still here' while nothing happened
    a.close()


def test_a_client_that_left_does_not_break_the_stream(
    served: tuple[Server, Watcher], stub: StubRoster
) -> None:
    server, w = served
    a = Stream(server.sock)
    a.close()
    stub.rows = [row()]
    w.poll()  # the server's write to the closed client fails quietly
    time.sleep(0.1)
    b = Stream(server.sock, last_event_id='0')
    assert [e['seq'] for e in b.take(1)] == [1]
    b.close()


def test_events_answers_only_get(served: tuple[Server, Watcher]) -> None:
    server, _ = served
    s = Stream(server.sock, path='/events')
    s.close()
    conn = socket.socket(socket.AF_UNIX)
    conn.connect(str(server.sock))
    conn.sendall(b'POST /events HTTP/1.1\r\nHost: runner\r\ncontent-length: 0\r\n\r\n')
    reply = conn.makefile('rb').read().decode()
    conn.close()
    assert ' 405 ' in reply.splitlines()[0]
    assert 'method_not_allowed' in reply


def test_events_is_404_on_a_runner_without_a_watcher() -> None:
    server = Server(None)
    try:
        conn = socket.socket(socket.AF_UNIX)
        conn.connect(str(server.sock))
        conn.sendall(b'GET /events HTTP/1.1\r\nHost: runner\r\n\r\n')
        reply = conn.makefile('rb').read().decode()
        conn.close()
    finally:
        server.close()
    assert ' 404 ' in reply.splitlines()[0]
