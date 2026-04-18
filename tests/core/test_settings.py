"""Tests for per-channel model settings storage."""

from __future__ import annotations

import pathlib

import pytest

import marcel_core.storage._root as _root_mod
from marcel_core.storage.settings import (
    clear_channel_tier,
    load_channel_model,
    load_channel_tier,
    save_channel_model,
    save_channel_tier,
)


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_root_mod, '_DATA_ROOT', tmp_path)


# ---------------------------------------------------------------------------
# storage.settings
# ---------------------------------------------------------------------------


def test_load_returns_none_when_no_settings():
    assert load_channel_model('shaun', 'telegram') is None


def test_save_and_load_roundtrip():
    save_channel_model('shaun', 'telegram', 'anthropic:claude-opus-4-6')
    assert load_channel_model('shaun', 'telegram') == 'anthropic:claude-opus-4-6'


def test_channels_are_independent():
    save_channel_model('shaun', 'telegram', 'anthropic:claude-opus-4-6')
    save_channel_model('shaun', 'cli', 'openai:gpt-4o')
    assert load_channel_model('shaun', 'telegram') == 'anthropic:claude-opus-4-6'
    assert load_channel_model('shaun', 'cli') == 'openai:gpt-4o'


def test_users_are_independent():
    save_channel_model('shaun', 'telegram', 'anthropic:claude-opus-4-6')
    save_channel_model('other', 'telegram', 'openai:gpt-4o')
    assert load_channel_model('shaun', 'telegram') == 'anthropic:claude-opus-4-6'
    assert load_channel_model('other', 'telegram') == 'openai:gpt-4o'


def test_update_overwrites_previous():
    save_channel_model('shaun', 'telegram', 'anthropic:claude-opus-4-6')
    save_channel_model('shaun', 'telegram', 'anthropic:claude-haiku-4-5-20251001')
    assert load_channel_model('shaun', 'telegram') == 'anthropic:claude-haiku-4-5-20251001'


def test_settings_file_created_in_user_dir(tmp_path: pathlib.Path):
    save_channel_model('shaun', 'cli', 'openai:gpt-4o')
    settings_file = tmp_path / 'users' / 'shaun' / 'settings.json'
    assert settings_file.exists()


def test_load_with_corrupt_file(tmp_path: pathlib.Path):
    """Corrupt settings file returns None without raising."""
    settings_path = tmp_path / 'users' / 'shaun' / 'settings.json'
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text('not json')
    assert load_channel_model('shaun', 'telegram') is None


# ---------------------------------------------------------------------------
# channel_tiers (ISSUE-e0db47)
# ---------------------------------------------------------------------------


def test_channel_tier_load_returns_none_when_unset():
    assert load_channel_tier('shaun', 'telegram') is None


def test_channel_tier_roundtrip():
    save_channel_tier('shaun', 'telegram', 'fast')
    assert load_channel_tier('shaun', 'telegram') == 'fast'


def test_channel_tier_clear_removes_entry():
    save_channel_tier('shaun', 'telegram', 'standard')
    clear_channel_tier('shaun', 'telegram')
    assert load_channel_tier('shaun', 'telegram') is None


def test_channel_tier_clear_is_noop_when_unset():
    # Must not raise or create a settings file needlessly.
    clear_channel_tier('shaun', 'telegram')
    assert load_channel_tier('shaun', 'telegram') is None


def test_channel_tier_and_channel_model_coexist():
    save_channel_model('shaun', 'telegram', 'anthropic:claude-opus-4-6')
    save_channel_tier('shaun', 'telegram', 'fast')
    assert load_channel_model('shaun', 'telegram') == 'anthropic:claude-opus-4-6'
    assert load_channel_tier('shaun', 'telegram') == 'fast'


def test_channel_tiers_per_channel_and_user_independent():
    save_channel_tier('shaun', 'telegram', 'fast')
    save_channel_tier('shaun', 'cli', 'standard')
    save_channel_tier('other', 'telegram', 'standard')
    assert load_channel_tier('shaun', 'telegram') == 'fast'
    assert load_channel_tier('shaun', 'cli') == 'standard'
    assert load_channel_tier('other', 'telegram') == 'standard'


def test_self_healing_migration_qualifies_legacy_names(tmp_path: pathlib.Path):
    """Legacy unqualified names stored pre-ISSUE-073 get ``anthropic:`` prepended."""
    import json

    settings_path = tmp_path / 'users' / 'shaun' / 'settings.json'
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps({'channel_models': {'telegram': 'claude-opus-4-6'}}))

    assert load_channel_model('shaun', 'telegram') == 'anthropic:claude-opus-4-6'
    # file was rewritten
    rewritten = json.loads(settings_path.read_text())
    assert rewritten['channel_models']['telegram'] == 'anthropic:claude-opus-4-6'
