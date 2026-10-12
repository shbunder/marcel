"""Cloud spawn and send (S-03.6): `claude --cloud` on a pty, `claude -p --cloud <id>` headless.

The happy paths run the fake claude shim's SP3 recordings (`--cloud` refuses without a terminal,
so the pty is real). Edge cases (a hang, a crash, a refusal, a link to somewhere else) use small
`claude` scripts of this file's own, written per test: the shim belongs to S-02.3.
"""

import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import apart
from fake_claude import EXECUTABLE, FakeClaude
from test_sessions import Rig

from marcel_runner import cloud as cloud_mod
from marcel_runner.cloud import MAX_OUTPUT, Cloud, clean, page_url, run_on_pty
from marcel_runner.server import serve
from marcel_runner.sessions import SessionError, Sessions
from marcel_runner.usage import UsageProbe

RECORDED_ID = 'session_0168oBNCs8Q2BrHDFu6tYUdh'  # SP3, fixtures/cloud/create-pty-output.txt
TOKEN = 'mt_' + 'Zq8xL2vR9kP4wN7tY3bH6cJ5'  # the kind of per-task token a worker prompt carries
PROMPT = f'You are a Marcel worker for task tsk_01J9ZQ5. Report with token {TOKEN} when done.'


def scenario(tmp_path: Path, body: str) -> Path:
    path = tmp_path / 'cloud-scenario.yaml'
    path.write_text(body)
    return path


def script(tmp_path: Path, body: str, name: str = 'claude') -> Path:
    """A `claude` of this file's own: Python, with `args` and `pidfile` ready."""
    path = tmp_path / name
    path.write_text(
        f'#!{sys.executable}\n'
        'import json, os, sys, time\n'
        'args = sys.argv[1:]\n'
        f'pidfile = {str(tmp_path / "pids")!r}\n'
        'open(pidfile, "a").write(f"{os.getpid()}\\n")\n' + body
    )
    path.chmod(0o755)
    return path


def gone(pid: int) -> bool:
    """True when `pid` has exited AND been reaped: a zombie still answers signal 0."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def pids(tmp_path: Path) -> list[int]:
    return [int(p) for p in (tmp_path / 'pids').read_text().split()]


def open_fds() -> int:
    return len(os.listdir('/dev/fd'))


@pytest.fixture
def fake(tmp_path: Path) -> FakeClaude:
    return FakeClaude(tmp_path / 'home')


@pytest.fixture
def shim(fake: FakeClaude) -> Cloud:
    return Cloud(EXECUTABLE, fake.home)


# --- the shim scenario ----------------------------------------------------------------------


def test_spawn_reads_the_recorded_sp3_output(shim: Cloud, fake: FakeClaude, tmp_path: Path) -> None:
    fake.use(
        str(scenario(tmp_path, f'cloud:\n  session_id: {RECORDED_ID}\n  title: SP3 cloud spike\n'))
    )
    out = shim.spawn(json.dumps({'prompt': 'SP3 cloud spike: post a report'}).encode())
    # CloudSpawned: exactly these keys; the link without `?from=cli&m=0`.
    assert out == {
        'session_id': RECORDED_ID,
        'url': f'https://claude.ai/code/{RECORDED_ID}',
        'title': 'SP3 cloud spike',
    }


def test_spawn_then_send_through_the_runner(tmp_path: Path) -> None:
    rig = CloudRig(tmp_path)
    try:
        rig.start()
        code, spawned = rig.call('POST', '/cloud/sessions', {'prompt': 'Fix the flaky login test'})
        assert code == 201, spawned
        assert set(spawned) == {'session_id', 'url', 'title'}
        cloud_id = spawned['session_id']
        assert re.fullmatch(r'session_[A-Za-z0-9]+', cloud_id)
        assert spawned['url'] == f'https://claude.ai/code/{cloud_id}'
        assert spawned['title'] == 'Fix the flaky login test'

        text = 'Also add a test for the empty case.'
        code, ack = rig.call('POST', f'/cloud/sessions/{cloud_id}/messages', {'text': text})
        assert code == 202, ack
        assert ack == {'ok': True, 'session_id': cloud_id, 'url': spawned['url']}
        record = json.loads(rig.fake.store.cloud_file(cloud_id).read_text())
        assert record['prompt'] == 'Fix the flaky login test'
        assert record['messages'] == [text]

        create, send = rig.calls_with('--cloud')
        assert create['argv'][1:] == ['--cloud', '--', 'Fix the flaky login test']
        assert send['argv'][1:] == [
            '-p', '--cloud', cloud_id, '--output-format', 'json', '--', text,
        ]  # fmt: skip
        # The runner's own Claude markers and API keys never reach a cloud spawn.
        assert not [k for k in create['env'] if k.startswith(('CLAUDE', 'ANTHROPIC_'))]
    finally:
        rig.close()


def test_unparsable_output_is_a_readable_error_with_the_output(
    shim: Cloud, fake: FakeClaude, tmp_path: Path
) -> None:
    fake.use(str(scenario(tmp_path, 'cloud:\n  output: Something went sideways\n')))
    with pytest.raises(SessionError) as e:
        shim.spawn(b'{"prompt": "Fix it"}')
    assert (e.value.status, e.value.code) == (422, 'cloud_unparsed')
    assert e.value.message == (
        'Claude did not print a cloud session id and a claude.ai link. '
        'Claude said: Something went sideways'
    )
    assert e.value.detail == 'Something went sideways'


def test_runner_routes_errors_and_methods(tmp_path: Path) -> None:
    rig = CloudRig(tmp_path)
    try:
        rig.start()
        assert rig.call('GET', '/cloud/sessions')[0] == 405
        assert rig.call('GET', f'/cloud/sessions/{RECORDED_ID}/messages')[0] == 405
        assert rig.call('POST', '/cloud/elsewhere', {})[0] == 404
        code, body = rig.call('POST', '/cloud/sessions', {'prompt': 'x', 'repo': 'y'})
        assert (code, body['code']) == (400, 'bad_request')
        code, body = rig.call('POST', f'/cloud/sessions/{RECORDED_ID}/messages', {'text': 'hi'})
        assert (code, body['code']) == (422, 'cloud_send_failed')  # the fake never created it
    finally:
        rig.close()


# --- untrusted text and ids -----------------------------------------------------------------


HOSTILE = [
    '--bg --name evil',
    'session_0168oBNCs8Q2BrHDFu6tYUdh',
    '"; touch pwned; echo "',
    '$(touch pwned) `touch pwned`',
    '-p /usage',
]


@pytest.mark.parametrize('hostile', HOSTILE)
def test_prompt_and_text_are_one_argument_after_the_double_dash(
    tmp_path: Path, hostile: str
) -> None:
    rig = CloudRig(tmp_path)
    try:
        rig.start()
        code, spawned = rig.call('POST', '/cloud/sessions', {'prompt': hostile})
        assert code == 201, spawned
        code, _ = rig.call(
            'POST', f'/cloud/sessions/{spawned["session_id"]}/messages', {'text': hostile}
        )
        assert code == 202
        for call in rig.calls_with('--cloud'):
            assert call['argv'][-2:] == ['--', hostile]
        record = json.loads(rig.fake.store.cloud_file(spawned['session_id']).read_text())
        assert (record['prompt'], record['messages']) == (hostile, [hostile])
        assert not list(tmp_path.rglob('pwned'))
    finally:
        rig.close()


@pytest.mark.parametrize(
    'bad',
    [
        'session_abc%3Btouch%20pwned',
        'session_',
        '--bg',
        'session_abc-def',
        'SESSION_abc',
        'session_abc%0A',
        '..',
    ],
)
def test_a_bad_session_id_never_reaches_claude(tmp_path: Path, bad: str) -> None:
    rig = CloudRig(tmp_path)
    try:
        rig.start()
        code, body = rig.call('POST', f'/cloud/sessions/{bad}/messages', {'text': 'hi'})
        assert (code, body['code']) == (400, 'bad_request')
        assert rig.calls_with('--cloud') == []
    finally:
        rig.close()


@pytest.mark.parametrize(
    'raw',
    [
        b'',
        b'not json',
        b'[]',
        b'{}',
        b'{"prompt": 7}',
        b'{"prompt": "  "}',
        b'{"prompt":"a","x":1}',
    ],
)
def test_spawn_body_must_match_the_contract(shim: Cloud, raw: bytes) -> None:
    with pytest.raises(SessionError) as e:
        shim.spawn(raw)
    assert (e.value.status, e.value.code) == (400, 'bad_request')


def test_send_body_must_match_the_contract(shim: Cloud) -> None:
    with pytest.raises(SessionError) as e:
        shim.send(RECORDED_ID, b'{"prompt": "hi"}')
    assert (e.value.status, e.value.code) == (400, 'bad_request')


# --- the link -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    'url',
    [
        f'http://claude.ai/code/{RECORDED_ID}',
        f'https://claude.ai.evil.example/code/{RECORDED_ID}',
        f'https://evil.example/code/{RECORDED_ID}',
        f'https://claude.ai@evil.example/code/{RECORDED_ID}',
        f'https://me@claude.ai/code/{RECORDED_ID}',
        f'https://claude.ai:8443/code/{RECORDED_ID}',
        f'https://claude.ai/code/{RECORDED_ID}/../../elsewhere',
        'https://claude.ai/code/session_other',
        'https://claude.ai:bad/code/x',
        f'javascript:alert(1)//claude.ai/code/{RECORDED_ID}',
        None,
    ],
)
def test_only_a_claude_ai_code_link_for_that_session_is_accepted(url: Any) -> None:
    assert page_url(url, RECORDED_ID) is None


def test_the_recorded_link_is_accepted_without_its_query() -> None:
    recorded = f'https://claude.ai/code/{RECORDED_ID}?from=cli&m=0'
    assert page_url(recorded, RECORDED_ID) == f'https://claude.ai/code/{RECORDED_ID}'
    assert page_url(f'https://CLAUDE.AI/code/{RECORDED_ID}', RECORDED_ID) is not None


def test_spawn_refuses_a_link_to_anywhere_else(
    shim: Cloud, fake: FakeClaude, tmp_path: Path
) -> None:
    line = (
        f'Created cloud session: Fix it View: https://claude.ai.evil.example/code/{RECORDED_ID} '
        f'Resume with: claude --teleport {RECORDED_ID}'
    )
    fake.use(str(scenario(tmp_path, f'cloud:\n  output: "{line}"\n')))
    with pytest.raises(SessionError) as e:
        shim.spawn(b'{"prompt": "Fix it"}')
    assert e.value.code == 'cloud_unparsed'
    assert 'claude.ai.evil.example' in e.value.message  # the output is shown, not used


def test_send_refuses_an_ack_with_a_foreign_link(tmp_path: Path) -> None:
    ack = {'ok': True, 'session_id': RECORDED_ID, 'url': 'https://evil.example/x'}
    claude = script(tmp_path, f'print({json.dumps(json.dumps(ack))})\n')
    with pytest.raises(SessionError) as e:
        Cloud(claude, tmp_path).send(RECORDED_ID, b'{"text": "hi"}')
    assert e.value.code == 'cloud_send_failed'


def test_send_refuses_an_ack_for_another_session(tmp_path: Path) -> None:
    other = 'session_0ther'
    ack = {'ok': True, 'session_id': other, 'url': f'https://claude.ai/code/{RECORDED_ID}'}
    claude = script(tmp_path, f'print({json.dumps(json.dumps(ack))})\n')
    with pytest.raises(SessionError) as e:
        Cloud(claude, tmp_path).send(RECORDED_ID, b'{"text": "hi"}')
    assert e.value.code == 'cloud_send_failed'
    assert e.value.message.startswith('Claude acknowledged a different session')


# --- send failures --------------------------------------------------------------------------


def test_send_reports_a_refusal_in_plain_words(tmp_path: Path) -> None:
    ack = {'ok': False, 'session_id': RECORDED_ID, 'url': f'https://claude.ai/code/{RECORDED_ID}'}
    claude = script(
        tmp_path, f'print("warning: slow network")\nprint({json.dumps(json.dumps(ack))})\n'
    )
    with pytest.raises(SessionError) as e:
        Cloud(claude, tmp_path).send(RECORDED_ID, b'{"text": "hi"}')
    assert e.value.message.startswith('claude.ai refused the message. Claude said: warning')


def test_send_reports_a_cli_failure_with_what_it_said(tmp_path: Path) -> None:
    claude = script(
        tmp_path, 'print("Error: not logged in", file=sys.stderr)\nprint("{oops")\nsys.exit(1)\n'
    )
    with pytest.raises(SessionError) as e:
        Cloud(claude, tmp_path).send(RECORDED_ID, b'{"text": "hi"}')
    assert (e.value.status, e.value.code) == (422, 'cloud_send_failed')
    assert e.value.message.startswith('Claude could not send the message. Claude said: ')
    assert 'Error: not logged in' in e.value.message


def test_send_times_out_with_a_plain_error(tmp_path: Path) -> None:
    claude = script(tmp_path, 'print("half", flush=True)\ntime.sleep(30)\n')
    started = time.monotonic()
    with pytest.raises(SessionError) as e:
        Cloud(claude, tmp_path, send_timeout=0.5).send(RECORDED_ID, b'{"text": "hi"}')
    assert time.monotonic() - started < 10
    assert e.value.code == 'cloud_send_failed'
    assert e.value.message.startswith('Claude did not pass the message on within 0.5 s.')
    (pid,) = pids(tmp_path)
    assert gone(pid)


def test_a_missing_claude_is_a_plain_error(tmp_path: Path) -> None:
    cloud = Cloud(tmp_path / 'nope' / 'claude', tmp_path)
    with pytest.raises(SessionError) as e:
        cloud.spawn(b'{"prompt": "x"}')
    assert (e.value.code, e.value.message.split(':')[0]) == (
        'cloud_unparsed',
        f'The runner could not run {tmp_path / "nope" / "claude"}',
    )
    with pytest.raises(SessionError) as e:
        cloud.send(RECORDED_ID, b'{"text": "x"}')
    assert e.value.code == 'cloud_send_failed'


# --- the pty: always reaped, nothing leaked -------------------------------------------------


def test_spawn_reaps_the_child_and_leaks_no_fd(tmp_path: Path) -> None:
    line = f'Created cloud session: T View: https://claude.ai/code/{RECORDED_ID} Resume with: x'
    claude = script(tmp_path, f'print({line!r})\n')
    cloud = Cloud(claude, tmp_path)
    cloud.spawn(b'{"prompt": "warm up"}')  # first call may open lazy module fds
    before = open_fds()
    for _ in range(5):
        assert cloud.spawn(b'{"prompt": "x"}')['session_id'] == RECORDED_ID
    assert open_fds() == before
    assert all(gone(pid) for pid in pids(tmp_path))


def test_spawn_timeout_kills_and_reaps_the_whole_group(tmp_path: Path) -> None:
    # The child starts a grandchild that holds the pty open, then both hang.
    claude = script(
        tmp_path,
        'print("Connecting to claude.ai…", flush=True)\n'
        'import subprocess\n'
        'g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
        'open(pidfile, "a").write(f"{g.pid}\\n")\n'
        'time.sleep(60)\n',
    )
    cloud = Cloud(claude, tmp_path, spawn_timeout=3)
    before = open_fds()
    started = time.monotonic()
    with pytest.raises(SessionError) as e:
        cloud.spawn(b'{"prompt": "x"}')
    assert time.monotonic() - started < 10
    assert (e.value.status, e.value.code) == (422, 'cloud_unparsed')
    assert e.value.message == (
        'Claude did not create the cloud session within 3 s. '
        'It may still appear at claude.ai/code: look there before trying again. '
        'Claude said: Connecting to claude.ai…'
    )
    assert open_fds() == before
    child, grandchild = pids(tmp_path)
    assert gone(child)  # killed and reaped by the runner
    deadline = time.monotonic() + 5  # the grandchild is init's to reap once killed
    while not gone(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert gone(grandchild)


def test_spawn_with_a_nonzero_exit_and_no_id(tmp_path: Path) -> None:
    claude = script(
        tmp_path, 'print("Error: --cloud requires an interactive terminal.")\nsys.exit(1)\n'
    )
    with pytest.raises(SessionError) as e:
        Cloud(claude, tmp_path).spawn(b'{"prompt": "x"}')
    assert e.value.detail == 'Error: --cloud requires an interactive terminal.'
    assert all(gone(pid) for pid in pids(tmp_path))


def test_run_on_pty_cleans_up_when_popen_fails(tmp_path: Path) -> None:
    before = open_fds()
    with pytest.raises(OSError):
        run_on_pty([str(tmp_path / 'missing')], dict(os.environ), tmp_path, 1.0)
    assert open_fds() == before


def test_run_on_pty_stops_reading_at_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cloud_mod, 'MAX_READ', 1000)
    claude = script(tmp_path, 'sys.stdout.write("x" * 50000)\n')
    code, out = run_on_pty([str(claude)], dict(os.environ), tmp_path, 10.0)
    assert code == 0
    assert len(out) == 1000


# --- what an error may show -----------------------------------------------------------------


def test_errors_strip_escapes_and_are_bounded(tmp_path: Path) -> None:
    claude = script(
        tmp_path,
        'sys.stdout.write("\\x1b[1;31mFailed\\x1b[0m \\x1b]0;title\\x07"'
        ' "\\x0f\\x1b7\\x1b8" + "y" * 9000)\n',
    )
    with pytest.raises(SessionError) as e:
        Cloud(claude, tmp_path).spawn(b'{"prompt": "x"}')
    detail = e.value.detail or ''
    assert detail.startswith('Failed y')
    assert len(detail) == MAX_OUTPUT
    assert not re.search(r'[\x00-\x08\x0b-\x1f\x7f]', e.value.message)


def test_errors_never_repeat_the_prompts_token(tmp_path: Path) -> None:
    # A CLI that echoes what it was given (as an error might) must not leak the task's token.
    claude = script(tmp_path, 'print("Could not create a session for:", args[-1])\nsys.exit(1)\n')
    cloud = Cloud(claude, tmp_path)
    with pytest.raises(SessionError) as e:
        cloud.spawn(json.dumps({'prompt': PROMPT}).encode())
    assert TOKEN not in e.value.message and TOKEN not in (e.value.detail or '')
    assert '[redacted]' in e.value.message
    with pytest.raises(SessionError) as e:
        cloud.send(RECORDED_ID, json.dumps({'text': f'Use {TOKEN} now'}).encode())
    assert TOKEN not in e.value.message and TOKEN not in (e.value.detail or '')


def test_clean_keeps_ordinary_words() -> None:
    assert clean('\r\nhello\r\n world \x1b[2K', ('averyveryverylongsecret',)) == 'hello\n world'


# --- a runner with cloud wired in -----------------------------------------------------------


class CloudRig(Rig):
    """test_sessions' Rig (the logging wrapper around the fake), with `Cloud` in the server."""

    def start(self, channel_wait: float = 5.0, spawn_timeout: float = 60.0) -> Sessions:
        self.sessions = Sessions(
            self.settings,
            self.state,
            home=self.fake.home,
            cgroup_of=apart,
            channel_wait=channel_wait,
            spawn_timeout=spawn_timeout,
        )
        self.srv = serve(
            self.sock,
            self.settings,
            cgroup_of=apart,
            usage=UsageProbe(self.claude, env=self.fake.env()),
            sessions=self.sessions,
            cloud=Cloud(self.claude, self.fake.home),
        )
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self.sessions
