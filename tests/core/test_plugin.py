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
    _metadata,
    _registry,
    get_handler,
    get_integration_metadata,
    list_integrations,
    list_python_skills,
)


@pytest.fixture
def isolated_registry(monkeypatch):
    """Give each test a fresh integration + metadata registry, restored on teardown."""
    saved_registry = dict(_registry)
    saved_metadata = dict(_metadata)
    monkeypatch.setattr('marcel_core.skills.integrations._registry', {})
    monkeypatch.setattr('marcel_core.skills.integrations._metadata', {})
    yield
    _registry.clear()
    _registry.update(saved_registry)
    _metadata.clear()
    _metadata.update(saved_metadata)


@pytest.fixture
def cleanup_external_modules():
    """Remove any ``_marcel_ext_integrations.*`` entries created during a test."""
    yield
    for name in list(sys.modules):
        if name.startswith(_EXTERNAL_MODULE_PREFIX):
            sys.modules.pop(name, None)


def _write_integration(root: Path, name: str, body: str, *, yaml: str | None = None) -> Path:
    """Materialize an external integration package under ``root/integrations/``.

    Optionally writes ``integration.yaml`` alongside ``__init__.py`` when
    *yaml* is provided.
    """
    pkg = root / 'integrations' / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / '__init__.py').write_text(body, encoding='utf-8')
    if yaml is not None:
        (pkg / 'integration.yaml').write_text(yaml, encoding='utf-8')
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


_VALID_HANDLER_BODY = (
    'from marcel_core.plugin import register\n'
    '\n'
    '@register("metatest.ping")\n'
    'async def ping(params, user_slug):\n'
    '    return "pong"\n'
)


class TestIntegrationMetadata:
    def test_valid_yaml_populates_metadata(
        self, tmp_path, monkeypatch, isolated_registry, cleanup_external_modules
    ):
        """A valid integration.yaml is parsed and exposed via get_integration_metadata."""
        from marcel_core.config import settings

        _write_integration(
            tmp_path,
            'metatest',
            _VALID_HANDLER_BODY,
            yaml=(
                'name: metatest\n'
                'description: Test integration\n'
                'provides:\n'
                '  - metatest.ping\n'
                'requires:\n'
                '  env:\n'
                '    - METATEST_TOKEN\n'
            ),
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))

        _discover_external()

        meta = get_integration_metadata('metatest')
        assert meta is not None
        assert meta.name == 'metatest'
        assert meta.description == 'Test integration'
        assert meta.provides == ['metatest.ping']
        assert meta.requires == {'env': ['METATEST_TOKEN']}
        assert 'metatest' in list_integrations()

    def test_missing_yaml_logs_warning_no_metadata(
        self, tmp_path, monkeypatch, isolated_registry, cleanup_external_modules, caplog
    ):
        """Habitat with no integration.yaml works but registers no metadata + logs a warning."""
        from marcel_core.config import settings

        _write_integration(tmp_path, 'metatest', _VALID_HANDLER_BODY)
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))

        with caplog.at_level('WARNING', logger='marcel_core.skills.integrations'):
            _discover_external()

        assert get_integration_metadata('metatest') is None
        assert 'metatest.ping' in list_python_skills()  # handler still works
        assert any('integration.yaml' in r.message for r in caplog.records)

    def test_invalid_yaml_logs_error_no_metadata(
        self, tmp_path, monkeypatch, isolated_registry, cleanup_external_modules, caplog
    ):
        """Malformed YAML logs an error but the handler still loads."""
        from marcel_core.config import settings

        _write_integration(
            tmp_path,
            'metatest',
            _VALID_HANDLER_BODY,
            yaml='name: metatest\n  bad-indent: [unbalanced\n',
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))

        with caplog.at_level('ERROR', logger='marcel_core.skills.integrations'):
            _discover_external()

        assert get_integration_metadata('metatest') is None
        assert 'metatest.ping' in list_python_skills()
        assert any('integration.yaml' in r.message and 'metatest' in r.message for r in caplog.records)

    def test_name_mismatch_rejects_metadata(
        self, tmp_path, monkeypatch, isolated_registry, cleanup_external_modules, caplog
    ):
        """integration.yaml whose name differs from the directory name is rejected."""
        from marcel_core.config import settings

        _write_integration(
            tmp_path,
            'metatest',
            _VALID_HANDLER_BODY,
            yaml='name: not_metatest\nprovides: [metatest.ping]\n',
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))

        with caplog.at_level('ERROR', logger='marcel_core.skills.integrations'):
            _discover_external()

        assert get_integration_metadata('metatest') is None
        assert get_integration_metadata('not_metatest') is None
        assert any('match directory name' in r.message for r in caplog.records)

    def test_provides_outside_namespace_rejects_metadata(
        self, tmp_path, monkeypatch, isolated_registry, cleanup_external_modules, caplog
    ):
        """integration.yaml listing handlers outside its namespace is rejected."""
        from marcel_core.config import settings

        _write_integration(
            tmp_path,
            'metatest',
            _VALID_HANDLER_BODY,
            yaml=('name: metatest\nprovides:\n  - metatest.ping\n  - other.nope\n'),
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))

        with caplog.at_level('ERROR', logger='marcel_core.skills.integrations'):
            _discover_external()

        assert get_integration_metadata('metatest') is None
        assert any('outside its namespace' in r.message for r in caplog.records)

    def test_unknown_requires_keys_warn_but_register(
        self, tmp_path, monkeypatch, isolated_registry, cleanup_external_modules, caplog
    ):
        """Unknown requires keys log a warning but metadata is still registered."""
        from marcel_core.config import settings

        _write_integration(
            tmp_path,
            'metatest',
            _VALID_HANDLER_BODY,
            yaml=(
                'name: metatest\n'
                'provides: [metatest.ping]\n'
                'requires:\n'
                '  env: [X]\n'
                '  unknown_key: [Y]\n'
            ),
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))

        with caplog.at_level('WARNING', logger='marcel_core.skills.integrations'):
            _discover_external()

        meta = get_integration_metadata('metatest')
        assert meta is not None
        assert meta.requires == {'env': ['X'], 'unknown_key': ['Y']}
        assert any('unknown_key' in r.message for r in caplog.records)
