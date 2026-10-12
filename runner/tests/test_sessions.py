"""Spawn, list, get, stop and the worker cap, through the socket, against the fake claude.

`claude` here is a small recording wrapper around the fake shim (runner/tests/fake_claude): it logs
every call (argv, cwd, environment) and then runs the shim. It also answers `daemon status`, which
the shim does not fake, and has a few switches (`Rig.mode`) for failures the shim cannot play.
"""

import json
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import UnixConnection, apart
from fake_claude import EXECUTABLE, FakeClaude

from marcel_runner import server
from marcel_runner.claude_env import scrubbed_env
from marcel_runner.health import Settings, collect_health
from marcel_runner.server import UnixHTTPServer, serve
from marcel_runner.sessions import (
    SessionError,
    Sessions,
    StateFileError,
    parse_spawn,
    state_dir_from_env,
)
from marcel_runner.usage import UsageProbe

GOLDEN = Path(__file__).parent / 'test_sessions_golden'

WRAPPER = """#!{python}
import json, os, sys
args = sys.argv[1:]
with open({log!r}, 'a') as f:
    f.write(json.dumps({{'argv': sys.argv, 'cwd': os.getcwd(), 'env': dict(os.environ)}}) + '\\n')
mode = open({mode!r}).read().split() if os.path.exists({mode!r}) else []
if args[:2] == ['daemon', 'status']:
    if 'supervisor-down' in mode:
        print('not running'); sys.exit(1)
    print('running'); print('  pid:          1'); sys.exit(0)
if args[:1] == ['agents'] and 'agents-broken' in mode:
    print('error: daemon unreachable', file=sys.stderr); sys.exit(1)
if '--bg' in args and 'bg-fails' in mode:
    print('error: something went wrong', file=sys.stderr); sys.exit(1)
if args[:1] == ['agents'] and 'agents-empty' in mode:
    print('[]'); sys.exit(0)
if '--bg' in args and ('bg-unreadable' in mode or 'bg-slow' in mode):
    import subprocess, time
    subprocess.run([{python!r}, {fake!r}, *args], capture_output=True)  # the session starts
    if 'bg-slow' in mode:
        time.sleep(5)
    print('hello'); sys.exit(0)
if '--bg' in args and 'bg-garbled' in mode:
    print('hello'); sys.exit(0)
if args[:2] == ['-p', '/usage'] and 'usage-hangs' in mode:
    import time; time.sleep(5); sys.exit(1)
if args[:1] == ['stop'] and 'stop-fails' in mode:
    print('error: cannot stop', file=sys.stderr); sys.exit(1)
if 'no-debug-line' in mode and '--debug-file' in args[: args.index('--') if '--' in args else None]:
    i = args.index('--debug-file'); del args[i : i + 2]
os.execv({python!r}, [{python!r}, {fake!r}, *args])
"""

WORK_PROMPT = 'Check disk usage of all Docker volumes and report the five largest.'


class Rig:
    """A runner on a temp socket, with the fake claude and its own state folder."""

    def __init__(self, tmp_path: Path, cap: int = 2) -> None:
        self.tmp = tmp_path
        home = tmp_path / 'home'
        home.mkdir()
        self.fake = FakeClaude(home)
        self.log = tmp_path / 'calls.jsonl'
        self.mode_file = tmp_path / 'mode'
        bindir = tmp_path / 'bin'
        bindir.mkdir()
        self.claude = bindir / 'claude'
        self.claude.write_text(
            WRAPPER.format(
                python=sys.executable,
                log=str(self.log),
                mode=str(self.mode_file),
                fake=str(EXECUTABLE),
            )
        )
        self.claude.chmod(0o755)
        self.state = tmp_path / 'state'
        self.work = tmp_path / 'work'
        self.work.mkdir()
        self.settings = Settings(claude=self.claude, cap=cap)
        self.sockdir = Path(tempfile.mkdtemp(prefix='ms-'))
        self.sock = self.sockdir / 'runner.sock'
        self.srv: UnixHTTPServer | None = None
        self.sessions: Sessions | None = None

    def mode(self, *flags: str) -> None:
        self.mode_file.write_text(' '.join(flags))

    def start(self, channel_wait: float = 5.0, spawn_timeout: float = 60.0) -> Sessions:
        """Start (or restart) the runner. A new `Sessions` reads the state file afresh."""
        n = iter(range(1, 1000))
        self.sessions = Sessions(
            self.settings,
            self.state,
            home=self.fake.home,
            cgroup_of=apart,
            channel_wait=channel_wait,
            spawn_timeout=spawn_timeout,
            poll=0.02,
            token=lambda: f'token{next(n)}',
        )
        usage = UsageProbe(self.claude, env=self.fake.env())  # the fake's HOME, never the real one
        self.srv = serve(
            self.sock, self.settings, cgroup_of=apart, usage=usage, sessions=self.sessions
        )
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self.sessions

    def stop_runner(self) -> None:
        if self.srv is not None:
            self.srv.shutdown()
            self.srv.server_close()
            self.sock.unlink(missing_ok=True)
            self.srv = None

    def close(self) -> None:
        self.stop_runner()
        self.fake.stop_all()
        shutil.rmtree(self.sockdir, ignore_errors=True)

    def call(
        self, method: str, path: str, body: Any = None, raw: bytes | None = None
    ) -> tuple[int, Any]:
        conn = UnixConnection(str(self.sock))
        conn.timeout = 30
        try:
            data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
            headers = {'content-type': 'application/json'} if data is not None else {}
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read())
        finally:
            conn.close()

    def spawn(self, role: str = 'worker', name: str = 'w', **extra: Any) -> tuple[int, Any]:
        body = {'role': role, 'name': name, 'model': 'sonnet', 'prompt': WORK_PROMPT}
        body.setdefault('cwd', str(self.work))
        body.update(extra)
        return self.call('POST', '/sessions', body)

    def calls(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def calls_with(self, word: str) -> list[dict[str, Any]]:
        return [c for c in self.calls() if word in c['argv'][1:]]

    def golden(self, name: str, **values: str) -> list[str]:
        values = {'claude': str(self.claude), 'state': str(self.state), **values}
        out = []
        for arg in json.loads((GOLDEN / name).read_text()):
            for k, v in values.items():
                arg = arg.replace('{' + k + '}', v)
            out.append(arg)
        return out


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path)
    yield r
    r.close()


@pytest.fixture
def up(rig: Rig) -> Rig:
    rig.start()
    return rig


# --- done when: each command line is asserted exactly against a golden file -------------------


def test_spawn_command_line_matches_the_golden_file(up: Rig) -> None:
    status, body = up.spawn(name='tsk-01J9ZQ6', permission_mode='manual')
    assert status == 201, body
    (call,) = up.calls_with('--bg')
    assert call['argv'] == up.golden('spawn-worker.json')
    assert call['cwd'] == str(up.work.resolve())
    assert body['channel_registered'] is True
    assert body['session_id'].startswith(body['short_id'])
    assert len(body['session_id']) == 36
    assert body == {
        'session_id': body['session_id'],
        'short_id': body['short_id'],
        'name': 'tsk-01J9ZQ6',
        'cwd': str(up.work),
        'channel_registered': True,
    }


def test_spawn_without_channel_matches_its_golden_file(up: Rig) -> None:
    status, body = up.call(
        'POST',
        '/sessions',
        {
            'role': 'brain',
            'name': 'brain',
            'model': 'opus',
            'prompt': 'Wait for channel messages.',
            'cwd': str(up.work),
            'channel': False,
        },
    )
    assert status == 201, body
    assert body['channel_registered'] is False
    (call,) = up.calls_with('--bg')
    assert call['argv'] == up.golden('spawn-no-channel.json')


def test_list_and_stop_command_lines_match_their_golden_files(up: Rig) -> None:
    up.fake.use('worker-needs-approval')
    _, spawned = up.spawn()
    up.log.unlink()
    assert up.call('GET', '/sessions')[0] == 200
    assert up.calls()[-1]['argv'] == up.golden('list-all.json')
    assert up.call('GET', '/sessions?all=false')[0] == 200
    assert up.calls()[-1]['argv'] == up.golden('list-live.json')
    up.log.unlink()
    assert up.call('POST', f'/sessions/{spawned["session_id"]}/stop')[0] == 200
    (stop,) = up.calls_with('stop')
    assert stop['argv'] == up.golden('stop.json', short=spawned['short_id'])


# --- done when: the cap -----------------------------------------------------------------------


def test_cap_of_two_refuses_the_third_worker_until_one_stops(up: Rig) -> None:
    up.fake.use('worker-needs-approval')  # workers stay blocked, so they keep running
    first = up.spawn(name='w1')
    second = up.spawn(name='w2')
    assert first[0] == second[0] == 201
    assert up.call('GET', '/health')[1]['active_workers'] == 2

    status, body = up.spawn(name='w3')
    assert status == 409
    assert body['code'] == 'cap_reached'
    assert '2 workers are already running' in body['message']
    assert len(up.calls_with('--bg')) == 2  # nothing was started

    assert up.call('POST', f'/sessions/{first[1]["session_id"]}/stop')[0] == 200
    assert up.call('GET', '/health')[1]['active_workers'] == 1
    status, body = up.spawn(name='w3')
    assert status == 201, body


def test_brain_and_side_sessions_never_count_toward_the_cap(up: Rig) -> None:
    up.fake.use('worker-needs-approval')
    assert up.spawn(role='brain', name='brain')[0] == 201
    assert up.spawn(role='side', name='side')[0] == 201
    assert up.spawn(name='w1')[0] == 201
    assert up.spawn(name='w2')[0] == 201
    assert up.call('GET', '/health')[1]['active_workers'] == 2
    assert up.spawn(name='w3')[0] == 409
    assert up.spawn(role='side', name='side2')[0] == 201  # at the cap, a side thread still starts


def test_a_runner_restart_keeps_roles_and_still_counts_the_workers(rig: Rig) -> None:
    rig.fake.use('worker-needs-approval')
    rig.start()
    _, w1 = rig.spawn(name='w1')
    assert rig.spawn(name='w2')[0] == 201
    rig.stop_runner()

    rig.start()  # a new process: everything comes from the state file
    assert rig.call('GET', '/health')[1]['active_workers'] == 2
    status, body = rig.spawn(name='w3')
    assert (status, body['code']) == (409, 'cap_reached')
    session = rig.call('GET', f'/sessions/{w1["session_id"]}')[1]
    assert session['role'] == 'worker'
    record = json.loads((rig.state / 'sessions.json').read_text())['sessions'][w1['session_id']]
    assert record == {
        'id': w1['session_id'],
        'short_id': w1['short_id'],
        'role': 'worker',
        'name': 'w1',
        'task': None,
        'permission_mode': 'auto',
        'model': 'sonnet',
        'cwd': str(rig.work),
        'channel': True,
        'started_at': record['started_at'],
    }


def test_simultaneous_spawns_never_pass_the_cap_together(tmp_path: Path) -> None:
    rig = Rig(tmp_path, cap=1)
    try:
        rig.fake.use('worker-needs-approval')
        rig.start()
        results: list[int] = []

        def go(i: int) -> None:
            results.append(rig.spawn(name=f'w{i}')[0])

        threads = [threading.Thread(target=go, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results) == [201, 409, 409]
        assert len(rig.calls_with('--bg')) == 1
    finally:
        rig.close()


# --- done when: a failed session is reported as failed, with the shim's reason ----------------


def test_a_failed_session_is_reported_failed_with_its_reason(up: Rig) -> None:
    up.fake.use('worker-fails')
    _, spawned = up.spawn()
    up.fake.wait_for(spawned['short_id'], 'failed')
    status, session = up.call('GET', f'/sessions/{spawned["session_id"]}')
    assert status == 200
    assert session['state'] == 'failed'
    assert session['needs'] == 'API error'
    assert session['detail'] == 'Creating file and listing directory'
    assert 'remote_url' not in session  # the transcript's bridge url is null
    listed = up.call('GET', '/sessions')[1]
    assert [(s['id'], s['state'], s['needs']) for s in listed] == [
        (spawned['session_id'], 'failed', 'API error')
    ]


# --- spawn failures ---------------------------------------------------------------------------


def test_untrusted_workspace_fails_the_spawn_with_claudes_words(up: Rig) -> None:
    up.fake.use('workspace-untrusted')
    status, body = up.spawn()
    assert status == 422
    assert body['code'] == 'not_trusted'
    assert 'not trusted yet' in body['message']
    assert body['detail'].startswith('Workspace not trusted. Run claude in')
    assert up.call('GET', '/sessions')[1] == []


def test_skipped_channel_fails_the_spawn_and_stops_the_session(up: Rig) -> None:
    up.fake.use('channel-skipped')
    status, body = up.spawn()
    assert status == 422
    assert body['code'] == 'channel_skipped'
    assert 'could not hear you' in body['message']
    assert body['detail'].startswith(
        'MCP server "plugin:marcel:marcel": Channel notifications skipped'
    )
    (session,) = up.call('GET', '/sessions')[1]
    assert 'status' not in session  # no process left
    assert len(up.calls_with('stop')) == 1


def test_no_channel_line_in_time_fails_the_spawn_and_stops_the_session(rig: Rig) -> None:
    rig.start(channel_wait=0.3)
    rig.mode('no-debug-line')
    rig.fake.use('worker-needs-approval')
    status, body = rig.spawn()
    assert status == 422
    assert body['code'] == 'channel_timeout'
    assert 'within 0.3 s' in body['message']
    assert len(rig.calls_with('stop')) == 1
    assert rig.call('GET', '/health')[1]['active_workers'] == 0


def test_claude_refusing_to_start_is_reported_with_its_words(up: Rig) -> None:
    up.mode('bg-fails')
    status, body = up.spawn()
    assert (status, body['code'], body['detail']) == (422, 'exited', 'error: something went wrong')


def test_unreadable_spawn_output_is_reported(up: Rig) -> None:
    up.mode('bg-garbled')
    status, body = up.spawn()
    assert (status, body['code'], body['detail']) == (422, 'exited', 'hello')


def test_spawn_rechecks_the_supervisor_every_time(up: Rig) -> None:
    assert up.spawn(name='w1')[0] == 201
    up.mode('supervisor-down')
    status, body = up.spawn(name='w2')
    assert status == 422
    assert body['code'] == 'no_supervisor'
    assert 'no background supervisor' in body['message']
    assert len(up.calls_with('--bg')) == 1


def test_spawn_is_refused_when_claude_was_missing_at_start(rig: Rig, tmp_path: Path) -> None:
    rig.settings = Settings(claude=tmp_path / 'nowhere' / 'claude', cap=2)
    rig.start()
    status, body = rig.spawn()
    assert (status, body['code']) == (422, 'no_supervisor')
    assert 'not installed' in body['message']


def test_worker_spawn_is_refused_when_the_workers_cannot_be_counted(up: Rig) -> None:
    up.mode('agents-broken')
    status, body = up.spawn()
    assert (status, body['code']) == (422, 'no_supervisor')
    assert body['detail'] == 'error: daemon unreachable'
    assert up.calls_with('--bg') == []


# --- controls: argument lists, environment, cwd, absolute path --------------------------------


def test_the_prompt_is_one_argument_never_a_shell_string(up: Rig) -> None:
    canary = up.tmp / 'pwned'
    prompt = f'it\'s "$(touch {canary})"; touch {canary} && `touch {canary}` --model opus'
    assert up.spawn(prompt=prompt)[0] == 201
    (call,) = up.calls_with('--bg')
    assert call['argv'][-2:] == ['--', prompt]
    assert not canary.exists()


def test_a_name_or_model_that_looks_like_a_flag_is_refused(up: Rig) -> None:
    for extra in ({'name': '--dangerously-skip-permissions'}, {'model': '-p'}):
        status, body = up.spawn(**extra)
        assert (status, body['code']) == (400, 'bad_request')
    assert up.calls_with('--bg') == []


def test_claude_gets_no_inherited_claude_or_anthropic_variables(
    up: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('CLAUDECODE', '1')
    monkeypatch.setenv('CLAUDE_CODE_CHILD_SESSION', '1')
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'parent')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-test')
    monkeypatch.setenv('MARCEL_KEEP', 'yes')
    up.log.unlink()  # the runner's start-up calls ran before these were set
    assert up.spawn()[0] == 201
    up.call('GET', '/sessions')
    # health.py and usage.py (other stories) run their own calls; these are this module's.
    ours = [c for c in up.calls() if c['argv'][1] in ('--bg', 'agents')]
    assert {c['argv'][1] for c in ours} == {'--bg', 'agents'}
    for call in ours:
        leaked = [k for k in call['env'] if k.startswith(('CLAUDE', 'ANTHROPIC_'))]
        assert leaked == [], call['argv']
        assert call['env']['HOME'] == str(up.fake.home)
        assert call['env']['MARCEL_KEEP'] == 'yes'


def test_scrubbed_env_keeps_everything_else() -> None:
    env = {'PATH': '/bin', 'CLAUDECODE': '1', 'ANTHROPIC_BASE_URL': 'x', 'LANG': 'C'}
    assert scrubbed_env(env, Path('/h')) == {'PATH': '/bin', 'LANG': 'C', 'HOME': '/h'}


def test_claude_is_run_by_absolute_path_only(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match='must be absolute'):
        Sessions(Settings(claude=Path('claude'), cap=2), tmp_path)


@pytest.mark.parametrize(
    ('cwd', 'words'), [('relative/dir', 'absolute path'), ('/no/such/folder', 'does not exist')]
)
def test_spawn_needs_an_existing_absolute_cwd(up: Rig, cwd: str, words: str) -> None:
    status, body = up.spawn(cwd=cwd)
    assert (status, body['code']) == (400, 'bad_request')
    assert words in body['message']
    assert up.calls_with('--bg') == []


# --- list, get, stop --------------------------------------------------------------------------


def test_list_is_normalised_newest_first_with_roles(up: Rig) -> None:
    up.fake.use('worker-needs-approval')
    _, older = up.spawn(role='brain', name='brain')
    time.sleep(0.01)
    _, newer = up.spawn(name='w1')
    up.fake.wait_for(newer['short_id'], 'blocked')
    status, sessions = up.call('GET', '/sessions')
    assert status == 200
    assert [s['id'] for s in sessions] == [newer['session_id'], older['session_id']]
    worker = sessions[0]
    assert worker['role'] == 'worker'
    assert worker['short_id'] == newer['short_id']
    assert worker['name'] == 'w1'
    assert worker['state'] == 'blocked'
    assert worker['status'] == 'waiting'
    assert worker['waiting_for'] == 'permission prompt'
    assert isinstance(worker['pid'], int)
    assert worker['started_at'].endswith('Z')
    assert worker['needs'].startswith('approve Bash:')
    assert sessions[1]['role'] == 'brain'


def test_sessions_the_runner_did_not_start_have_no_role(up: Rig) -> None:
    up.fake.run('--bg', '--name', 'stranger', '--', 'hello', cwd=up.work)
    (session,) = up.call('GET', '/sessions')[1]
    assert session['name'] == 'stranger'
    assert 'role' not in session


def test_get_returns_the_remote_control_url_and_the_result(up: Rig) -> None:
    up.fake.use('worker-happy-path')
    _, spawned = up.spawn()
    up.fake.wait_for(spawned['short_id'], 'done')
    up.fake.wait_idle(spawned['short_id'])
    status, session = up.call('GET', f'/sessions/{spawned["short_id"]}')  # short id works too
    assert status == 200
    assert session['id'] == spawned['session_id']
    assert session['remote_url'] == 'https://claude.ai/code/session_01T3zPDEiVuVzXeb4G5pZz3Q'
    assert session['result']


def test_stop_returns_the_stopped_session(up: Rig) -> None:
    up.fake.use('worker-needs-approval')
    _, spawned = up.spawn()
    up.fake.wait_for(spawned['short_id'], 'blocked')
    status, session = up.call('POST', f'/sessions/{spawned["session_id"]}/stop')
    assert status == 200
    assert session['state'] == 'stopped'
    assert 'status' not in session


def test_stop_failure_is_reported_with_claudes_words(up: Rig) -> None:
    up.fake.use('worker-needs-approval')
    _, spawned = up.spawn()
    up.mode('stop-fails')
    status, body = up.call('POST', f'/sessions/{spawned["session_id"]}/stop')
    assert (status, body['code'], body['detail']) == (502, 'stop_failed', 'error: cannot stop')


@pytest.mark.parametrize(
    ('method', 'path'), [('GET', '/sessions/nope'), ('POST', '/sessions/nope/stop')]
)
def test_unknown_session_is_404(up: Rig, method: str, path: str) -> None:
    status, body = up.call(method, path)
    assert status == 404
    assert body == {'code': 'not_found', 'message': 'There is no session nope on this machine.'}


def test_list_says_so_when_claude_cannot_list(up: Rig) -> None:
    up.mode('agents-broken')
    status, body = up.call('GET', '/sessions')
    assert (status, body['code']) == (503, 'roster_unavailable')
    assert up.call('GET', '/health')[1]['active_workers'] == 0  # health still answers


# --- HTTP edges -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('method', 'path'),
    [('DELETE', '/sessions'), ('POST', '/sessions/x'), ('GET', '/sessions/x/stop')],
)
def test_wrong_method_is_405(up: Rig, method: str, path: str) -> None:
    status, body = up.call(method, path)
    assert (status, body['code']) == (405, 'method_not_allowed')


def test_routes_of_later_stories_are_404(up: Rig) -> None:
    status, body = up.call('POST', '/sessions/x/fork', {})
    assert (status, body['code']) == (404, 'not_found')


def test_repo_spawns_are_not_supported_yet(up: Rig) -> None:
    repo = {'url': 'https://github.com/shbunder/marcel', 'task_id': 't', 'slug': 's'}
    body = {'role': 'worker', 'name': 'w', 'model': 'sonnet', 'prompt': 'x', 'repo': repo}
    status, answer = up.call('POST', '/sessions', body)
    assert (status, answer['code']) == (501, 'not_supported')


@pytest.mark.parametrize(
    ('raw', 'words'),
    [
        (b'{nope', 'not valid JSON'),
        (b'[]', 'JSON object'),
        (b'', 'JSON object'),
        (b'{"role":"worker","name":"w","model":"m","prompt":"p","cwd":"/","x":1}', 'Unknown field'),
        (b'{"role":"worker","name":"w","model":"m"}', 'Missing field(s): prompt'),
        (b'{"role":"boss","name":"w","model":"m","prompt":"p","cwd":"/"}', 'role must be'),
        (b'{"role":"worker","name":"a b","model":"m","prompt":"p","cwd":"/"}', 'name must be'),
        (b'{"role":"worker","name":"w","model":7,"prompt":"p","cwd":"/"}', 'model must be'),
        (
            b'{"role":"worker","name":"w","model":"m","prompt":"p","cwd":"/","permission_mode":"yolo"}',
            'permission_mode must be',
        ),
        (b'{"role":"worker","name":"w","model":"m","prompt":"","cwd":"/"}', 'prompt must be'),
        (
            b'{"role":"worker","name":"w","model":"m","prompt":"p","cwd":"/","channel":1}',
            'channel must be',
        ),
        (b'{"role":"worker","name":"w","model":"m","prompt":"p"}', 'exactly one of cwd and repo'),
    ],
)
def test_bad_spawn_bodies_are_400_in_words(raw: bytes, words: str) -> None:
    with pytest.raises(SessionError) as e:
        parse_spawn(raw)
    assert e.value.status == 400
    assert words in e.value.message


def test_bad_body_through_the_socket(up: Rig) -> None:
    status, body = up.call('POST', '/sessions', raw=b'{nope')
    assert (status, body['code']) == (400, 'bad_request')


def test_without_sessions_the_server_still_404s_them(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    try:
        rig.srv = serve(
            rig.sock,
            rig.settings,
            cgroup_of=apart,
            usage=UsageProbe(rig.claude, env=rig.fake.env()),
        )
        threading.Thread(target=rig.srv.serve_forever, daemon=True).start()
        assert rig.call('GET', '/sessions')[0] == 404
    finally:
        rig.close()


# --- the state file and settings --------------------------------------------------------------


def test_a_corrupt_state_file_refuses_to_start_in_words(rig: Rig) -> None:
    rig.state.mkdir()
    (rig.state / 'sessions.json').write_text('{oops')
    with pytest.raises(StateFileError, match='cannot read its state file'):
        rig.start()
    (rig.state / 'sessions.json').write_text('[]')
    with pytest.raises(StateFileError, match='no "sessions" map'):
        rig.start()


def test_main_refuses_a_corrupt_state_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / 'sessions.json').write_text('{oops')
    monkeypatch.setenv('MARCEL_RUNNER_STATE', str(tmp_path))
    monkeypatch.setenv('MARCEL_CLAUDE', str(tmp_path / 'claude'))
    assert server.main() == 1
    assert 'cannot read its state file' in capsys.readouterr().err


def test_state_dir_from_env() -> None:
    assert state_dir_from_env({'MARCEL_RUNNER_STATE': '/s'}) == Path('/s')
    assert state_dir_from_env({'XDG_STATE_HOME': '/x'}) == Path('/x/marcel/runner')
    assert state_dir_from_env({}) == Path.home() / '.local' / 'state' / 'marcel' / 'runner'


def test_a_request_without_a_body_is_read_as_empty(up: Rig) -> None:
    conn = UnixConnection(str(up.sock))  # no content-length header at all
    try:
        conn.putrequest('GET', '/sessions')
        conn.endheaders()
        assert conn.getresponse().status == 200
    finally:
        conn.close()


# --- usage is read again before each worker spawn (contract: GET /usage) -----------------------


def _wait_for(check: Any, timeout: float = 10) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


def test_a_worker_spawn_reads_usage_and_brain_spawns_do_not(up: Rig) -> None:
    assert up.call('GET', '/usage')[0] == 503  # nothing read yet: the poll is not started here
    assert up.spawn(role='brain', name='brain')[0] == 201
    time.sleep(0.3)
    assert up.calls_with('/usage') == []
    assert up.spawn(name='w1')[0] == 201
    assert _wait_for(lambda: up.call('GET', '/usage')[0] == 200)
    (call,) = up.calls_with('/usage')
    assert call['argv'][1:] == ['-p', '/usage', '--no-session-persistence']


def test_a_hanging_usage_read_never_delays_a_spawn(up: Rig) -> None:
    up.mode('usage-hangs')  # the read takes 5 s
    started = time.monotonic()
    assert up.spawn()[0] == 201
    assert time.monotonic() - started < 3
    assert _wait_for(lambda: len(up.calls_with('/usage')) == 1)


def test_a_failing_usage_refresh_never_fails_a_spawn(up: Rig) -> None:
    assert up.sessions is not None
    called: list[bool] = []

    def boom() -> None:
        called.append(True)
        raise RuntimeError('usage exploded')

    up.sessions.refresh_usage = boom
    assert up.spawn()[0] == 201
    assert _wait_for(lambda: called == [True])


# --- a spawn that fails half-way never leaves a session outside the cap -------------------------


def _active(rig: Rig) -> int:
    return rig.call('GET', '/health')[1]['active_workers']


def test_a_skipped_channel_whose_stop_fails_says_so_and_still_counts(up: Rig) -> None:
    up.fake.use('channel-skipped')
    up.mode('stop-fails')
    status, body = up.spawn()
    assert (status, body['code']) == (422, 'channel_skipped')
    assert 'could not stop it' in body['message']
    assert 'stopped it' not in body['message'].replace('could not stop it', '')
    assert body['detail'].endswith('claude stop said: error: cannot stop')
    (session,) = up.call('GET', '/sessions')[1]
    assert session['status'] == 'busy'  # still running, so it must still count
    assert _active(up) == 1


def test_a_channel_timeout_whose_stop_fails_says_so(rig: Rig) -> None:
    rig.start(channel_wait=0.3)
    rig.fake.use('worker-needs-approval')
    rig.mode('no-debug-line', 'stop-fails')
    status, body = rig.spawn()
    assert (status, body['code']) == (422, 'channel_timeout')
    assert 'could not stop it' in body['message']
    assert body['detail'] == 'claude stop said: error: cannot stop'
    assert _active(rig) == 1


def test_a_session_missing_from_claudes_list_is_stopped(up: Rig) -> None:
    up.fake.use('worker-needs-approval')
    up.mode('agents-empty')
    status, body = up.spawn()
    assert (status, body['code']) == (422, 'exited')
    assert 'stopped the session it had started' in body['message']
    up.mode()
    (session,) = up.call('GET', '/sessions')[1]
    assert session['state'] == 'stopped'
    assert session['role'] == 'worker'  # recorded by its short id
    assert _active(up) == 0


def test_a_session_missing_from_claudes_list_that_cannot_be_stopped_holds_the_cap(
    up: Rig,
) -> None:
    up.fake.use('worker-needs-approval')
    up.mode('agents-empty', 'stop-fails')
    status, body = up.spawn(name='w1')
    assert (status, body['code']) == (422, 'exited')
    assert 'could not stop it' in body['message']
    up.mode()
    assert _active(up) == 1
    assert up.spawn(name='w2')[0] == 201
    assert up.spawn(name='w3')[1]['code'] == 'cap_reached'


def test_unreadable_spawn_output_finds_the_session_by_name_and_stops_it(up: Rig) -> None:
    up.fake.use('worker-needs-approval')
    up.mode('bg-unreadable')
    status, body = up.spawn(name='w1')
    assert (status, body['code'], body['detail']) == (422, 'exited', 'hello')
    assert 'stopped the session it had started' in body['message']
    up.mode()
    (session,) = up.call('GET', '/sessions')[1]
    assert (session['name'], session['state'], session['role']) == ('w1', 'stopped', 'worker')


def test_unreadable_spawn_output_that_cannot_be_stopped_holds_the_cap(up: Rig) -> None:
    up.fake.use('worker-needs-approval')
    up.mode('bg-unreadable', 'stop-fails')
    assert up.spawn(name='w1')[1]['code'] == 'exited'
    up.mode()
    assert _active(up) == 1
    assert up.spawn(name='w2')[0] == 201
    assert up.spawn(name='w3')[1]['code'] == 'cap_reached'


def test_a_spawn_that_times_out_after_the_session_began_holds_the_cap(rig: Rig) -> None:
    rig.start(spawn_timeout=1.0)
    rig.fake.use('worker-needs-approval')
    rig.mode('bg-slow', 'stop-fails')
    status, body = rig.spawn(name='w1')
    assert (status, body['code']) == (503, 'claude_timeout')
    assert 'could not stop it' in body['message']
    rig.mode()
    assert _active(rig) == 1
    assert rig.spawn(name='w2')[0] == 201
    assert rig.spawn(name='w3')[1]['code'] == 'cap_reached'


def test_a_name_match_never_touches_an_older_session(up: Rig) -> None:
    up.fake.use('worker-needs-approval')
    up.fake.run('--bg', '--name', 'w1', '--', 'not ours', cwd=up.work)
    time.sleep(1.2)  # older than the spawn's window
    up.mode('bg-garbled')  # claude prints nonsense and starts nothing
    assert up.spawn(name='w1')[1]['code'] == 'exited'
    up.mode()
    (stranger,) = up.call('GET', '/sessions')[1]
    assert 'role' not in stranger
    assert up.calls_with('stop') == []


# --- the state file is written safely ---------------------------------------------------------


def test_the_state_file_is_flushed_to_disk_then_swapped_in(
    up: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marcel_runner import sessions as mod

    assert up.spawn(name='w1')[0] == 201
    before = (up.state / 'sessions.json').read_text()
    steps: list[str] = []
    real_fsync = mod.os.fsync
    monkeypatch.setattr(mod.os, 'fsync', lambda fd: (steps.append('fsync'), real_fsync(fd))[1])

    def refuse(src: Any, dst: Any) -> None:
        steps.append('replace')
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(mod.os, 'replace', refuse)
    assert up.spawn(name='w2')[0] == 201  # the spawn itself still works
    assert steps == ['fsync', 'replace']
    # A plain overwrite would have changed the file; the swap failed, so the old one is intact.
    assert (up.state / 'sessions.json').read_text() == before
    assert _active(up) == 2  # and this process still counts both


# --- every claude the runner starts gets the scrubbed environment ------------------------------


def _env_recorder(tmp_path: Path) -> tuple[Path, Path]:
    out = tmp_path / 'env.txt'
    script = tmp_path / 'claude'
    script.write_text(
        f'#!/bin/sh\nenv > {out}\n'
        'if [ "$1" = "--version" ]; then echo "2.1.289 (Claude Code)"; exit 0; fi\n'
        'echo "running"; echo "  pid: 1"; exit 0\n'
    )
    script.chmod(0o755)
    return script, out


def _leaked(env_file: Path) -> list[str]:
    names = [line.split('=', 1)[0] for line in env_file.read_text().splitlines() if '=' in line]
    return [n for n in names if n.startswith(('CLAUDE', 'ANTHROPIC_'))]


def test_health_checks_run_claude_without_claude_or_anthropic_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script, out = _env_recorder(tmp_path)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-test')
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', '/elsewhere')
    collect_health(Settings(claude=script, cap=1), lambda: 0, apart)
    assert out.exists()
    assert _leaked(out) == []


def test_the_usage_probe_runs_claude_without_claude_or_anthropic_variables(
    tmp_path: Path,
) -> None:
    script, out = _env_recorder(tmp_path)
    env = {'PATH': '/usr/bin:/bin', 'ANTHROPIC_API_KEY': 'sk-test', 'CLAUDE_CONFIG_DIR': '/x'}
    UsageProbe(script, env=env).refresh()
    assert out.exists()
    assert _leaked(out) == []
