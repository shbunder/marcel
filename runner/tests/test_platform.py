"""The two systems' supervisor checks (S-22.3): Linux compares cgroups, macOS compares process
groups from a fake `ps`. Nothing here needs the system it tests: both run on both."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import RUNNER_PGID, fake_ps, mac, write_script

from marcel_runner import platform
from marcel_runner.platform import MAC_REFUSAL, Linux, MacOS, Proc, current, parse_ps

# --- macOS -----------------------------------------------------------------------------------


def test_a_supervisor_in_its_own_group_outlives_the_runner(tmp_path: Path) -> None:
    # SP11, 2026-10-24: the on-demand supervisor leads its own group (pgid = its pid, ppid 1).
    assert mac(tmp_path, supervisor_pgid=1).own_service_check(1) is None


def test_a_supervisor_in_the_runners_group_is_refused_in_mac_words(tmp_path: Path) -> None:
    # `launchctl kickstart -k` kills the runner job's whole group: this one would go with it.
    problem = mac(tmp_path, supervisor_pgid=RUNNER_PGID).own_service_check(1)
    assert problem == MAC_REFUSAL
    assert 'marcel start runner' in problem
    assert 'systemctl' not in problem and '.service' not in problem


def test_ps_is_asked_for_the_runner_and_the_supervisor_only(tmp_path: Path) -> None:
    mac(tmp_path).own_service_check(1)
    calls = [json.loads(line) for line in (tmp_path / 'ps.calls').read_text().splitlines()]
    assert calls == [['-o', 'pid=,ppid=,pgid=', '-p', f'{os.getpid()},1']]


def test_a_supervisor_ps_does_not_show_is_cannot_tell(tmp_path: Path) -> None:
    problem = mac(tmp_path).own_service_check(77)
    assert problem is not None
    assert 'could not tell' in problem and 'process 77' in problem
    assert 'stop' not in problem.lower()


def test_a_runner_ps_does_not_show_is_cannot_tell(tmp_path: Path) -> None:
    problem = mac(tmp_path, runner=False).own_service_check(1)
    assert problem is not None
    assert 'could not tell' in problem and 'the runner itself' in problem


def test_a_missing_ps_is_cannot_tell(tmp_path: Path) -> None:
    problem = MacOS(ps=tmp_path / 'no-ps').own_service_check(1)
    assert problem is not None
    assert 'could not tell' in problem and 'could not run' in problem


def test_a_ps_that_hangs_is_cannot_tell(tmp_path: Path) -> None:
    slow = write_script(tmp_path / 'ps', 'exec sleep 10\n')
    problem = MacOS(ps=slow, timeout=0.3).own_service_check(1)
    assert problem is not None
    assert 'did not answer' in problem


def test_the_real_ps_reads_this_process(tmp_path: Path) -> None:
    """The real `/bin/ps` (on a Mac; skipped where there is none): this test process and its
    parent are in one group unless the parent leads its own, and the format parses."""
    if not Path('/bin/ps').exists():
        pytest.skip('no /bin/ps here')
    here = os.getpid()
    p = subprocess.run(
        ['/bin/ps', '-o', 'pid=,ppid=,pgid=', '-p', str(here)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert parse_ps(p.stdout)[here] == Proc(here, os.getppid(), os.getpgid(0))
    # Same group as itself: the check would refuse a supervisor that is this process.
    assert MacOS().own_service_check(here) == MAC_REFUSAL


def test_parse_ps_skips_what_is_not_a_row() -> None:
    text = '  PID  PPID  PGID\n    1     0     1\nps: bad\n  12    1   12  extra\n  99 1 42\n'
    assert parse_ps(text) == {1: Proc(1, 0, 1), 99: Proc(99, 1, 42)}


def test_no_supervisor_is_fine_on_a_mac_and_not_on_linux() -> None:
    assert MacOS().no_supervisor() is None
    linux = Linux().no_supervisor()
    assert linux is not None and 'marcel-claude-daemon.service' in linux


def test_the_mac_socket_is_under_marcel_home(tmp_path: Path) -> None:
    assert (
        MacOS().default_socket({'MARCEL_HOME': str(tmp_path)}) == tmp_path / 'run' / 'runner.sock'
    )
    assert MacOS().default_socket({}) == Path.home() / '.marcel' / 'run' / 'runner.sock'
    # XDG_RUNTIME_DIR is a Linux idea: a Mac ignores it.
    assert MacOS().default_socket({'XDG_RUNTIME_DIR': '/run/user/1'}) == (
        Path.home() / '.marcel' / 'run' / 'runner.sock'
    )


# --- Linux -----------------------------------------------------------------------------------


def test_linux_compares_cgroups() -> None:
    def cg(pid: int) -> str:
        return 'runner.service' if pid == os.getpid() else 'marcel-claude-daemon.service'

    assert Linux(cg).own_service_check(1) is None
    same = Linux(lambda pid: 'one').own_service_check(1)
    assert same is not None and 'inside the runner' in same


def test_linux_socket_is_under_the_runtime_dir() -> None:
    assert Linux().default_socket({'XDG_RUNTIME_DIR': '/run/user/7'}) == Path(
        '/run/user/7/marcel/runner.sock'
    )
    assert Linux().default_socket({}) == Path(f'/run/user/{os.getuid()}/marcel/runner.sock')


def test_linux_reads_proc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[str] = []

    def read_text(self: Path) -> str:
        seen.append(str(self))
        return '0::/x\n'

    monkeypatch.setattr(Path, 'read_text', read_text)
    assert platform.cgroup_of(42) == '0::/x\n'
    assert seen == ['/proc/42/cgroup']


# --- which system -----------------------------------------------------------------------------


@pytest.mark.parametrize(('name', 'kind'), [('darwin', MacOS), ('linux', Linux)])
def test_current_picks_by_the_system(
    monkeypatch: pytest.MonkeyPatch, name: str, kind: type
) -> None:
    monkeypatch.setattr(sys, 'platform', name)
    assert isinstance(current(), kind)


def test_fake_ps_is_what_the_mac_tests_use(tmp_path: Path) -> None:
    # The fake answers like the real one: the rows of the pids it knows, in pid order of asking.
    ps = fake_ps(tmp_path, supervisor_pgid=9)
    out = subprocess.run([str(ps), '-o', 'pid=,ppid=,pgid=', '-p', '1,5'], capture_output=True)
    assert parse_ps(out.stdout.decode()) == {1: Proc(1, 1, 9)}
