from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / '.claude' / 'hooks' / 'guard-git-staging.py'


def load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location('guard_git_staging', HOOK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def hook() -> ModuleType:
    return load_hook()


def run(hook: ModuleType, monkeypatch: pytest.MonkeyPatch, command: str, tool: str = 'Bash') -> int:
    payload = json.dumps({'tool_name': tool, 'tool_input': {'command': command}})
    monkeypatch.setattr('sys.stdin', io.StringIO(payload))
    return hook.main()


@pytest.mark.parametrize(
    'command',
    [
        'git add .',
        'git add -A',
        'git add --all',
        'git add -u',
        'git add --update',
        'git add ./',
        'git -C ../marcel-S-04.9 add .',
        'git status && git add -A && git commit -m "x"',
        'cd hub; git add .',
        'git commit -a -m "x"',
        'git commit -am "x"',
        'git commit --all -m "x"',
        'git commit -m "x" -a',
        '/usr/bin/git add .',
    ],
)
def test_blocks_broad_staging(hook: ModuleType, monkeypatch: pytest.MonkeyPatch, command: str):
    assert run(hook, monkeypatch, command) == 2


@pytest.mark.parametrize(
    'command',
    [
        'git add hub/src/marcel_hub/app.py docs/hub.md',
        'git add ./hub/app.py',
        'git add -p hub/app.py',
        'git commit -m "add . to the list of -A flags"',
        'git commit -m "x" -- hub/app.py',
        'git commit -m -a',
        'git commit --amend --no-edit',
        'git status --short',
        'echo git add .',
        'git',
    ],
)
def test_allows_named_staging(hook: ModuleType, monkeypatch: pytest.MonkeyPatch, command: str):
    assert run(hook, monkeypatch, command) == 0


def test_ignores_other_tools(hook: ModuleType, monkeypatch: pytest.MonkeyPatch):
    assert run(hook, monkeypatch, 'git add .', tool='Edit') == 0


def test_unbalanced_quotes_still_checked(hook: ModuleType, monkeypatch: pytest.MonkeyPatch):
    assert run(hook, monkeypatch, 'git add -A; echo "oops') == 2


def test_fails_open_on_malformed_input(hook: ModuleType, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr('sys.stdin', io.StringIO('not json'))
    assert hook.main() == 0


def test_message_says_how_to_stage(hook: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys):
    run(hook, monkeypatch, 'git add .')
    assert 'git add <path>' in capsys.readouterr().err


def test_runs_as_a_script_and_blocks():
    payload = json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'git add -A'}})
    done = subprocess.run(
        [sys.executable, str(HOOK)], input=payload, text=True, capture_output=True
    )
    assert done.returncode == 2


def test_project_settings_run_the_hook_on_bash():
    # The guard only works if Claude Code calls it: deleting this wiring must turn a test red.
    settings = json.loads((ROOT / '.claude' / 'settings.json').read_text())
    commands = [
        h['command']
        for entry in settings['hooks']['PreToolUse']
        if entry['matcher'] == 'Bash'
        for h in entry['hooks']
    ]
    assert any('guard-git-staging.py' in c for c in commands)
