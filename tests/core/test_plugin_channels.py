"""Tests for the channel plugin registry (``marcel_core.plugin.channels``).

Covers ISSUE-7d6b3f stage 1: the registry API, the ``ChannelPlugin``
protocol, and precedence of plugin-declared capabilities over the built-in
rich-UI fallback set.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi import APIRouter

from marcel_core.channels.adapter import ChannelCapabilities, channel_supports_rich_ui
from marcel_core.plugin import channels as plugin_channels
from marcel_core.plugin.channels import (
    channel_has_rich_ui,
    get_channel,
    list_channels,
    register_channel,
)


@dataclass(frozen=True)
class _FakeChannel:
    name: str
    capabilities: ChannelCapabilities
    router: APIRouter | None = None


@pytest.fixture
def isolated_registry(monkeypatch):
    """Swap in a fresh registry dict per test, restored on teardown."""
    saved = dict(plugin_channels._registry)
    plugin_channels._registry.clear()
    yield plugin_channels._registry
    plugin_channels._registry.clear()
    plugin_channels._registry.update(saved)


class TestRegistry:
    def test_register_and_get(self, isolated_registry):
        plugin = _FakeChannel(name='demo', capabilities=ChannelCapabilities())
        register_channel(plugin)
        assert get_channel('demo') is plugin

    def test_get_unregistered_returns_none(self, isolated_registry):
        assert get_channel('nope') is None

    def test_list_is_sorted(self, isolated_registry):
        register_channel(_FakeChannel(name='zeta', capabilities=ChannelCapabilities()))
        register_channel(_FakeChannel(name='alpha', capabilities=ChannelCapabilities()))
        assert list_channels() == ['alpha', 'zeta']

    def test_reregister_same_instance_is_silent(self, isolated_registry, caplog):
        plugin = _FakeChannel(name='demo', capabilities=ChannelCapabilities())
        register_channel(plugin)
        with caplog.at_level('WARNING', logger='marcel_core.plugin.channels'):
            register_channel(plugin)
        assert 'already registered' not in caplog.text

    def test_reregister_different_instance_warns(self, isolated_registry, caplog):
        first = _FakeChannel(name='demo', capabilities=ChannelCapabilities())
        second = _FakeChannel(name='demo', capabilities=ChannelCapabilities(rich_ui=True))
        register_channel(first)
        with caplog.at_level('WARNING', logger='marcel_core.plugin.channels'):
            register_channel(second)
        assert 'already registered' in caplog.text
        assert get_channel('demo') is second


class TestRichUICapability:
    def test_channel_has_rich_ui_unregistered_returns_none(self, isolated_registry):
        assert channel_has_rich_ui('ghost') is None

    def test_channel_has_rich_ui_reads_plugin_flag(self, isolated_registry):
        register_channel(_FakeChannel(name='shiny', capabilities=ChannelCapabilities(rich_ui=True)))
        register_channel(_FakeChannel(name='plain', capabilities=ChannelCapabilities(rich_ui=False)))
        assert channel_has_rich_ui('shiny') is True
        assert channel_has_rich_ui('plain') is False

    def test_adapter_prefers_plugin_over_builtin(self, isolated_registry):
        register_channel(_FakeChannel(name='telegram', capabilities=ChannelCapabilities(rich_ui=False)))
        assert channel_supports_rich_ui('telegram') is False

    def test_adapter_falls_back_to_builtin_when_unregistered(self, isolated_registry):
        assert channel_supports_rich_ui('websocket') is True
        assert channel_supports_rich_ui('cli') is False
