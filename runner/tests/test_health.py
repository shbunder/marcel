import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import (
    AUTH_LOGGED_OUT,
    CLAUDE_VERSION,
    CLAUDE_VERSION_LINE,
    DAEMON_STATUS_NOT_RUNNING,
    apart,
    auth_answer,
    request,
    runner_api,
    write_script,
)

from marcel_runner import __version__, health
from marcel_runner.health import Settings, parse_pid
from marcel_runner.login import LOST_MESSAGE, LoginCheck
from marcel_runner.server import serve

HEALTH = runner_api('Health')


def collect_health(*args, **kwargs) -> dict:
    """`health.collect_health`, every answer checked against runner-api.yaml `Health`."""
    answer = health.collect_health(*args, **kwargs)
    HEALTH.validate(answer)
    return answer


def settings(claude: Path, **kw) -> Settings:
    return Settings(claude=claude, cap=4, **kw)


def test_healthy_runner_reports_every_field(fake_claude: Path) -> None:
    h = collect_health(settings(fake_claude), active_workers=lambda: 1, cgroup_of=apart)
    assert h == {
        'ok': True,
        'version': __version__,
        'claude_version': CLAUDE_VERSION_LINE,
        'claude_version_expected': CLAUDE_VERSION,
        'supervisor': {'running': True, 'own_unit': True},
        'cap': 4,
        'active_workers': 1,
    }


def test_missing_claude_is_not_ok_with_a_reason_a_person_can_act_on(tmp_path: Path) -> None:
    missing = tmp_path / 'nope' / 'claude'
    h = collect_health(settings(missing), active_workers=lambda: 0, cgroup_of=apart)
    assert h['ok'] is False
    assert h['claude_version'] == ''
    assert h['supervisor'] == {'running': False, 'own_unit': False}
    assert str(missing) in h['reason']
    assert 'MARCEL_CLAUDE' in h['reason']


def test_claude_that_is_not_executable_is_not_ok(tmp_path: Path) -> None:
    plain = tmp_path / 'claude'
    plain.write_text('not a program')
    h = collect_health(settings(plain), active_workers=lambda: 0, cgroup_of=apart)
    assert h['ok'] is False
    assert 'cannot run' in h['reason']


def test_claude_that_hangs_is_not_ok(tmp_path: Path) -> None:
    slow = write_script(tmp_path / 'claude', 'sleep 5\n')
    h = collect_health(settings(slow, timeout=0.2), active_workers=lambda: 0, cgroup_of=apart)
    assert h['ok'] is False
    assert 'did not answer' in h['reason']


def test_claude_version_failure_is_not_ok(tmp_path: Path) -> None:
    broken = write_script(tmp_path / 'claude', 'echo boom >&2; exit 3\n')
    h = collect_health(settings(broken), active_workers=lambda: 0, cgroup_of=apart)
    assert h['ok'] is False
    assert h['claude_version'] == ''
    assert 'boom' in h['reason']


def test_no_supervisor_is_not_ok(tmp_path: Path) -> None:
    c = write_script(
        tmp_path / 'claude',
        f'if [ "$1" = "--version" ]; then echo "{CLAUDE_VERSION_LINE}"; exit 0; fi\n'
        'echo "not running"; exit 1\n',
    )
    h = collect_health(settings(c), active_workers=lambda: 0, cgroup_of=apart)
    assert h['ok'] is False
    assert h['claude_version'] == CLAUDE_VERSION_LINE
    assert h['supervisor'] == {'running': False, 'own_unit': False}
    assert 'marcel-claude-daemon.service' in h['reason']


def test_supervisor_in_the_runners_own_cgroup_is_not_ok(fake_claude: Path) -> None:
    h = collect_health(
        settings(fake_claude), active_workers=lambda: 0, cgroup_of=lambda pid: 'same-unit'
    )
    assert h['ok'] is False
    assert h['supervisor'] == {'running': True, 'own_unit': False}
    assert 'marcel-claude-daemon.service' in h['reason']
    assert 'restart' in h['reason']


def test_supervisor_whose_cgroup_cannot_be_read_is_not_own_unit(fake_claude: Path) -> None:
    def unreadable(pid: int) -> str:
        raise OSError('gone')

    h = collect_health(settings(fake_claude), active_workers=lambda: 0, cgroup_of=unreadable)
    assert h['supervisor'] == {'running': True, 'own_unit': False}
    assert h['ok'] is False


def test_the_cap_and_active_count_come_from_the_caller(fake_claude: Path) -> None:
    h = collect_health(
        Settings(claude=fake_claude, cap=2), active_workers=lambda: 2, cgroup_of=apart
    )
    assert (h['cap'], h['active_workers']) == (2, 2)


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('running\n  pid: 42\n  origin: foreground\n', 42),
        ('running (pid 42, origin: foreground)\n', 42),
        ('PID=42', 42),
        ('Pid:          42', 42),
        ('not running\n\nbg sessions:\n  roster.json: absent\n', None),
        ('pid:\n42', None),
        ('', None),
    ],
)
def test_parse_pid_is_lenient(text: str, expected: int | None) -> None:
    assert parse_pid(text) == expected


def daemon(tmp_path: Path, status_body: str) -> Path:
    return write_script(
        tmp_path / 'claude',
        f'if [ "$1" = "--version" ]; then echo "{CLAUDE_VERSION_LINE}"; exit 0; fi\n' + status_body,
    )


def test_exit_code_decides_running_not_the_text(tmp_path: Path) -> None:
    # exit 1 with text that looks healthy is still "not running"
    c = daemon(tmp_path, 'echo "running"; echo "pid: 1"; exit 1\n')
    h = collect_health(settings(c), active_workers=lambda: 0, cgroup_of=apart)
    assert h['supervisor'] == {'running': False, 'own_unit': False}
    assert h['ok'] is False
    assert 'no background supervisor' in h['reason']
    # exit 0 with no recognisable words is running
    c = daemon(tmp_path, 'echo "all good"; echo "pid: 1"; exit 0\n')
    h = collect_health(settings(c), active_workers=lambda: 0, cgroup_of=apart)
    assert h['ok'] is True


def test_no_pid_means_the_runner_cannot_tell_and_never_says_stop(tmp_path: Path) -> None:
    c = daemon(tmp_path, 'echo "running"; exit 0\n')
    h = collect_health(settings(c), active_workers=lambda: 0, cgroup_of=apart)
    assert h['ok'] is False
    assert h['supervisor'] == {'running': True, 'own_unit': False}
    assert 'could not tell' in h['reason']
    assert 'stop' not in h['reason'].lower()


def test_unreadable_supervisor_cgroup_means_cannot_tell_and_never_says_stop(
    fake_claude: Path,
) -> None:
    def cg(pid: int) -> str:
        if pid == os.getpid():
            return 'runner.service'
        raise OSError('gone')

    h = collect_health(settings(fake_claude), active_workers=lambda: 0, cgroup_of=cg)
    assert h['ok'] is False
    assert 'could not tell' in h['reason']
    assert 'stop' not in h['reason'].lower()


def test_unreadable_runner_cgroup_fails_closed(fake_claude: Path) -> None:
    def cg(pid: int) -> str:
        if pid == os.getpid():
            raise OSError('no /proc')
        return ''  # would equal a '' fallback for our own cgroup: must not be compared

    h = collect_health(settings(fake_claude), active_workers=lambda: 0, cgroup_of=cg)
    assert h['ok'] is False
    assert h['supervisor'] == {'running': True, 'own_unit': False}
    assert 'own cgroup' in h['reason']
    assert 'stop' not in h['reason'].lower()


def test_daemon_status_that_hangs_has_its_own_reason(tmp_path: Path) -> None:
    # The timeout covers `--version` too, which can take over 0.3 s on a cold start: keep it
    # well above that, and the hang well above the timeout.
    c = daemon(tmp_path, 'exec sleep 10\n')
    h = collect_health(settings(c, timeout=2), active_workers=lambda: 0, cgroup_of=apart)
    assert h['ok'] is False
    assert 'did not answer' in h['reason']
    assert 'daemon status' in h['reason']
    assert 'no background supervisor' not in h['reason']


def test_daemon_status_that_cannot_run_has_its_own_reason(
    fake_claude: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = subprocess.run

    def run(cmd, *a, **k):
        if cmd[1:] == ['daemon', 'status']:
            raise OSError(8, 'Exec format error')
        return real(cmd, *a, **k)

    monkeypatch.setattr(subprocess, 'run', run)
    h = collect_health(settings(fake_claude), active_workers=lambda: 0, cgroup_of=apart)
    assert h['ok'] is False
    assert 'could not run' in h['reason']
    assert 'no background supervisor' not in h['reason']


def test_daemon_status_failing_for_another_reason_is_cannot_tell(tmp_path: Path) -> None:
    c = daemon(tmp_path, 'echo "unknown command" >&2; exit 2\n')
    h = collect_health(settings(c), active_workers=lambda: 0, cgroup_of=apart)
    assert h['ok'] is False
    assert 'cannot tell' in h['reason']
    assert 'unknown command' in h['reason']


def test_recorded_not_running_output_gives_no_supervisor(tmp_path: Path) -> None:
    # Recorded on the NUC with Claude Code 2.1.289: exit code 1 and this text.
    recorded = tmp_path / 'daemon-status.txt'
    recorded.write_text(DAEMON_STATUS_NOT_RUNNING)
    c = daemon(tmp_path, f'cat {recorded}; exit 1\n')
    h = collect_health(settings(c), active_workers=lambda: 0, cgroup_of=apart)
    assert h['ok'] is False
    assert h['supervisor'] == {'running': False, 'own_unit': False}
    assert 'no background supervisor' in h['reason']


# --- the login (runner-api 0.1.13, S-03.13) -------------------------------------------------------


def login_check(claude: Path, tmp_path: Path) -> LoginCheck:
    return LoginCheck(claude, tmp_path / 'state', tmp_path / 'daemon.log')


def test_a_lost_login_is_reported_and_never_makes_the_runner_not_ok(
    fake_claude: Path, tmp_path: Path
) -> None:
    """A redeploy check reads `ok`: rolling back a good merge cannot log anyone in."""
    auth_answer(fake_claude, AUTH_LOGGED_OUT, code=1)
    h = collect_health(
        settings(fake_claude), lambda: 0, apart, login=login_check(fake_claude, tmp_path)
    )
    assert h['ok'] is True
    assert 'reason' not in h
    assert h['login']['state'] == 'lost'
    assert h['login']['message'] == LOST_MESSAGE


def test_a_lost_login_leaves_a_real_problems_reason_as_it_is(tmp_path: Path) -> None:
    c = write_script(
        tmp_path / 'claude',
        f'if [ "$1" = "--version" ]; then echo "{CLAUDE_VERSION_LINE}"; exit 0; fi\n'
        f'if [ "$1" = "auth" ]; then cat <<"X"\n{AUTH_LOGGED_OUT}X\nexit 1; fi\n'
        'echo "not running"; exit 1\n',
    )
    h = collect_health(settings(c), lambda: 0, apart, login=login_check(c, tmp_path))
    assert h['ok'] is False
    assert 'no background supervisor' in h['reason']
    assert 'logged out' not in h['reason']
    assert h['login']['state'] == 'lost'


def test_without_a_login_check_there_is_no_login_and_no_auth_status_run(
    fake_claude: Path, tmp_path: Path
) -> None:
    """What `sessions.py` calls before each start: a start never waits for `auth status`."""
    h = collect_health(settings(fake_claude), lambda: 0, apart)
    assert 'login' not in h
    assert not (tmp_path / 'auth-status.calls').exists()


@pytest.fixture
def sock_path(tmp_path: Path) -> Iterator[Path]:
    # unix socket paths are limited to ~100 bytes; pytest's tmp_path can be long
    d = Path(os.environ.get('TMPDIR', '/tmp')) / f'mh-{os.getpid()}-{tmp_path.name[-8:]}'
    d.mkdir()
    yield d / 'runner.sock'
    shutil.rmtree(d, ignore_errors=True)


def test_get_health_carries_the_login(sock_path: Path, fake_claude: Path, tmp_path: Path) -> None:
    srv = serve(
        sock_path,
        settings(fake_claude),
        cgroup_of=apart,
        login=login_check(fake_claude, tmp_path),
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, body = request(sock_path, 'GET', '/health')
        auth_answer(fake_claude, AUTH_LOGGED_OUT, code=1)
        _, cached = request(sock_path, 'GET', '/health')  # within 30 s: the last look's answer
    finally:
        srv.shutdown()
        srv.server_close()
    assert status == 200
    HEALTH.validate(body)
    assert body['ok'] is True
    assert body['login']['state'] == 'ok'
    assert cached['login'] == body['login']
