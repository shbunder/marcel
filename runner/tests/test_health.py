import os
import subprocess
from pathlib import Path

import pytest
from conftest import apart, write_script

from marcel_runner import __version__
from marcel_runner.health import Settings, collect_health, parse_pid


def settings(claude: Path, **kw) -> Settings:
    return Settings(claude=claude, cap=4, **kw)


def test_healthy_runner_reports_every_field(fake_claude: Path) -> None:
    h = collect_health(settings(fake_claude), active_workers=lambda: 1, cgroup_of=apart)
    assert h == {
        'ok': True,
        'version': __version__,
        'claude_version': '2.1.289 (Claude Code)',
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
        'if [ "$1" = "--version" ]; then echo "2.1.289 (Claude Code)"; exit 0; fi\n'
        'echo "not running"; exit 1\n',
    )
    h = collect_health(settings(c), active_workers=lambda: 0, cgroup_of=apart)
    assert h['ok'] is False
    assert h['claude_version'] == '2.1.289 (Claude Code)'
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
        'if [ "$1" = "--version" ]; then echo "2.1.289 (Claude Code)"; exit 0; fi\n' + status_body,
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
    c = daemon(tmp_path, 'sleep 5\n')
    h = collect_health(settings(c, timeout=0.3), active_workers=lambda: 0, cgroup_of=apart)
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
    recorded = Path(__file__).parent / 'fixtures' / 'daemon-status-not-running.txt'
    c = daemon(tmp_path, f'cat {recorded}; exit 1\n')
    h = collect_health(settings(c), active_workers=lambda: 0, cgroup_of=apart)
    assert h['ok'] is False
    assert h['supervisor'] == {'running': False, 'own_unit': False}
    assert 'no background supervisor' in h['reason']
