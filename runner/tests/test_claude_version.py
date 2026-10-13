"""The Claude Code version pin (S-03.10): `runner/claude-version` is the version Marcel is tested
with. Another one installed: `/health` says so in plain words, the runner keeps serving it, and
every start (spawn, resume, fork, cloud) answers 422 `claude_version` and starts nothing.

Through the socket, against the fake claude (`MARCEL_FAKE_CLAUDE_VERSION` makes it report another
version)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import CLAUDE_VERSION, write_script
from test_cloud import CloudRig
from test_sessions import Rig

from marcel_runner.health import PIN_FILE, Settings, pinned_version, version_problem
from marcel_runner.server import StartupError, serve

OTHER = '2.1.999'


@pytest.fixture
def up(tmp_path: Path) -> Iterator[Rig]:
    rig = CloudRig(tmp_path)
    rig.start()
    yield rig
    rig.close()


def other_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('MARCEL_FAKE_CLAUDE_VERSION', OTHER)


def test_the_pin_is_one_bare_version_number() -> None:
    pin = PIN_FILE.read_text()
    assert pin == f'{pinned_version()}\n' and pin.count('.') == 2 and pin.split('.')[0].isdigit()
    assert pinned_version() == CLAUDE_VERSION  # the test stubs read it too


def test_health_says_in_plain_words_which_version_is_wrong(
    up: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    other_version(monkeypatch)
    status, health = up.call('GET', '/health')
    assert status == 200  # the runner keeps serving /health
    assert health['ok'] is False
    assert health['claude_version'] == f'{OTHER} (Claude Code)'
    assert health['claude_version_expected'] == CLAUDE_VERSION
    assert health['reason'] == (
        f'Claude Code {OTHER} is installed, but Marcel is tested with {CLAUDE_VERSION}. '
        f'Install {CLAUDE_VERSION}, or test the new version and change runner/claude-version.'
    )


def test_with_the_pinned_version_health_is_ok(up: Rig) -> None:
    status, health = up.call('GET', '/health')
    assert (status, health['ok'], health['claude_version_expected']) == (200, True, CLAUDE_VERSION)


def refused(status: int, body: dict) -> None:
    assert (status, body['code']) == (422, 'claude_version'), body
    assert OTHER in body['message'] and CLAUDE_VERSION in body['message']
    assert body['message'].startswith(('No session was started.', 'Nothing was started.'))


def test_a_spawn_is_refused_and_starts_nothing(up: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    """Delete the comparison and this goes red: the spawn would go ahead."""
    other_version(monkeypatch)
    refused(*up.spawn())
    assert up.calls_with('--bg') == []


def test_resume_and_fork_are_refused_and_start_nothing(
    up: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, brain = up.spawn(role='brain', name='brain')
    assert status == 201, brain
    sid = brain['session_id']
    assert up.call('POST', f'/sessions/{sid}/stop')[0] == 200
    started = len(up.calls_with('--bg'))
    other_version(monkeypatch)  # Claude Code was updated since
    refused(*up.call('POST', f'/sessions/{sid}/resume', {'prompt': 'go'}))
    refused(*up.call('POST', f'/sessions/{sid}/fork', {'name': 'side', 'prompt': 'hi'}))
    assert len(up.calls_with('--bg')) == started


def test_a_cloud_start_is_refused_and_starts_nothing(
    up: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    other_version(monkeypatch)
    refused(*up.call('POST', '/cloud/sessions', {'prompt': 'Fix the flaky login test'}))
    assert up.calls_with('--cloud') == []


def test_the_runner_starts_on_another_version_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    claude = write_script(
        tmp_path / 'claude',
        f'if [ "$1" = "--version" ]; then echo "{OTHER} (Claude Code)"; exit 0; fi\n'
        'if [ "$1" = "daemon" ]; then echo "pid:     1"; exit 0; fi\nexit 2\n',
    )
    srv = serve(
        tmp_path / 'r.sock',
        Settings(claude=claude, cap=1),
        cgroup_of=lambda pid: 'a' if pid == 1 else 'b',
    )
    srv.server_close()
    assert f'Claude Code {OTHER} is installed' in capsys.readouterr().err


def test_with_the_version_and_the_supervisor_both_wrong_start_up_names_both(
    tmp_path: Path,
) -> None:
    claude = write_script(
        tmp_path / 'claude',
        f'if [ "$1" = "--version" ]; then echo "{OTHER} (Claude Code)"; exit 0; fi\n'
        'if [ "$1" = "daemon" ]; then echo "not running"; exit 1; fi\nexit 2\n',
    )
    with pytest.raises(StartupError) as e:
        serve(tmp_path / 'r.sock', Settings(claude=claude, cap=1), cgroup_of=lambda pid: 'x')
    assert f'Claude Code {OTHER} is installed' in str(e.value)
    assert 'supervisor' in str(e.value).lower() or 'marcel-claude-daemon' in str(e.value)


@pytest.mark.parametrize(
    ('installed', 'wrong'),
    [
        (f'{CLAUDE_VERSION} (Claude Code)', False),
        (f'{OTHER} (Claude Code)', True),
        ('', False),  # claude missing: the health check reports that on its own
    ],
)
def test_version_problem(installed: str, wrong: bool) -> None:
    assert (version_problem(installed, CLAUDE_VERSION) is not None) is wrong
