from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / '.claude' / 'hooks' / 'guard-restricted.py'


def load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location('guard_restricted', HOOK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def payload(tool: str, path: str) -> str:
    return json.dumps({'tool_name': tool, 'tool_input': {'file_path': path}})


def run(hook: ModuleType, monkeypatch: pytest.MonkeyPatch, tool: str, path: str) -> int:
    monkeypatch.setattr('sys.stdin', io.StringIO(payload(tool, path)))
    return hook.main()


@pytest.fixture
def hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = load_hook()
    monkeypatch.setattr(module, 'UNLOCK_FLAG', tmp_path / '.unlock-safety')
    return module


@pytest.mark.parametrize(
    'relative', ['contracts/app-api.yaml', 'CLAUDE.md', '.env', 'deploy/.env.local']
)
def test_blocks_restricted_paths(hook: ModuleType, monkeypatch: pytest.MonkeyPatch, relative: str):
    assert run(hook, monkeypatch, 'Edit', str(ROOT / relative)) == 2


def test_blocks_relative_path_from_repo_root(hook: ModuleType, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(ROOT)
    assert run(hook, monkeypatch, 'Write', 'contracts/runner-api.yaml') == 2


@pytest.mark.parametrize(
    'relative', ['hub/src/marcel_hub/app.py', 'plan/README.md', 'README.md', 'brain/CLAUDE.md']
)
def test_allows_other_paths(hook: ModuleType, monkeypatch: pytest.MonkeyPatch, relative: str):
    assert run(hook, monkeypatch, 'Write', str(ROOT / relative)) == 0


def test_contracts_folder_outside_the_repo_is_not_ours(
    hook: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    assert run(hook, monkeypatch, 'Write', str(tmp_path / 'contracts' / 'x.yaml')) == 0


def test_unlock_flag_allows_restricted_edit(
    hook: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    (tmp_path / '.unlock-safety').touch()
    assert run(hook, monkeypatch, 'Edit', str(ROOT / 'contracts/app-api.yaml')) == 0


def test_ignores_tools_that_do_not_write(hook: ModuleType, monkeypatch: pytest.MonkeyPatch):
    assert run(hook, monkeypatch, 'Read', str(ROOT / 'contracts/app-api.yaml')) == 0


def test_fails_open_on_malformed_input(hook: ModuleType, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr('sys.stdin', io.StringIO('not json'))
    assert hook.main() == 0


def test_block_message_says_how_to_unlock(
    hook: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    run(hook, monkeypatch, 'Edit', str(ROOT / 'contracts/x.yaml'))
    err = capsys.readouterr().err
    assert 'contract' in err
    assert '.unlock-safety' in err


def registered_command() -> str:
    settings = json.loads((ROOT / '.claude' / 'settings.json').read_text())
    entries = settings['hooks']['PreToolUse']
    commands = [
        h['command']
        for entry in entries
        if 'Edit' in entry['matcher'].split('|')
        for h in entry['hooks']
        if 'guard-restricted' in h['command']
    ]
    assert len(commands) == 1, 'the guard hook must be registered once for Edit'
    return commands[0]


@pytest.mark.parametrize('cwd', ['.', 'hub'])
def test_registered_command_blocks_from_any_folder(cwd: str):
    """Runs the command exactly as Claude Code does, from the root and from a subfolder."""
    if (ROOT / '.claude' / '.unlock-safety').exists():
        pytest.skip('an unlock flag is present in this checkout')
    result = subprocess.run(
        registered_command(),
        shell=True,
        cwd=ROOT / cwd,
        input=payload('Edit', str(ROOT / 'contracts/app-api.yaml')),
        capture_output=True,
        text=True,
        env={**os.environ, 'CLAUDE_PROJECT_DIR': str(ROOT)},
    )
    assert result.returncode == 2, result.stderr
    assert 'Blocked edit' in result.stderr
