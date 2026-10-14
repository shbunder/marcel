"""`role_settings/`: the settings a role gets with `--settings` (S-03.10). Today only the worker:
it may not restart Marcel (rule: self-modification), refuses cross-session messages (ADR-003), and
may use workflows and subagents with an hour of prompt cache (ADR-009, S-21.1).
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
# The brain alone may not delegate (ADR-009, S-21.1): it routes, quests do the work. Workers may.
BRAIN_ONLY_DENIES = {'Agent', 'Task', 'Workflow'}
BRAIN_SETTINGS = Path(__file__).resolve().parents[2] / 'brain' / '.claude' / 'settings.json'


def worker() -> dict:
    return json.loads((ROLE_SETTINGS / 'worker.json').read_text())


def test_only_the_worker_has_role_settings() -> None:
    assert sorted(p.name for p in ROLE_SETTINGS.iterdir()) == ['worker.json']


def test_worker_settings_hold_only_the_allowed_keys_and_values() -> None:
    assert worker() == {
        'permissions': {'allow': ['Workflow'], 'deny': RESTARTS},
        'crossSessionInbound': 'refuse',
        'subagentPromptCacheTtl': '1h',
    }


def test_a_worker_cannot_restart_marcel_the_same_way_the_brain_cannot() -> None:
    """Delete the deny list and this goes red. The restart denies are the brain's own, in the
    same order; the brain's delegation denies are its own alone."""
    assert worker()['permissions']['deny'] == RESTARTS
    brain = json.loads(BRAIN_SETTINGS.read_text())['permissions']['deny']
    assert [rule for rule in brain if rule not in BRAIN_ONLY_DENIES] == RESTARTS
    assert set(brain) - set(RESTARTS) == BRAIN_ONLY_DENIES


def test_a_worker_may_delegate() -> None:
    # ADR-009: quests use subagents and workflows; only the brain is denied them.
    assert not set(worker()['permissions']['deny']) & BRAIN_ONLY_DENIES
    assert 'Workflow' in worker()['permissions']['allow']


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


def test_a_worker_may_use_workflows_and_subagents() -> None:
    """ADR-009: a quest may fan out. `Workflow` is pre-allowed; `Agent` is not listed, because
    entering auto mode drops `Agent` allow rules (docs: permission-modes), so a worker's subagent
    calls go to auto mode's judgement. Nothing denies either (the brain denies both)."""
    rules = worker()['permissions']
    assert rules['allow'] == ['Workflow']
    assert not any(r.split('(')[0] in ('Agent', 'Task', 'Workflow') for r in rules['deny'])


def test_a_workers_subagents_reuse_the_prompt_cache_for_an_hour() -> None:
    """`subagentPromptCacheTtl` takes "5m" or "1h"; Claude Code ignores any other value."""
    assert worker()['subagentPromptCacheTtl'] == '1h'
