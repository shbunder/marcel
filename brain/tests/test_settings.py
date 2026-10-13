"""brain/.claude/settings.json validates and agrees with how the runner starts the brain.

No copy of Claude Code's settings schema is available offline, so this is a strict check of the keys
the brain may set, with the values Claude Code 2.1.290 accepts for each (read from its settings schema
on 2026-10-12).
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from conftest import BRAIN, ROOT

import render

SETTINGS = BRAIN / '.claude' / 'settings.json'
RUNNER_API = (ROOT / 'contracts' / 'runner-api.yaml').read_text()

# Claude Code 2.1.290: the permission modes and the `crossSessionInbound` values.
CLAUDE_MODES = {'acceptEdits', 'auto', 'bypassPermissions', 'default', 'dontAsk', 'plan'}
CROSS_SESSION = {'accept', 'hold', 'refuse'}
ALLOWED_KEYS = {
    '$schema',
    'model',
    'permissions',
    'extraKnownMarketplaces',
    'enabledPlugins',
    'crossSessionInbound',
    'autoCompactWindow',
}
RESTARTS = ('docker restart', 'docker compose restart', 'systemctl restart', 'systemctl --user restart')


def load(path: Path = SETTINGS) -> dict[str, Any]:
    return json.loads(path.read_text())


def rendered(tmp_path: Path, values: dict[str, str]) -> dict[str, Any]:
    render.render(tmp_path / 'brain', values)
    return load(tmp_path / 'brain' / '.claude' / 'settings.json')


def test_only_known_keys(tmp_path: Path, values: dict[str, str]) -> None:
    settings = rendered(tmp_path, values)
    assert set(settings) <= ALLOWED_KEYS, f'unexpected keys: {sorted(set(settings) - ALLOWED_KEYS)}'
    assert set(settings['permissions']) == {'deny'}
    assert all(isinstance(rule, str) for rule in settings['permissions']['deny'])


def test_opus_like_the_hub_asks_for() -> None:
    # B-13: the brain is Opus. The hub spawns it with the agent's model_brain, which defaults to opus.
    assert load()['model'] == 'opus'
    hub_models = (ROOT / 'hub' / 'src' / 'marcel_hub' / 'models.py').read_text()
    assert "model_brain: str = 'opus'" in hub_models


def test_the_mode_comes_from_the_runner_flag_not_settings() -> None:
    # Workspace settings cannot grant auto mode; the runner's --permission-mode flag sets it.
    assert 'defaultMode' not in load()['permissions']
    runner_modes = re.search(r'permission_mode:\s*\n\s*type: string\s*\n\s*enum: \[([^\]]*)\]', RUNNER_API)
    assert runner_modes, 'SpawnRequest.permission_mode not found in contracts/runner-api.yaml'
    assert 'auto' in [m.strip() for m in runner_modes.group(1).split(',')]
    assert 'auto' in CLAUDE_MODES


def test_the_plugin_the_runner_starts_the_channel_with_is_enabled(
    tmp_path: Path, values: dict[str, str]
) -> None:
    settings = rendered(tmp_path, values)
    plugin = re.search(r'--channels plugin:(\S+?)`', RUNNER_API)
    assert plugin, 'the --channels plugin is not named in contracts/runner-api.yaml'
    assert settings['enabledPlugins'] == {plugin.group(1): True}
    marketplace = plugin.group(1).split('@')[1]
    assert settings['extraKnownMarketplaces'][marketplace] == {
        'source': {'source': 'directory', 'path': values['MARKETPLACE_DIR']}
    }


def test_auto_compact_keeps_the_context(tmp_path: Path, values: dict[str, str]) -> None:
    # S-06.8 (ADR-010): Claude Code compacts the brain at a 200k-token window instead of the hub
    # rolling it over; a tokens value between 100000 and 1000000 (Claude Code's range).
    window = rendered(tmp_path, values)['autoCompactWindow']
    assert window == 200_000
    assert isinstance(window, int)
    assert 100_000 <= window <= 1_000_000


def test_cross_session_messages_are_refused() -> None:
    # SP1 chose the channel for everything that reaches the brain (ADR-001, ADR-003). A worker's
    # SendMessage would reach the brain around the hub, so it is refused.
    value = load()['crossSessionInbound']
    assert value in CROSS_SESSION
    assert value == 'refuse'


@pytest.mark.parametrize('command', RESTARTS)
def test_restarts_are_denied(command: str) -> None:
    # Control (self-modification rule): delete a deny rule and this goes red.
    assert f'Bash({command}:*)' in load()['permissions']['deny']


def test_nothing_denies_the_tools_the_runner_allows() -> None:
    allowed = set(re.findall(r'mcp__plugin_marcel_\w+', RUNNER_API))
    assert allowed, 'no allowed tools found in contracts/runner-api.yaml'
    for rule in load()['permissions']['deny']:
        assert not rule.startswith('mcp__'), f"{rule} contradicts the runner's --allowedTools"


def test_no_api_key_or_model_endpoint() -> None:
    text = SETTINGS.read_text()
    for banned in ('ANTHROPIC', 'apiKeyHelper', 'api.anthropic.com', '"env"'):
        assert banned not in text
