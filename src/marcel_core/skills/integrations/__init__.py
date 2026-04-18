"""Pluggable integration modules for the ``integration`` tool.

Each integration module defines async handler functions decorated with
:func:`register`.  At import time the decorator adds the function to a
global registry keyed by dotted skill name (e.g. ``"icloud.calendar"``).

Discovery is automatic. :func:`discover` imports:

1. Every sibling module in this package (first-party integrations shipped
   inside ``marcel_core``).
2. Every integration habitat directory under ``<data_root>/integrations/``
   — external, data-root-sourced integrations, the marcel-zoo pattern
   (ISSUE-3c87dd).

External habitats are packages: a directory with its own ``__init__.py``
that calls ``@register`` at import time. The directory name must match
the ``family`` segment of every handler name it registers — e.g. an
integration at ``<data_root>/integrations/docker/`` may register
``docker.list`` but **not** ``container.start``. Handlers that violate
this are rejected and the whole integration is rolled back (no partial
registrations leak into the registry).

Errors in one external integration never abort discovery of its siblings
— the failure is logged, the integration is disabled, and the rest load
normally.

Usage in an integration module::

    from marcel_core.plugin import register

    @register("myservice.action")
    async def action(params: dict, user_slug: str) -> str:
        ...
        return "result text"
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import pkgutil
import re
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

log = logging.getLogger(__name__)

# Handler signature: (params: dict[str, str], user_slug: str) -> str
IntegrationHandler = Callable[[dict, str], Awaitable[str]]

# Global registry: skill_name -> handler function
_registry: dict[str, IntegrationHandler] = {}

# Skill names must follow the ``family.action`` convention: two dot-separated
# segments, each containing only lowercase letters, digits, and underscores.
# Matches the same pattern enforced in registry.py.
_SKILL_NAME_PATTERN: re.Pattern[str] = re.compile(r'^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$')

# Prefix used for sys.modules entries of dynamically-loaded external
# integrations. Kept private so it cannot collide with a future real
# top-level package.
_EXTERNAL_MODULE_PREFIX = '_marcel_ext_integrations'


def register(skill_name: str) -> Callable[[IntegrationHandler], IntegrationHandler]:
    """Decorator that registers an async handler under *skill_name*.

    Raises ``ValueError`` if:
    - the name is already registered (prevents silent overwrites from duplicate imports).
    - the name does not match the ``family.action`` convention.

    Valid names are two dot-separated lowercase segments, e.g. ``"icloud.calendar"``.
    Each segment may contain letters, digits, and underscores.
    """
    if not _SKILL_NAME_PATTERN.match(skill_name):
        raise ValueError(
            f"Invalid skill name '{skill_name}'. "
            "Skill names must follow the 'family.action' convention: two dot-separated "
            'segments using only lowercase letters, digits, and underscores '
            "(e.g. 'icloud.calendar', 'banking.balance')."
        )

    def decorator(fn: IntegrationHandler) -> IntegrationHandler:
        if skill_name in _registry:
            raise ValueError(f"Integration skill '{skill_name}' is already registered")
        _registry[skill_name] = fn
        return fn

    return decorator


def get_handler(skill_name: str) -> IntegrationHandler:
    """Return the handler for *skill_name*, or raise ``KeyError``."""
    try:
        return _registry[skill_name]
    except KeyError:
        raise KeyError(f"No python integration registered for '{skill_name}'") from None


def list_python_skills() -> list[str]:
    """Return all registered python integration skill names."""
    return sorted(_registry.keys())


def discover() -> None:
    """Discover first-party and external integrations.

    First-party modules are siblings of this package. External habitats
    live at ``<data_root>/integrations/<name>/`` and are loaded via
    :func:`_discover_external`. Safe to call multiple times — already-
    imported modules are skipped by Python's import machinery.
    """
    _discover_builtin()
    _discover_external()


def _discover_builtin() -> None:
    """Import all sibling modules to trigger ``@register`` decorators."""
    package_path = __path__
    for info in pkgutil.iter_modules(package_path):
        if info.name.startswith('_'):
            continue
        module_name = f'{__name__}.{info.name}'
        try:
            importlib.import_module(module_name)
        except Exception:
            log.exception('Failed to import integration module %s', module_name)


def _discover_external() -> None:
    """Import external integration habitats from ``<data_root>/integrations/``.

    Each subdirectory is loaded as a package. See :func:`_load_external_integration`
    for the per-package loading contract (namespace enforcement, error isolation).
    """
    try:
        from marcel_core.config import settings

        external_dir = settings.data_dir / 'integrations'
    except Exception:
        log.exception('Failed to resolve data_dir for external integration discovery')
        return

    if not external_dir.is_dir():
        return

    for entry in sorted(external_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith(('_', '.')):
            continue
        _load_external_integration(entry)


def _load_external_integration(pkg_dir: Path) -> None:
    """Load one external integration package from *pkg_dir*.

    The directory name must match the ``family`` segment of every handler
    name registered by the package. Handlers outside that namespace cause
    the entire integration to be rolled back — no partial state leaks
    into the registry.

    Errors are logged and contained; the caller continues with the next
    integration regardless of what happens here.
    """
    init_py = pkg_dir / '__init__.py'
    if not init_py.exists():
        log.warning(
            "Integration habitat '%s' has no __init__.py — skipping",
            pkg_dir.name,
        )
        return

    module_name = f'{_EXTERNAL_MODULE_PREFIX}.{pkg_dir.name}'

    # Idempotency — discovery is called from multiple entry points and
    # must behave like the built-in path, which benefits from Python's
    # import cache automatically.
    if module_name in sys.modules:
        return

    before = set(_registry)

    try:
        spec = importlib.util.spec_from_file_location(
            module_name,
            init_py,
            submodule_search_locations=[str(pkg_dir)],
        )
        if spec is None or spec.loader is None:
            log.error(
                "Could not create module spec for integration habitat '%s'",
                pkg_dir.name,
            )
            return

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        added = set(_registry) - before
        invalid = [name for name in added if not name.startswith(f'{pkg_dir.name}.')]
        if invalid:
            for name in added:
                _registry.pop(name, None)
            sys.modules.pop(module_name, None)
            log.error(
                "Integration habitat '%s' registered handlers outside its namespace: %s. "
                "All handler names must start with '%s.'. Integration disabled.",
                pkg_dir.name,
                sorted(invalid),
                pkg_dir.name,
            )
    except Exception:
        for name in set(_registry) - before:
            _registry.pop(name, None)
        sys.modules.pop(module_name, None)
        log.exception(
            "Failed to load integration habitat '%s'",
            pkg_dir.name,
        )
