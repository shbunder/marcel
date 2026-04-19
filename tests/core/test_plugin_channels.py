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
    discover,
    get_channel,
    list_channels,
    register_channel,
)


@dataclass(frozen=True)
class _FakeChannel:
    name: str
    capabilities: ChannelCapabilities
    router: APIRouter | None = None

    async def send_message(self, user_slug: str, text: str) -> bool:  # noqa: ARG002
        return False

    async def send_photo(
        self,
        user_slug: str,  # noqa: ARG002
        image_bytes: bytes,  # noqa: ARG002
        caption: str | None = None,  # noqa: ARG002
    ) -> bool:
        return False

    async def send_artifact_link(
        self,
        user_slug: str,  # noqa: ARG002
        artifact_id: str,  # noqa: ARG002
        title: str,  # noqa: ARG002
    ) -> bool:
        return False

    def resolve_user_slug(self, external_id: str) -> str | None:  # noqa: ARG002
        return None


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


class TestDiscoverExternalChannels:
    """Stage 4b — channel habitats in ``<MARCEL_ZOO_DIR>/channels/`` load at
    startup. Each habitat's ``__init__.py`` calls :func:`register_channel`.

    Failures in one habitat never abort discovery of its siblings; a missing
    zoo dir is a silent no-op.
    """

    def _write_habitat(self, root, name, body):
        habitat = root / 'channels' / name
        habitat.mkdir(parents=True)
        (habitat / '__init__.py').write_text(body)
        return habitat

    def _stub_zoo_dir(self, monkeypatch, path):
        from marcel_core import config as cfg

        monkeypatch.setattr(
            cfg.settings,
            'marcel_zoo_dir',
            str(path) if path is not None else None,
        )

    def test_discover_imports_channel_habitat(self, isolated_registry, tmp_path, monkeypatch):
        self._write_habitat(
            tmp_path,
            'demo',
            'from marcel_core.channels.adapter import ChannelCapabilities\n'
            'from marcel_core.plugin import register_channel\n'
            '\n'
            'class _Demo:\n'
            '    name = "demo"\n'
            '    capabilities = ChannelCapabilities()\n'
            '    router = None\n'
            '    async def send_message(self, u, t): return False\n'
            '    async def send_photo(self, u, b, caption=None): return False\n'
            '    async def send_artifact_link(self, u, a, t): return False\n'
            '    def resolve_user_slug(self, x): return None\n'
            '\n'
            'register_channel(_Demo())\n',
        )
        self._stub_zoo_dir(monkeypatch, tmp_path)
        import sys

        monkeypatch.setitem(sys.modules, '_marcel_ext_channels.demo', None)
        sys.modules.pop('_marcel_ext_channels.demo', None)

        discover()
        assert 'demo' in list_channels()
        assert get_channel('demo') is not None

    def test_discover_skips_sibling_failure(self, isolated_registry, tmp_path, monkeypatch, caplog):
        self._write_habitat(
            tmp_path,
            'broken',
            'raise RuntimeError("boom")\n',
        )
        self._write_habitat(
            tmp_path,
            'good',
            'from marcel_core.channels.adapter import ChannelCapabilities\n'
            'from marcel_core.plugin import register_channel\n'
            '\n'
            'class _Good:\n'
            '    name = "good"\n'
            '    capabilities = ChannelCapabilities()\n'
            '    router = None\n'
            '    async def send_message(self, u, t): return False\n'
            '    async def send_photo(self, u, b, caption=None): return False\n'
            '    async def send_artifact_link(self, u, a, t): return False\n'
            '    def resolve_user_slug(self, x): return None\n'
            '\n'
            'register_channel(_Good())\n',
        )
        self._stub_zoo_dir(monkeypatch, tmp_path)
        import sys

        for mod in ('_marcel_ext_channels.broken', '_marcel_ext_channels.good'):
            sys.modules.pop(mod, None)

        with caplog.at_level('ERROR', logger='marcel_core.plugin.channels'):
            discover()

        assert 'good' in list_channels()
        assert 'broken' not in list_channels()
        assert any("Failed to load channel habitat 'broken'" in rec.message for rec in caplog.records)

    def test_discover_no_zoo_dir_is_noop(self, isolated_registry, monkeypatch):
        self._stub_zoo_dir(monkeypatch, None)
        discover()
        assert list_channels() == []

    def test_discover_missing_channels_subdir_is_noop(self, isolated_registry, tmp_path, monkeypatch):
        self._stub_zoo_dir(monkeypatch, tmp_path)
        discover()
        assert list_channels() == []
