"""Tests for per-channel model settings storage and integration handlers."""

from __future__ import annotations

import pathlib

import pytest

import marcel_core.storage._root as _root_mod
from marcel_core.storage.settings import load_channel_model, save_channel_model


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_root_mod, '_DATA_ROOT', tmp_path)


# ---------------------------------------------------------------------------
# storage.settings
# ---------------------------------------------------------------------------


def test_load_returns_none_when_no_settings():
    assert load_channel_model('shaun', 'telegram') is None


def test_save_and_load_roundtrip():
    save_channel_model('shaun', 'telegram', 'claude-opus-4-6')
    assert load_channel_model('shaun', 'telegram') == 'claude-opus-4-6'


def test_channels_are_independent():
    save_channel_model('shaun', 'telegram', 'claude-opus-4-6')
    save_channel_model('shaun', 'cli', 'gpt-4o')
    assert load_channel_model('shaun', 'telegram') == 'claude-opus-4-6'
    assert load_channel_model('shaun', 'cli') == 'gpt-4o'


def test_users_are_independent():
    save_channel_model('shaun', 'telegram', 'claude-opus-4-6')
    save_channel_model('other', 'telegram', 'gpt-4o')
    assert load_channel_model('shaun', 'telegram') == 'claude-opus-4-6'
    assert load_channel_model('other', 'telegram') == 'gpt-4o'


def test_update_overwrites_previous():
    save_channel_model('shaun', 'telegram', 'claude-opus-4-6')
    save_channel_model('shaun', 'telegram', 'claude-haiku-4-5-20251001')
    assert load_channel_model('shaun', 'telegram') == 'claude-haiku-4-5-20251001'


def test_settings_file_created_in_user_dir(tmp_path: pathlib.Path):
    save_channel_model('shaun', 'cli', 'gpt-4o')
    settings_file = tmp_path / 'users' / 'shaun' / 'settings.json'
    assert settings_file.exists()


def test_load_with_corrupt_file(tmp_path: pathlib.Path):
    """Corrupt settings file returns None without raising."""
    settings_path = tmp_path / 'users' / 'shaun' / 'settings.json'
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text('not json')
    assert load_channel_model('shaun', 'telegram') is None


# ---------------------------------------------------------------------------
# integration handlers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_models_returns_all_models():
    from marcel_core.skills.integrations.settings import list_models

    result = await list_models({}, 'shaun')
    assert 'claude-sonnet-4-6' in result
    assert 'gpt-4o' in result


@pytest.mark.asyncio
async def test_get_model_returns_default_when_unset():
    from marcel_core.harness.agent import DEFAULT_MODEL
    from marcel_core.skills.integrations.settings import get_model

    result = await get_model({'channel': 'telegram'}, 'shaun')
    assert DEFAULT_MODEL in result


@pytest.mark.asyncio
async def test_get_model_returns_saved_preference():
    from marcel_core.skills.integrations.settings import get_model

    save_channel_model('shaun', 'telegram', 'claude-opus-4-6')
    result = await get_model({'channel': 'telegram'}, 'shaun')
    assert 'claude-opus-4-6' in result


@pytest.mark.asyncio
async def test_get_model_missing_channel_returns_error():
    from marcel_core.skills.integrations.settings import get_model

    result = await get_model({}, 'shaun')
    assert 'Error' in result


@pytest.mark.asyncio
async def test_set_model_saves_preference():
    from marcel_core.skills.integrations.settings import set_model

    result = await set_model({'channel': 'telegram', 'model': 'claude-opus-4-6'}, 'shaun')
    assert 'claude-opus-4-6' in result
    assert load_channel_model('shaun', 'telegram') == 'claude-opus-4-6'


@pytest.mark.asyncio
async def test_set_model_rejects_unknown_model():
    from marcel_core.skills.integrations.settings import set_model

    result = await set_model({'channel': 'telegram', 'model': 'not-a-real-model'}, 'shaun')
    assert 'Error' in result


@pytest.mark.asyncio
async def test_set_model_missing_params_returns_error():
    from marcel_core.skills.integrations.settings import set_model

    result = await set_model({'channel': 'telegram'}, 'shaun')
    assert 'Error' in result

    result = await set_model({'model': 'claude-opus-4-6'}, 'shaun')
    assert 'Error' in result
