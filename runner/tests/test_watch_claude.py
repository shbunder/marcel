"""Claude Code changing under the runner (S-03.12, runner-api 0.1.12): `runner.claude_changed` and
`runner.supervisor_restarted`, from a stub roster and a fake probe. Found live on the NUC: another
`claude` updated the shared binary and the supervisor restarted onto it, ending five sessions
(project/lessons/runner-built-ins.md). Nothing here runs the real claude."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import write_script
from jsonschema import Draft202012Validator
from test_watch import SID, SID2, StubRoster, row

from marcel_runner.health import ClaudeProbe, Probe, Settings, last_shutdown
from marcel_runner.watch import Watcher

API = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / 'contracts' / 'runner-api.yaml').read_text()
)
EVENT = Draft202012Validator(
    {'$ref': '#/components/schemas/RunnerEvent', 'components': API['components']}
)
PINNED, NEWER = '2.1.296 (Claude Code)', '2.1.301 (Claude Code)'
LOG = """\
2026-10-14T03:10:01Z [supervisor] started version=2.1.296
2026-10-14T03:11:00Z shutting down (cause=idle, uptime=59s, leases=0, live_workers=0)
2026-10-14T03:12:00Z [supervisor] binary at /home/m/claude changed (.../2.1.296 \
→ .../2.1.301) — self-restarting
2026-10-14T03:12:00Z shutting down (cause=upgrade, uptime=61s, leases=0, live_workers=5)
2026-10-14T03:12:01Z ─── daemon start ─── version=2.1.301
"""


class Looks:
    """A probe that hands out the looks queued in `looks` (None: not due yet)."""

    def __init__(self) -> None:
        self.looks: list[Probe | None] = []

    def __call__(self) -> Probe | None:
        return self.looks.pop(0) if self.looks else None


@pytest.fixture
def roster(tmp_path: Path) -> StubRoster:
    return StubRoster(tmp_path)


@pytest.fixture
def looks() -> Looks:
    return Looks()


@pytest.fixture
def watcher(roster: StubRoster, looks: Looks) -> Watcher:
    return Watcher(roster, probe=looks, expected='2.1.296')


def ours(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    for event in events:
        EVENT.validate(event)
    return [e for e in events if e['type'] == kind]


def test_a_version_change_is_said_once(watcher: Watcher, looks: Looks) -> None:
    looks.looks = [Probe(PINNED, 1), Probe(NEWER, 1), Probe(NEWER, 1)]
    assert ours(watcher.poll(), 'runner.claude_changed') == [], 'the first look only records'
    [changed] = ours(watcher.poll(), 'runner.claude_changed')
    assert changed['claude_change'] == {'from': PINNED, 'to': NEWER, 'expected': '2.1.296'}
    assert changed['message'] == (
        'Claude Code changed from 2.1.296 to 2.1.301; Marcel is tested with 2.1.296.'
    )
    assert ours(watcher.poll(), 'runner.claude_changed') == [], 'the same version again'


def test_back_on_the_pin_says_so(watcher: Watcher, looks: Looks) -> None:
    looks.looks = [Probe(NEWER, 1), Probe(PINNED, 1)]
    watcher.poll()
    [back] = ours(watcher.poll(), 'runner.claude_changed')
    assert back['claude_change']['to'] == PINNED
    assert 'back to 2.1.296, the version Marcel is tested with' in back['message']


def test_a_look_that_is_not_due_or_cannot_tell_says_nothing(watcher: Watcher, looks: Looks) -> None:
    looks.looks = [Probe(PINNED, 1), None, Probe('', None), Probe(PINNED, 1)]
    for _ in range(4):
        assert ours(watcher.poll(), 'runner.claude_changed') == []
        assert ours(watcher.poll(), 'runner.supervisor_restarted') == []


def test_a_supervisor_restart_names_the_sessions_it_ended(
    watcher: Watcher, roster: StubRoster, looks: Looks, tmp_path: Path
) -> None:
    (tmp_path / '.claude').mkdir()
    (tmp_path / '.claude' / 'daemon.log').write_text(LOG)
    roster.rows = [row(SID, 'working'), row(SID2, 'blocked')]
    looks.looks = [Probe(PINNED, 100), Probe(NEWER, 200)]
    watcher.poll()
    roster.rows = [row(SID2, 'blocked')]  # SID is gone after the restart
    events = watcher.poll()
    [restarted] = ours(events, 'runner.supervisor_restarted')
    assert restarted['supervisor_restart'] == {
        'session_ids': [SID],
        'cause': 'upgrade',
        'live_workers': 5,
    }
    assert (
        restarted['message'] == "Claude's supervisor restarted (upgrade); 1 running session ended."
    )
    assert len(ours(events, 'runner.claude_changed')) == 1
    assert [e['session']['id'] for e in ours(events, 'session.gone')] == [SID], 'still said'


def test_without_the_log_the_restart_has_no_cause(
    watcher: Watcher, roster: StubRoster, looks: Looks
) -> None:
    roster.rows = [row(SID, 'working')]
    looks.looks = [Probe(PINNED, 100), Probe(PINNED, 200)]
    watcher.poll()
    roster.rows = [row(SID, 'done')]  # listed, but no longer running
    [restarted] = ours(watcher.poll(), 'runner.supervisor_restarted')
    assert restarted['supervisor_restart'] == {'session_ids': [SID]}
    assert restarted['message'] == "Claude's supervisor restarted; 1 running session ended."


def test_a_restart_that_ended_nothing(watcher: Watcher, looks: Looks) -> None:
    looks.looks = [Probe(PINNED, 1), Probe(PINNED, 2), Probe(PINNED, 2)]
    watcher.poll()
    [restarted] = ours(watcher.poll(), 'runner.supervisor_restarted')
    assert restarted['supervisor_restart'] == {'session_ids': []}
    assert 'no running session ended' in restarted['message']
    assert ours(watcher.poll(), 'runner.supervisor_restarted') == [], 'the same pid again'


def test_a_supervisor_that_is_down_for_a_look_is_no_restart(watcher: Watcher, looks: Looks) -> None:
    looks.looks = [Probe(PINNED, 1), Probe(PINNED, None), Probe(PINNED, 1)]
    for _ in range(3):
        assert ours(watcher.poll(), 'runner.supervisor_restarted') == []


def test_the_events_reach_the_stream_history(watcher: Watcher, looks: Looks) -> None:
    looks.looks = [Probe(PINNED, 1), Probe(NEWER, 2)]
    watcher.poll()
    watcher.poll()
    missed, _ = watcher.feed.connect(str(watcher.feed.head - 2))
    kinds = [e['type'] for e in missed]
    assert 'runner.claude_changed' in kinds and 'runner.supervisor_restarted' in kinds


# The probe and the log ---------------------------------------------------------------------------


def test_the_probe_runs_claude_at_most_once_per_interval(tmp_path: Path) -> None:
    calls = tmp_path / 'calls'
    claude = write_script(
        tmp_path / 'claude',
        f'echo "$1" >> {calls}\n'
        'if [ "$1" = "--version" ]; then echo "2.1.296 (Claude Code)"; exit 0; fi\n'
        'if [ "$1" = "daemon" ]; then echo "pid:     4242"; exit 0; fi\nexit 2\n',
    )
    now = [0.0]
    probe = ClaudeProbe(Settings(claude=claude, cap=1), every=30.0, clock=lambda: now[0])
    assert probe() == Probe('2.1.296 (Claude Code)', 4242)
    now[0] = 29.0
    assert probe() is None
    now[0] = 30.0
    assert probe() == Probe('2.1.296 (Claude Code)', 4242)
    assert calls.read_text().split() == ['--version', 'daemon', '--version', 'daemon']


def test_the_probe_without_a_supervisor_or_claude(tmp_path: Path) -> None:
    claude = write_script(tmp_path / 'claude', 'if [ "$1" = "daemon" ]; then exit 1; fi\nexit 2\n')
    assert ClaudeProbe(Settings(claude=claude, cap=1))() == Probe('', None)
    missing = Settings(claude=tmp_path / 'no-claude', cap=1)
    assert ClaudeProbe(missing)() == Probe('', None)


def test_the_last_shutdown_line_wins(tmp_path: Path) -> None:
    log = tmp_path / 'daemon.log'
    log.write_text(LOG)
    assert last_shutdown(log) == {'cause': 'upgrade', 'live_workers': 5}
    log.write_text('shutting down (cause=signal)\n')
    assert last_shutdown(log) == {'cause': 'signal'}
    log.write_text('nothing to see\n')
    assert last_shutdown(log) == {}
    assert last_shutdown(tmp_path / 'missing.log') == {}


def test_the_real_sessions_get_a_probe(tmp_path: Path) -> None:
    class WithSettings(StubRoster):
        settings = Settings(claude=tmp_path / 'claude', cap=1, expected='2.1.296')

    watcher = Watcher(WithSettings(tmp_path))
    assert isinstance(watcher.probe, ClaudeProbe) and watcher.expected == '2.1.296'
    assert Watcher(StubRoster(tmp_path)).probe is None
