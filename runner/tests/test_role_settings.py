"""`role_settings/`: the settings a role gets with `--settings` (S-03.10). Today only the worker:
it may not restart Marcel (rule: self-modification) and refuses cross-session messages (ADR-003).
No settings schema is available offline, so the keys and values are checked strictly here, like
brain/tests/test_settings.py."""

import json
from pathlib import Path

import pytest

from marcel_runner.sessions import ROLE_SETTINGS, role_settings, spawn_argv

RESTARTS = [
    'Bash(docker restart:*)',
    'Bash(docker compose restart:*)',
    'Bash(docker-compose restart:*)',
    'Bash(systemctl restart:*)',
    'Bash(systemctl --user restart:*)',
]
BRAIN_SETTINGS = Path(__file__).resolve().parents[2] / 'brain' / '.claude' / 'settings.json'


def worker() -> dict:
    return json.loads((ROLE_SETTINGS / 'worker.json').read_text())


def test_only_the_worker_has_role_settings() -> None:
    assert sorted(p.name for p in ROLE_SETTINGS.iterdir()) == ['worker.json']


def test_worker_settings_hold_only_the_allowed_keys_and_values() -> None:
    assert worker() == {'permissions': {'deny': RESTARTS}, 'crossSessionInbound': 'refuse'}


def test_a_worker_cannot_restart_marcel_the_same_way_the_brain_cannot() -> None:
    """Delete the deny list and this goes red. Kept equal to the brain's own list."""
    assert worker()['permissions']['deny'] == RESTARTS
    assert json.loads(BRAIN_SETTINGS.read_text())['permissions']['deny'] == RESTARTS


def test_a_worker_refuses_cross_session_messages() -> None:
    assert worker()['crossSessionInbound'] == 'refuse'


@pytest.mark.parametrize(('role', 'gets'), [('worker', True), ('brain', False), ('side', False)])
def test_spawn_passes_settings_only_for_a_role_that_has_them(role: str, gets: bool) -> None:
    argv = spawn_argv(
        Path('/usr/local/bin/claude'),
        name='n',
        model='sonnet',
        permission_mode='auto',
        prompt='p',
        debug_file=Path('/tmp/d.log'),
        channel=True,
        role=role,
    )
    before = argv[: argv.index('--')]
    if gets:
        assert before[before.index('--settings') + 1] == str(ROLE_SETTINGS / 'worker.json')
    else:
        assert '--settings' not in before
    assert role_settings(role) == (ROLE_SETTINGS / 'worker.json' if gets else None)
