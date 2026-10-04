import os
import socket
import stat
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import apart, request, write_script

from marcel_runner import server
from marcel_runner.health import Settings
from marcel_runner.server import StartupError, bind_socket, serve


@pytest.fixture
def sock_path(tmp_path: Path) -> Path:
    # unix socket paths are limited to ~100 bytes; pytest's tmp_path can be long
    d = Path(os.environ.get('TMPDIR', '/tmp')) / f'mr-{os.getpid()}-{tmp_path.name[-8:]}'
    d.mkdir()
    yield d / 'runner.sock'
    for p in d.iterdir():
        p.unlink()
    d.rmdir()


@pytest.fixture
def running(sock_path: Path, fake_claude: Path) -> Iterator[Path]:
    cfg = Settings(claude=fake_claude, cap=4)
    srv = serve(sock_path, cfg, cgroup_of=apart, active_workers=lambda: 0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield sock_path
    srv.shutdown()
    srv.server_close()


def test_server_answers_health_on_a_temp_socket(running: Path) -> None:
    status, body = request(running, 'GET', '/health')
    assert status == 200
    assert body['ok'] is True
    assert body['claude_version'] == '2.1.289 (Claude Code)'
    assert body['cap'] == 4
    assert body['active_workers'] == 0


def test_health_is_200_even_when_claude_is_missing(sock_path: Path, tmp_path: Path) -> None:
    cfg = Settings(claude=tmp_path / 'absent', cap=4)
    srv = serve(sock_path, cfg, cgroup_of=apart, active_workers=lambda: 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, body = request(sock_path, 'GET', '/health')
    finally:
        srv.shutdown()
        srv.server_close()
    assert status == 200
    assert body['ok'] is False
    assert 'absent' in body['reason']
    assert body['claude_version'] == ''


def test_unknown_path_is_an_error_a_person_can_read(running: Path) -> None:
    status, body = request(running, 'GET', '/nope')
    assert status == 404
    assert set(body) <= {'code', 'message', 'detail'}
    assert body['code'] == 'not_found'
    assert '/nope' in body['message']


def test_wrong_method_on_health_is_405_not_an_html_page(running: Path) -> None:
    status, body = request(running, 'POST', '/health')
    assert status == 405
    assert body['code'] == 'method_not_allowed'


def test_query_string_does_not_hide_health(running: Path) -> None:
    status, _ = request(running, 'GET', '/health?x=1')
    assert status == 200


def test_socket_mode_is_0660_whatever_the_umask(sock_path: Path) -> None:
    old = os.umask(0)
    try:
        s = bind_socket(sock_path)
    finally:
        os.umask(old)
    try:
        assert stat.S_IMODE(sock_path.stat().st_mode) == 0o660
    finally:
        s.close()


def test_socket_that_ends_up_with_the_wrong_mode_refuses_to_start(
    sock_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = os.chmod
    monkeypatch.setattr(os, 'chmod', lambda path, mode: real(path, 0o666))
    old = os.umask(0)
    try:
        with pytest.raises(StartupError, match='0660'):
            bind_socket(sock_path)
    finally:
        os.umask(old)
    assert not sock_path.exists()


def test_stale_socket_file_is_replaced(sock_path: Path) -> None:
    dead = socket.socket(socket.AF_UNIX)
    dead.bind(str(sock_path))
    dead.close()  # leaves the file behind, nobody listening
    assert sock_path.exists()
    s = bind_socket(sock_path)
    s.close()


def test_socket_in_use_by_a_live_runner_refuses_to_start(sock_path: Path) -> None:
    live = bind_socket(sock_path)
    live.listen()
    try:
        with pytest.raises(StartupError, match='already running'):
            bind_socket(sock_path)
        # and the live one was not unlinked
        assert sock_path.exists()
    finally:
        live.close()


def test_a_regular_file_at_the_socket_path_is_never_deleted(sock_path: Path) -> None:
    sock_path.write_text('precious')
    with pytest.raises(StartupError, match='not a socket'):
        bind_socket(sock_path)
    assert sock_path.read_text() == 'precious'


def test_missing_socket_directory_is_created_private(tmp_path: Path) -> None:
    p = Path(os.environ.get('TMPDIR', '/tmp')) / f'mr-new-{os.getpid()}' / 'marcel' / 'runner.sock'
    try:
        s = bind_socket(p)
        s.close()
        assert stat.S_IMODE(p.parent.stat().st_mode) == 0o750
    finally:
        p.unlink(missing_ok=True)
        p.parent.rmdir()
        p.parent.parent.rmdir()


def test_unknown_group_refuses_to_start(sock_path: Path) -> None:
    with pytest.raises(StartupError, match='no-such-group'):
        bind_socket(sock_path, group='no-such-group')
    assert not sock_path.exists()


def test_known_group_is_applied(sock_path: Path) -> None:
    import grp

    name = grp.getgrgid(os.getgid()).gr_name
    s = bind_socket(sock_path, group=name)
    s.close()
    assert sock_path.stat().st_gid == os.getgid()


def test_start_is_refused_without_a_supervisor_outside_our_cgroup(
    sock_path: Path, fake_claude: Path
) -> None:
    cfg = Settings(claude=fake_claude, cap=4)
    with pytest.raises(StartupError, match='marcel-claude-daemon.service'):
        serve(sock_path, cfg, cgroup_of=lambda pid: 'same', active_workers=lambda: 0)
    assert not sock_path.exists()  # refused before binding


def test_start_is_refused_when_no_supervisor_runs(sock_path: Path, tmp_path: Path) -> None:
    c = write_script(
        tmp_path / 'c2',
        'if [ "$1" = "--version" ]; then echo "2.1.289 (Claude Code)"; exit 0; fi\n'
        'echo "not running"\n',
    )
    with pytest.raises(StartupError, match='marcel-claude-daemon.service'):
        serve(sock_path, Settings(claude=c, cap=4), cgroup_of=apart, active_workers=lambda: 0)


def test_missing_claude_still_starts_so_health_can_say_why(sock_path: Path, tmp_path: Path) -> None:
    srv = serve(
        sock_path,
        Settings(claude=tmp_path / 'absent', cap=4),
        cgroup_of=apart,
        active_workers=lambda: 0,
    )
    srv.server_close()


def test_main_prints_the_reason_and_exits_1_when_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / 'file'
    f.write_text('x')
    monkeypatch.setenv('MARCEL_RUNNER_SOCK', str(f))
    monkeypatch.setenv('MARCEL_CLAUDE', str(tmp_path / 'absent'))
    assert server.main() == 1
    assert 'not a socket' in capsys.readouterr().err


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('MARCEL_CLAUDE', '/opt/claude')
    monkeypatch.setenv('MARCEL_WORKER_CAP', '7')
    monkeypatch.setenv('MARCEL_RUNNER_SOCK', str(tmp_path / 's.sock'))
    monkeypatch.setenv('MARCEL_RUNNER_GROUP', 'marcel')
    cfg, sock, group = server.settings_from_env()
    assert (cfg.claude, cfg.cap, sock, group) == (
        Path('/opt/claude'),
        7,
        tmp_path / 's.sock',
        'marcel',
    )


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ('MARCEL_CLAUDE', 'MARCEL_WORKER_CAP', 'MARCEL_RUNNER_SOCK', 'MARCEL_RUNNER_GROUP'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('XDG_RUNTIME_DIR', '/run/user/1234')
    cfg, sock, group = server.settings_from_env()
    assert cfg.claude == Path.home() / '.local' / 'bin' / 'claude'
    assert cfg.cap == 4
    assert sock == Path('/run/user/1234/marcel/runner.sock')
    assert group is None


def test_bad_worker_cap_is_refused_in_words(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('MARCEL_WORKER_CAP', 'lots')
    with pytest.raises(StartupError, match='MARCEL_WORKER_CAP'):
        server.settings_from_env()
