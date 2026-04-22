"""Back-compat tests for ISSUE-3c1534 Phase 1.

Verifies the migration aliases still work so external zoo forks that
haven't yet migrated keep loading cleanly. All of these assertions flip
to REJECT in Phase 5 when the aliases are removed.

Covers:
- ``from marcel_core.plugin import register`` — old decorator name
- ``from marcel_core.skills.integrations import ...`` — old module path
- ``IntegrationHandler`` / ``IntegrationMetadata`` type names
- ``<zoo>/integrations/`` directory discovery
- ``integration.yaml`` filename discovery
- The ``integration`` agent-facing tool alias
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from marcel_core.toolkit import _metadata, _registry


@pytest.fixture
def isolated_registry(monkeypatch):
    """Give each test a fresh registry, restored on teardown."""
    saved_registry = dict(_registry)
    saved_metadata = dict(_metadata)
    monkeypatch.setattr('marcel_core.toolkit._registry', {})
    monkeypatch.setattr('marcel_core.toolkit._metadata', {})
    yield
    _registry.clear()
    _registry.update(saved_registry)
    _metadata.clear()
    _metadata.update(saved_metadata)


# ---------------------------------------------------------------------------
# Decorator alias: @register still works
# ---------------------------------------------------------------------------


def test_register_decorator_is_available_from_plugin(isolated_registry):
    """Zoo habitats importing ``from marcel_core.plugin import register`` still work."""
    from marcel_core.plugin import register

    @register('backcompat.legacy')
    async def legacy(params, user_slug):
        return 'ok'

    from marcel_core.toolkit import get_handler, list_tools

    assert 'backcompat.legacy' in list_tools()
    assert get_handler('backcompat.legacy') is legacy


def test_register_is_same_function_as_marcel_tool():
    """``register`` is the exact same callable as ``marcel_tool`` — not a wrapper."""
    from marcel_core.plugin import marcel_tool, register

    assert register is marcel_tool


# ---------------------------------------------------------------------------
# Module path shim: marcel_core.skills.integrations still imports
# ---------------------------------------------------------------------------


def test_old_module_path_imports_cleanly():
    """``from marcel_core.skills.integrations import register`` works via the shim."""
    import marcel_core.skills.integrations as shim

    assert hasattr(shim, 'register')
    assert hasattr(shim, 'marcel_tool')
    assert hasattr(shim, 'discover')
    assert hasattr(shim, '_registry')
    assert hasattr(shim, '_metadata')
    # Shim and target share the same objects (not copies)
    from marcel_core.toolkit import _registry, discover

    assert shim.discover is discover
    assert shim._registry is _registry


def test_integration_handler_alias_still_resolves():
    """``IntegrationHandler`` and ``IntegrationMetadata`` type aliases work."""
    from marcel_core.toolkit import (
        IntegrationHandler,
        IntegrationMetadata,
        ToolkitHandler,
        ToolkitMetadata,
    )

    assert IntegrationHandler is ToolkitHandler
    assert IntegrationMetadata is ToolkitMetadata


# ---------------------------------------------------------------------------
# Dual directory discovery: <zoo>/integrations/ still walked
# ---------------------------------------------------------------------------


def _write_habitat(root: Path, name: str, yaml_filename: str) -> None:
    """Create a minimal habitat at *root*/integrations/<name>/ or *root*/toolkit/<name>/."""
    hab = root / name
    hab.mkdir(parents=True, exist_ok=True)
    (hab / '__init__.py').write_text(
        textwrap.dedent(
            f"""\
            from marcel_core.plugin import register

            @register("{name}.ping")
            async def ping(params, user_slug):
                return "pong from {name}"
            """
        )
    )
    (hab / yaml_filename).write_text(
        textwrap.dedent(
            f"""\
            name: {name}
            description: back-compat fixture
            provides:
              - {name}.ping
            """
        )
    )


def test_legacy_integrations_directory_is_discovered(tmp_path, monkeypatch, isolated_registry, caplog):
    """A zoo using the old ``integrations/`` directory name still loads."""
    zoo = tmp_path / 'zoo'
    (zoo / 'integrations').mkdir(parents=True)
    _write_habitat(zoo / 'integrations', 'legacy_dir', 'toolkit.yaml')

    from marcel_core.config import settings
    from marcel_core.toolkit import discover

    monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))

    from marcel_core.toolkit import list_tools

    with caplog.at_level('WARNING', logger='marcel_core.toolkit'):
        discover()

    assert 'legacy_dir.ping' in list_tools(), 'habitat in integrations/ was not discovered'
    # The directory-deprecation warning fires once per kernel boot.
    assert any('integrations/' in r.message and 'deprecated' in r.message for r in caplog.records), (
        'expected a deprecation warning about the integrations/ directory'
    )


def test_legacy_integration_yaml_is_read(tmp_path, monkeypatch, isolated_registry, caplog):
    """A habitat that ships ``integration.yaml`` (not toolkit.yaml) still publishes metadata."""
    zoo = tmp_path / 'zoo'
    (zoo / 'toolkit').mkdir(parents=True)
    _write_habitat(zoo / 'toolkit', 'legacy_yaml', 'integration.yaml')

    from marcel_core.config import settings
    from marcel_core.toolkit import discover, get_toolkit_metadata, list_toolkits

    monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))

    with caplog.at_level('WARNING', logger='marcel_core.toolkit'):
        discover()

    assert 'legacy_yaml' in list_toolkits(), 'integration.yaml was not parsed'
    meta = get_toolkit_metadata('legacy_yaml')
    assert meta is not None
    assert meta.provides == ['legacy_yaml.ping']
    assert any('integration.yaml' in r.message for r in caplog.records), (
        'expected a per-habitat deprecation warning for the old YAML filename'
    )


def test_toolkit_wins_over_integrations_on_name_collision(tmp_path, monkeypatch, isolated_registry):
    """If a habitat name appears in both directories, ``toolkit/`` wins (higher precedence)."""
    zoo = tmp_path / 'zoo'
    (zoo / 'toolkit').mkdir(parents=True)
    (zoo / 'integrations').mkdir(parents=True)

    # Two habitats with the same NAME, different handler return values.
    hab_toolkit = zoo / 'toolkit' / 'collide'
    hab_toolkit.mkdir()
    (hab_toolkit / '__init__.py').write_text(
        textwrap.dedent(
            """\
            from marcel_core.plugin import register

            @register("collide.ping")
            async def ping(params, user_slug):
                return "from-toolkit"
            """
        )
    )
    (hab_toolkit / 'toolkit.yaml').write_text('name: collide\nprovides: [collide.ping]\n')

    hab_integrations = zoo / 'integrations' / 'collide'
    hab_integrations.mkdir()
    (hab_integrations / '__init__.py').write_text(
        textwrap.dedent(
            """\
            from marcel_core.plugin import register

            @register("collide.ping")
            async def ping(params, user_slug):
                return "from-integrations"
            """
        )
    )
    (hab_integrations / 'integration.yaml').write_text('name: collide\nprovides: [collide.ping]\n')

    from marcel_core.config import settings
    from marcel_core.toolkit import discover, get_handler

    monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))
    discover()

    import asyncio

    handler = get_handler('collide.ping')

    async def _call() -> str:
        return await handler({}, 'alice')

    result = asyncio.run(_call())
    assert result == 'from-toolkit', f'expected toolkit/ to win, got: {result}'


# ---------------------------------------------------------------------------
# Tool alias: `integration(id=...)` still dispatches
# ---------------------------------------------------------------------------


def test_integration_tool_alias_forwards_to_toolkit(monkeypatch):
    """``integration`` tool is a thin alias over ``toolkit`` that logs deprecation."""
    from marcel_core.tools import toolkit as toolkit_tool

    # Reset the one-shot flag so we observe the log.
    monkeypatch.setattr(toolkit_tool, '_DEPRECATION_ALIAS_LOGGED', False)

    assert toolkit_tool.integration is not toolkit_tool.toolkit, (
        'integration and toolkit must be separate functions so the deprecation log fires'
    )
    # Both coroutine signatures must match.
    import inspect

    sig_old = inspect.signature(toolkit_tool.integration)
    sig_new = inspect.signature(toolkit_tool.toolkit)
    assert list(sig_old.parameters.keys()) == list(sig_new.parameters.keys())
