"""Tests for the marcel_core.plugin surface and external integration discovery.

Covers ISSUE-3c87dd: the stable plugin re-exports plus the widened
``discover()`` path that walks ``<data_root>/integrations/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from marcel_core.skills.integrations import (
    _EXTERNAL_MODULE_PREFIX,
    _discover_external,
    _registry,
    get_handler,
    list_python_skills,
)


@pytest.fixture
def isolated_registry(monkeypatch):
    """Give each test a fresh integration registry, restored on teardown."""
    saved = dict(_registry)
    monkeypatch.setattr('marcel_core.skills.integrations._registry', {})
    yield
    _registry.clear()
    _registry.update(saved)


@pytest.fixture
def cleanup_external_modules():
    """Remove any ``_marcel_ext_integrations.*`` entries created during a test."""
    yield
    for name in list(sys.modules):
        if name.startswith(_EXTERNAL_MODULE_PREFIX):
            sys.modules.pop(name, None)


def _write_integration(root: Path, name: str, body: str) -> Path:
    """Materialize an external integration package under ``root/integrations/``."""
    pkg = root / 'integrations' / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / '__init__.py').write_text(body, encoding='utf-8')
    return pkg


class TestPluginSurface:
    def test_plugin_reexports_register_and_types(self):
        """marcel_core.plugin re-exports the integration surface."""
        from marcel_core import plugin
        from marcel_core.skills.integrations import IntegrationHandler, register

        assert plugin.register is register
        assert plugin.IntegrationHandler is IntegrationHandler

    def test_plugin_get_logger_returns_logger(self):
        from marcel_core.plugin import get_logger

        log = get_logger('marcel_core.plugin.test')
        assert log.name == 'marcel_core.plugin.test'


class TestExternalDiscovery:
    def test_external_integration_loads_and_registers(
        self, tmp_path, monkeypatch, isolated_registry, cleanup_external_modules
    ):
        """A minimal habitat at <data_root>/integrations/<name>/ is discovered."""
        from marcel_core.config import settings

        _write_integration(
            tmp_path,
            'demotest',
            (
                'from marcel_core.plugin import register\n'
                '\n'
                '@register("demotest.ping")\n'
                'async def ping(params, user_slug):\n'
                '    return "pong"\n'
            ),
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))

        _discover_external()

        assert 'demotest.ping' in list_python_skills()
        handler = get_handler('demotest.ping')
        assert callable(handler)

    @pytest.mark.asyncio
    async def test_loaded_handler_is_callable(self, tmp_path, monkeypatch, isolated_registry, cleanup_external_modules):
        """The handler function registered by an external habitat actually runs."""
        from marcel_core.config import settings

        _write_integration(
            tmp_path,
            'echotest',
            (
                'from marcel_core.plugin import register\n'
                '\n'
                '@register("echotest.say")\n'
                'async def say(params, user_slug):\n'
                "    return f\"said {params.get('msg', '')} for {user_slug}\"\n"
            ),
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))

        _discover_external()

        result = await get_handler('echotest.say')({'msg': 'hi'}, 'alice')
        assert result == 'said hi for alice'

    def test_namespace_mismatch_rejects_integration(
        self, tmp_path, monkeypatch, isolated_registry, cleanup_external_modules, caplog
    ):
        """Handlers whose family doesn't match the dir name are rejected in full."""
        from marcel_core.config import settings

        _write_integration(
            tmp_path,
            'foo',
            (
                'from marcel_core.plugin import register\n'
                '\n'
                '@register("bar.baz")\n'
                'async def bad(params, user_slug):\n'
                '    return "never reached"\n'
            ),
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))

        with caplog.at_level('ERROR', logger='marcel_core.skills.integrations'):
            _discover_external()

        assert 'bar.baz' not in list_python_skills()
        assert any('foo' in r.message and 'namespace' in r.message for r in caplog.records)

    def test_namespace_partial_match_rolls_back_valid_handlers(
        self, tmp_path, monkeypatch, isolated_registry, cleanup_external_modules
    ):
        """If one handler is out-of-namespace, sibling registrations roll back too."""
        from marcel_core.config import settings

        _write_integration(
            tmp_path,
            'foo',
            (
                'from marcel_core.plugin import register\n'
                '\n'
                '@register("foo.ok")\n'
                'async def good(params, user_slug):\n'
                '    return "ok"\n'
                '\n'
                '@register("other.nope")\n'
                'async def bad(params, user_slug):\n'
                '    return "bad"\n'
            ),
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))

        _discover_external()

        assert 'foo.ok' not in list_python_skills()
        assert 'other.nope' not in list_python_skills()

    def test_broken_integration_does_not_stop_siblings(
        self, tmp_path, monkeypatch, isolated_registry, cleanup_external_modules, caplog
    ):
        """An import error in one integration does not prevent others loading."""
        from marcel_core.config import settings

        _write_integration(
            tmp_path,
            'broken',
            'raise RuntimeError("intentionally broken habitat")\n',
        )
        _write_integration(
            tmp_path,
            'working',
            (
                'from marcel_core.plugin import register\n'
                '\n'
                '@register("working.ok")\n'
                'async def ok(params, user_slug):\n'
                '    return "ok"\n'
            ),
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))

        with caplog.at_level('ERROR', logger='marcel_core.skills.integrations'):
            _discover_external()

        assert 'working.ok' in list_python_skills()
        assert any('broken' in r.message for r in caplog.records)

    def test_habitat_without_init_is_skipped(
        self, tmp_path, monkeypatch, isolated_registry, cleanup_external_modules, caplog
    ):
        """Directories without __init__.py log a warning and are skipped."""
        from marcel_core.config import settings

        (tmp_path / 'integrations' / 'orphan').mkdir(parents=True)
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))

        with caplog.at_level('WARNING', logger='marcel_core.skills.integrations'):
            _discover_external()

        assert any('orphan' in r.message and '__init__.py' in r.message for r in caplog.records)

    def test_missing_integrations_dir_is_noop(self, tmp_path, monkeypatch, isolated_registry, cleanup_external_modules):
        """``MARCEL_ZOO_DIR`` set but no ``integrations/`` subdir is a silent no-op."""
        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))

        _discover_external()  # must not raise

    def test_unset_zoo_dir_is_noop(self, monkeypatch, isolated_registry, cleanup_external_modules):
        """``MARCEL_ZOO_DIR`` unset is a silent no-op — kernel ships no habitats."""
        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'marcel_zoo_dir', None)

        _discover_external()  # must not raise; nothing loaded

    def test_dotfile_and_underscore_dirs_are_skipped(
        self, tmp_path, monkeypatch, isolated_registry, cleanup_external_modules
    ):
        """Directories starting with ``.`` or ``_`` are ignored."""
        from marcel_core.config import settings

        _write_integration(
            tmp_path,
            '_private',
            'from marcel_core.plugin import register\n',
        )
        (tmp_path / 'integrations' / '.hidden').mkdir()
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))

        _discover_external()

        # No sys.modules entry should have been created for either.
        assert f'{_EXTERNAL_MODULE_PREFIX}._private' not in sys.modules
        assert f'{_EXTERNAL_MODULE_PREFIX}..hidden' not in sys.modules

    def test_discover_external_is_idempotent(self, tmp_path, monkeypatch, isolated_registry, cleanup_external_modules):
        """Calling _discover_external twice does not raise 'already registered'."""
        from marcel_core.config import settings

        _write_integration(
            tmp_path,
            'idemtest',
            (
                'from marcel_core.plugin import register\n'
                '\n'
                '@register("idemtest.hit")\n'
                'async def hit(params, user_slug):\n'
                '    return "hit"\n'
            ),
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))

        _discover_external()
        _discover_external()  # second call must not raise

        assert 'idemtest.hit' in list_python_skills()
