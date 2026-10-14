"""`GET /usage` over the runner's socket, read from the fake claude's recorded /usage output."""

import os
import shutil
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import apart, request, write_script
from fake_claude import FakeClaude

from marcel_runner.health import Settings
from marcel_runner.login import LOOK_EVERY, LoginCheck
from marcel_runner.server import UnixHTTPServer, serve
from marcel_runner.usage import UsageProbe

FIXTURES = Path(__file__).parent / 'test_usage_fixtures'


@pytest.fixture
def sock_path(tmp_path: Path) -> Iterator[Path]:
    # unix socket paths are limited to ~100 bytes; pytest's tmp_path can be long
    d = Path(os.environ.get('TMPDIR', '/tmp')) / f'mu-{os.getpid()}-{tmp_path.name[-8:]}'
    d.mkdir()
    yield d / 'runner.sock'
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def fake(tmp_path: Path) -> FakeClaude:
    return FakeClaude(tmp_path / 'home')


def start(sock: Path, supervisor_claude: Path, probe: UsageProbe) -> UnixHTTPServer:
    srv = serve(sock, Settings(claude=supervisor_claude, cap=4), cgroup_of=apart, usage=probe)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_get_usage_answers_the_reading_from_the_fake_claude(
    sock_path: Path, fake_claude: Path, fake: FakeClaude
) -> None:
    probe = UsageProbe(fake.claude, env=fake.env())
    srv = start(sock_path, fake_claude, probe)
    try:
        probe.refresh()
        status, body = request(sock_path, 'GET', '/usage')
    finally:
        srv.shutdown()
        srv.server_close()
    assert status == 200, body
    assert body['source'] == 'cli_usage'
    assert body['stale'] is False
    assert body['five_hour']['used_pct'] == 4
    assert body['weekly']['used_pct'] == 66
    assert body['five_hour']['resets_at'].endswith('T14:29:00Z')


def test_get_usage_is_503_no_reading_before_the_first_reading(
    sock_path: Path, fake_claude: Path, fake: FakeClaude
) -> None:
    srv = start(sock_path, fake_claude, UsageProbe(fake.claude, env=fake.env()))
    try:
        status, body = request(sock_path, 'GET', '/usage')
    finally:
        srv.shutdown()
        srv.server_close()
    assert status == 503
    assert body['code'] == 'no_reading'


def test_get_usage_is_stale_not_an_error_when_the_source_goes_away(
    sock_path: Path, fake_claude: Path, fake: FakeClaude
) -> None:
    probe = UsageProbe(fake.claude, env=fake.env())
    srv = start(sock_path, fake_claude, probe)
    try:
        probe.refresh()
        probe.claude = fake.home / 'gone' / 'claude'  # claude is no longer there
        probe.refresh()
        status, body = request(sock_path, 'GET', '/usage')
    finally:
        srv.shutdown()
        srv.server_close()
    assert status == 200
    assert body['stale'] is True
    assert body['weekly']['used_pct'] == 66


def test_post_usage_is_405(sock_path: Path, fake_claude: Path, fake: FakeClaude) -> None:
    srv = start(sock_path, fake_claude, UsageProbe(fake.claude, env=fake.env()))
    try:
        status, body = request(sock_path, 'POST', '/usage')
    finally:
        srv.shutdown()
        srv.server_close()
    assert status == 405
    assert '/usage' in body['message']


def test_serve_builds_its_own_probe_on_the_configured_claude(
    sock_path: Path, fake_claude: Path
) -> None:
    srv = serve(sock_path, Settings(claude=fake_claude, cap=4), cgroup_of=apart)
    try:
        assert srv.usage.claude == fake_claude
    finally:
        srv.server_close()


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now


def test_logged_in_again_the_old_logged_out_reason_is_gone_before_login_says_ok(
    sock_path: Path, fake_claude: Path, tmp_path: Path
) -> None:
    """The hub says "logged in again" on `login` ok, then reads `/usage`: an old "logged out"
    reason there would tell the owner the opposite a minute later."""
    out = tmp_path / 'usage.txt'
    out.write_text((FIXTURES / 'usage-2.1.299-logged-out.txt').read_text('utf-8'))
    probe = UsageProbe(write_script(tmp_path / 'usage-claude', f'cat "{out}"\n'))
    clock = Clock()
    login = LoginCheck(fake_claude, tmp_path / 'state', tmp_path / 'daemon.log', clock=clock)
    srv = serve(
        sock_path, Settings(claude=fake_claude, cap=4), cgroup_of=apart, usage=probe, login=login
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def look() -> tuple[str, dict]:
        clock.now += timedelta(seconds=LOOK_EVERY)
        return request(sock_path, 'GET', '/health')[1]['login']['state'], request(
            sock_path, 'GET', '/usage'
        )[1]

    try:
        probe.refresh()  # the cost view: logged out
        assert look()[0] == 'lost'
        out.write_text((FIXTURES / 'usage-2.1.299-text.txt').read_text('utf-8'))  # logged in
        states = []
        for _ in range(4):
            state, reading = look()
            states.append(state)
            if state == 'ok':
                assert reading.get('stale') is False and 'reason' not in reading, reading
            deadline = time.monotonic() + 10  # the read the look asked for, on its own thread
            while request(sock_path, 'GET', '/usage')[0] != 200 and time.monotonic() < deadline:
                time.sleep(0.05)
    finally:
        srv.shutdown()
        srv.server_close()
    assert states[-1] == 'ok', states
