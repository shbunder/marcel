"""Every session the runner starts leaves the owner's user settings out (S-03.10).

A control: without `--setting-sources project,local`, the brain and every worker inherit the
allow rules in the owner's own ~/.claude/settings.json. Spawn and fork pass it; a resume passes
no flag and keeps it through Claude's saved options. The golden command lines pin the same
thing; this test names the control so a failure says what was lost.
"""

import json
from pathlib import Path

import pytest

from marcel_runner.fork import fork_argv
from marcel_runner.sessions import spawn_argv

CLAUDE = Path('/usr/local/bin/claude')
DEBUG = Path('/tmp/debug.log')


def _before_prompt(argv: list[str]) -> list[str]:
    return argv[: argv.index('--')]


def _assert_project_and_local_only(argv: list[str]) -> None:
    flags = _before_prompt(argv)
    assert '--setting-sources' in flags, (
        "The session would load the owner's ~/.claude/settings.json and its allow rules: "
        '--setting-sources is missing.'
    )
    sources = flags[flags.index('--setting-sources') + 1]
    assert sources == 'project,local', (
        f'The session would load settings from {sources!r}; only project and local are allowed.'
    )


@pytest.mark.parametrize('role', ['brain', 'side', 'worker', 'adopted'])
@pytest.mark.parametrize('channel', [True, False])
def test_spawn_loads_only_project_and_local_settings(role: str, channel: bool) -> None:
    argv = spawn_argv(
        CLAUDE,
        name='tsk-1',
        model='sonnet',
        permission_mode='auto',
        prompt='Do the thing.',
        debug_file=DEBUG,
        channel=channel,
        role=role,
    )
    _assert_project_and_local_only(argv)


def test_resume_keeps_project_and_local_only_through_the_saved_options(tmp_path: Path) -> None:
    """A resume passes no flag (2.1.296 starts a copy for any), so the woken session keeps the
    `--setting-sources project,local` Claude saved at spawn (recorded on the NUC, 2026-10-13).
    Drop the flag from spawn and the saved options lose it, so this goes red too."""
    from test_sessions import Rig

    rig = Rig(tmp_path)
    rig.start()
    try:
        status, spawned = rig.spawn(role='brain', name='brain')
        assert status == 201, spawned
        sid = spawned['session_id']
        assert rig.call('POST', f'/sessions/{sid}/stop')[0] == 200
        assert rig.call('POST', f'/sessions/{sid}/resume', {'prompt': 'go'})[0] == 200
        (call,) = rig.calls_with('--resume')
        assert '--setting-sources' not in call['argv']
        job = rig.fake.home / '.claude' / 'jobs' / spawned['short_id'] / 'state.json'
        _assert_project_and_local_only([*json.loads(job.read_text())['respawnFlags'], '--'])
    finally:
        rig.close()


def test_fork_loads_only_project_and_local_settings() -> None:
    argv = fork_argv(
        CLAUDE,
        session_id='0b5c3e2a-0000-4000-8000-000000000000',
        name='side-1',
        model=None,
        permission_mode='manual',
        prompt='You are a side thread.',
        debug_file=DEBUG,
        channel=True,
    )
    _assert_project_and_local_only(argv)
