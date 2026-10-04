from pathlib import Path

import pytest
from conftest import apart, write_script

from marcel_runner import __version__
from marcel_runner.health import Settings, collect_health, parse_supervisor


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
        'echo "not running"; exit 0\n',
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
        ('running\n  pid: 42\n  origin: foreground\n', (True, 42)),
        ('running (pid 42, origin: foreground)\n', (True, 42)),
        ('not running\n\nbg sessions:\n  roster.json: absent\n', (False, None)),
        ('', (False, None)),
        ('running\n', (True, None)),
    ],
)
def test_parse_supervisor(text: str, expected: tuple[bool, int | None]) -> None:
    assert parse_supervisor(text) == expected
