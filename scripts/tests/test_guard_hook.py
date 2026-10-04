from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType

import pytest

HOOK = Path(__file__).resolve().parents[2] / '.claude' / 'hooks' / 'guard-restricted.py'


def load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location('guard_restricted', HOOK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(hook: ModuleType, monkeypatch: pytest.MonkeyPatch, tool: str, path: str) -> int:
    payload = json.dumps({'tool_name': tool, 'tool_input': {'file_path': path}})
    monkeypatch.setattr('sys.stdin', io.StringIO(payload))
    return hook.main()


@pytest.fixture
def hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = load_hook()
    monkeypatch.setattr(module, 'UNLOCK_FLAG', tmp_path / '.unlock-safety')
    return module


@pytest.mark.parametrize(
    'path',
    [
        'contracts/app-api.yaml',
        '/home/user/marcel/contracts/runner-api.yaml',
        'CLAUDE.md',
        'hub/CLAUDE.md',
        '.env',
        'deploy/.env.local',
    ],
)
def test_blocks_restricted_paths(hook: ModuleType, monkeypatch: pytest.MonkeyPatch, path: str):
    assert run(hook, monkeypatch, 'Edit', path) == 2


@pytest.mark.parametrize('path', ['hub/src/marcel_hub/app.py', 'plan/README.md', 'README.md'])
def test_allows_other_paths(hook: ModuleType, monkeypatch: pytest.MonkeyPatch, path: str):
    assert run(hook, monkeypatch, 'Write', path) == 0


def test_unlock_flag_allows_restricted_edit(
    hook: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    (tmp_path / '.unlock-safety').touch()
    assert run(hook, monkeypatch, 'Edit', 'contracts/app-api.yaml') == 0


def test_ignores_tools_that_do_not_write(hook: ModuleType, monkeypatch: pytest.MonkeyPatch):
    assert run(hook, monkeypatch, 'Read', 'contracts/app-api.yaml') == 0


def test_fails_open_on_malformed_input(hook: ModuleType, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr('sys.stdin', io.StringIO('not json'))
    assert hook.main() == 0


def test_block_message_says_how_to_unlock(
    hook: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    run(hook, monkeypatch, 'Edit', 'contracts/x.yaml')
    err = capsys.readouterr().err
    assert 'contract' in err
    assert '.unlock-safety' in err
