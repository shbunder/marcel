"""`GET /sessions/{id}/tail` on a real socket: resuming by offset, following a growing file, one
tail per file, errors in plain words, and the fake claude end to end. Nothing here reaches the real
claude or the network."""

import json
import socket
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fake_claude import EXECUTABLE, FakeClaude
from test_transcript import FIXTURES, SUBAGENT, normalise_file, valid

from marcel_runner.health import Settings
from marcel_runner.server import UnixHTTPServer, bind_socket
from marcel_runner.sessions import SessionError, Sessions, project_dir
from marcel_runner.transcript import Tails
from marcel_runner.usage import UsageProbe

SID = 'b18d7c1c-1008-4ee3-8dbf-1d988f51b36b'
CWD = '/home/me/work/scratch'
AGENT = 'a918dd7c41e7ee8b8'


class Tail:
    """A minimal SSE client for one tail."""

    def __init__(self, sock: Path, query: str = '', headers: str = '', ref: str = SID) -> None:
        self.s = socket.socket(socket.AF_UNIX)
        self.s.settimeout(5)
        self.s.connect(str(sock))
        request = f'GET /sessions/{ref}/tail?{query} HTTP/1.1\r\nHost: runner\r\n{headers}\r\n'
        self.s.sendall(request.encode())
        self.f = self.s.makefile('rb')
        self.status = int(self.f.readline().split()[1])
        self.headers: dict[str, str] = {}
        while (line := self.f.readline().decode().strip()) != '':
            name, _, value = line.partition(':')
            self.headers[name.lower()] = value.strip()
        self.comments: list[str] = []

    def json(self) -> dict[str, Any]:
        return json.loads(self.f.read())

    def next(self) -> dict[str, Any] | None:
        """The next event (checked against the contract), or None when the stream ended."""
        fields: dict[str, str] = {}
        while True:
            line = self.f.readline().decode()
            if not line:
                return None
            line = line.rstrip('\n')
            if line.startswith(':'):
                self.comments.append(line)
            elif line:
                name, _, value = line.partition(': ')
                fields[name] = value
            elif 'data' in fields:
                event = valid(json.loads(fields['data']))
                assert fields['event'] == 'transcript'
                assert fields['id'] == str(event['offset'])
                return event

    def all(self) -> list[dict[str, Any]]:
        out = []
        while (event := self.next()) is not None:
            out.append(event)
        return out

    def take(self, n: int) -> list[dict[str, Any]]:
        out = []
        for _ in range(n):
            event = self.next()
            assert event is not None, f'the stream ended after {out}'
            out.append(event)
        return out

    def close(self) -> None:
        self.f.close()
        self.s.close()


class Rig:
    def __init__(self, home: Path, sessions: Sessions | None = None, **tail_args: Any) -> None:
        self.home = home
        self.rows: list[dict[str, Any]] = [{'id': SID[:8], 'sessionId': SID, 'cwd': CWD}]
        self.roster_error: SessionError | None = None
        roster = sessions.roster if sessions is not None else self.roster
        self.tails = Tails(roster, home, **{'poll': 0.02, 'keepalive': 5.0, **tail_args})
        self.dir = Path(tempfile.mkdtemp(prefix='mtt-'))
        self.sock = self.dir / 'runner.sock'
        self.srv = UnixHTTPServer(
            bind_socket(self.sock),
            lambda: {'ok': True},
            UsageProbe(EXECUTABLE),
            sessions,
            None,
            self.tails,
        )
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def roster(self, all: bool = True) -> list[dict[str, Any]]:
        if self.roster_error is not None:
            raise self.roster_error
        return self.rows

    def file(self, sid: str = SID, cwd: str = CWD) -> Path:
        path = self.home / '.claude' / 'projects' / project_dir(cwd) / f'{sid}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def tail(self, query: str = '', headers: str = '', ref: str = SID) -> Tail:
        return Tail(self.sock, query, headers, ref)

    def close(self) -> None:
        self.tails.close()
        self.srv.shutdown()
        self.srv.server_close()
        self.sock.unlink(missing_ok=True)
        self.dir.rmdir()


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path / 'home')
    yield r
    r.close()


def append(path: Path, data: bytes) -> None:
    with path.open('ab') as f:
        f.write(data)


def until(check: Any, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, 'timed out'
        time.sleep(0.01)


T2 = FIXTURES / 't2-permission-approved.jsonl'


def test_follow_false_streams_the_whole_transcript_and_closes(rig: Rig) -> None:
    rig.file().write_bytes(T2.read_bytes())
    tail = rig.tail('follow=false')
    assert tail.status == 200 and tail.headers['content-type'] == 'text/event-stream'
    events = tail.all()
    expected = normalise_file(T2)
    assert [(e['type'], e['data']) for e in events] == [(e['type'], e['data']) for e in expected]
    assert {e['session_id'] for e in events} == {SID}
    assert events[-1]['offset'] == T2.stat().st_size


def test_the_short_id_finds_the_same_transcript(rig: Rig) -> None:
    rig.file().write_bytes(T2.read_bytes())
    assert len(rig.tail('follow=false', ref=SID[:8]).all()) == len(normalise_file(T2))


@pytest.mark.parametrize('how', ['offset', 'last-event-id'])
def test_resuming_from_an_offset_gives_no_duplicates_and_misses_nothing(rig: Rig, how: str) -> None:
    """Control: a reconnect at the last id seen continues exactly after it."""
    rig.file().write_bytes(T2.read_bytes())
    full = rig.tail('follow=false').all()
    for cut in sorted({e['offset'] for e in full}):
        if how == 'offset':
            rest = rig.tail(f'follow=false&offset={cut}').all()
        else:  # Last-Event-ID wins over offset
            rest = rig.tail('follow=false&offset=0', f'Last-Event-ID: {cut}\r\n').all()
        before = [e for e in full if e['offset'] <= cut]
        assert before + rest == full, cut


def test_resuming_mid_line_starts_at_that_line_never_mid_line(rig: Rig) -> None:
    rig.file().write_bytes(T2.read_bytes())
    full = rig.tail('follow=false').all()
    first, second = sorted({e['offset'] for e in full})[:2]
    rest = rig.tail(f'follow=false&offset={first + 3}').all()
    assert rest[0]['offset'] == second
    assert rest == [e for e in full if e['offset'] > first]


def test_a_growing_file_is_followed_and_a_partial_line_waits(rig: Rig) -> None:
    lines = T2.read_bytes().splitlines(keepends=True)
    path = rig.file()
    path.write_bytes(b''.join(lines[:7]))  # up to and including the prompt
    tail = rig.tail()
    [prompt] = tail.take(1)
    assert prompt['type'] == 'text' and prompt['offset'] == path.stat().st_size
    call = lines[26]
    append(path, b''.join(lines[7:26]) + call[:40])  # dropped lines, then half the tool call
    time.sleep(0.2)
    append(path, call[40:])
    [event] = tail.take(1)
    assert event['type'] == 'tool_call' and event['offset'] == path.stat().st_size
    tail.close()


def test_a_transcript_that_does_not_exist_yet_is_waited_for(rig: Rig) -> None:
    assert rig.tail('follow=false').all() == []
    tail = rig.tail()
    time.sleep(0.1)
    rig.file().write_bytes(T2.read_bytes().splitlines(keepends=True)[6])
    [event] = tail.take(1)
    assert event['type'] == 'text'
    tail.close()


def test_a_second_tail_of_the_same_file_replaces_the_first(rig: Rig) -> None:
    """Control: one tail per file. The older stream ends; the newer one keeps going."""
    path = rig.file()
    path.write_bytes(b'')
    first = rig.tail()
    until(lambda: len(rig.tails._open) == 1)
    second = rig.tail('offset=0')
    assert first.next() is None  # ended by the newer tail
    append(path, T2.read_bytes().splitlines(keepends=True)[6])
    [event] = second.take(1)
    assert event['type'] == 'text'
    assert len(rig.tails._open) == 1
    second.close()
    first.close()


def test_the_main_transcript_and_a_subagent_are_two_files(rig: Rig) -> None:
    path = rig.file()
    path.write_bytes(b'')
    sub = path.with_suffix('') / 'subagents' / f'agent-{AGENT}.jsonl'
    sub.parent.mkdir(parents=True)
    sub.write_bytes(SUBAGENT.read_bytes())
    main = rig.tail()
    events = rig.tail(f'subagent={AGENT}&follow=false').all()
    assert len(events) == len(normalise_file(SUBAGENT)) and {e['subagent_id'] for e in events} == {
        AGENT
    }
    append(path, T2.read_bytes().splitlines(keepends=True)[6])
    [event] = main.take(1)  # the subagent tail did not end the main one
    assert 'subagent_id' not in event
    main.close()


def test_a_resume_after_an_agent_call_still_names_the_subagent(rig: Rig) -> None:
    source = FIXTURES / 't1-happy-path.jsonl'
    rig.file().write_bytes(source.read_bytes())
    lines = source.read_bytes().splitlines(keepends=True)
    after_call = sum(len(x) for x in lines[:32])  # the Agent call is line 31, its launch line 32
    events = rig.tail(f'follow=false&offset={after_call}').all()
    [start] = [e for e in events if e['type'] == 'subagent_start']
    assert start['data']['description'] == 'Find buggy function'
    assert start['data']['subagent_type'] == 'general-purpose'


def test_a_truncated_file_is_streamed_again_from_the_start(rig: Rig) -> None:
    lines = T2.read_bytes().splitlines(keepends=True)
    path = rig.file()
    path.write_bytes(b''.join(lines[:28]))
    tail = rig.tail()
    assert [e['type'] for e in tail.take(3)] == ['text', 'tool_call', 'tool_result']
    path.write_bytes(lines[6])
    [again] = tail.take(1)
    assert again['type'] == 'text' and again['offset'] == len(lines[6])
    tail.close()


def test_a_too_long_line_becomes_one_raw_event(tmp_path: Path) -> None:
    rig = Rig(tmp_path / 'home', max_line=1000)
    try:
        lines = T2.read_bytes().splitlines(keepends=True)
        huge = json.dumps({'type': 'user', 'message': {'content': 'x' * 5000}}).encode() + b'\n'
        rig.file().write_bytes(huge + lines[6])
        events = rig.tail('follow=false').all()
        assert [e['type'] for e in events] == ['raw', 'text']
        assert events[0]['data']['entry']['bytes'] == len(huge)
        assert events[1]['offset'] == len(huge) + len(lines[6])
    finally:
        rig.close()


def test_a_quiet_stream_sends_keepalives_and_notices_the_client_leaving(tmp_path: Path) -> None:
    rig = Rig(tmp_path / 'home', keepalive=0.05)
    try:
        rig.file().write_bytes(b'')
        tail = rig.tail()
        until(lambda: tail.f.readline().startswith(b': still here'))
        tail.close()
        until(lambda: rig.tails._open == {})  # the write failed, the tail let go of the file
    finally:
        rig.close()


def test_closing_ends_every_tail_and_refuses_to_hang_new_ones(rig: Rig) -> None:
    rig.file().write_bytes(b'')
    tail = rig.tail()
    until(lambda: len(rig.tails._open) == 1)
    rig.tails.close()
    assert tail.next() is None
    assert rig.tail().next() is None


@pytest.mark.parametrize(
    ('query', 'ref', 'status', 'code', 'words'),
    [
        ('', 'nope', 404, 'not_found', 'There is no session nope on this machine.'),
        ('offset=-4', SID, 400, 'bad_request', 'whole number of bytes, not "-4"'),
        ('offset=ten', SID, 400, 'bad_request', 'whole number of bytes, not "ten"'),
        ('offset=%C2%B2', SID, 400, 'bad_request', 'whole number of bytes, not "²"'),
        ('subagent=../../etc', SID, 400, 'bad_request', 'is not a subagent id'),
    ],
)
def test_bad_requests_get_a_plain_words_error(
    rig: Rig, query: str, ref: str, status: int, code: str, words: str
) -> None:
    tail = rig.tail(query, ref=ref)
    body = tail.json()
    assert tail.status == status and body['code'] == code and words in body['message']


def test_a_roster_failure_is_passed_on(rig: Rig) -> None:
    rig.roster_error = SessionError(503, 'roster_unavailable', 'Claude could not list sessions.')
    tail = rig.tail()
    assert tail.status == 503 and tail.json()['code'] == 'roster_unavailable'


def test_only_get_is_answered(rig: Rig) -> None:
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(5)
    s.connect(str(rig.sock))
    s.sendall(
        f'POST /sessions/{SID}/tail HTTP/1.1\r\nHost: runner\r\nContent-Length: 0\r\n\r\n'.encode()
    )
    reply = s.makefile('rb').read().decode()
    s.close()
    assert reply.startswith('HTTP/1.0 405') and 'only answers GET' in reply


# --- the fake claude, end to end ---------------------------------------------------------------


def test_a_fake_worker_transcript_is_found_and_streamed(tmp_path: Path) -> None:
    """The fake writes where real claude does (folder from the cwd, subagents beside it); the
    runner finds both through the real `Sessions` roster."""
    home = tmp_path / 'home'
    home.mkdir()
    fake = FakeClaude(home)
    work = tmp_path / 'work_dir.v2'  # `_` and `.` in the cwd: the folder-name rule is a guess
    work.mkdir()
    sessions = Sessions(Settings(claude=EXECUTABLE, cap=4), tmp_path / 'state', home=home)
    rig = Rig(home, sessions)
    try:
        fake.use('worker-happy-path')
        p = fake.run('--bg', '--name', 'w1', '--', 'go', cwd=work)
        assert p.returncode == 0, p.stderr
        short = p.stdout.split(' · ')[1]
        fake.wait_for(short, 'done')
        events = rig.tail('follow=false', ref=short).all()
        types = {e['type'] for e in events}
        assert {'text', 'tool_call', 'tool_result', 'diff', 'subagent_start'} <= types
        [start] = [e['data'] for e in events if e['type'] == 'subagent_start']
        sub = rig.tail(f'follow=false&subagent={start["agent_id"]}', ref=short).all()
        assert sub and {e['subagent_id'] for e in sub} == {start['agent_id']}
    finally:
        rig.close()
        fake.stop_all()
