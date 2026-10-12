"""`GET /usage` over the runner's socket, read from the fake claude's recorded /usage output."""

import os
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import apart, request
from fake_claude import FakeClaude

from marcel_runner.health import Settings
from marcel_runner.server import UnixHTTPServer, serve
from marcel_runner.usage import UsageProbe


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
