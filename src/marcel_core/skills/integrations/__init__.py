"""Pluggable integration modules for the ``integration`` tool.

Each integration module defines async handler functions decorated with
:func:`register`.  At import time the decorator adds the function to a
global registry keyed by dotted skill name (e.g. ``"icloud.calendar"``).

Discovery is automatic: :func:`discover` imports every Python module in
this package so that ``@register`` calls execute and populate the registry.

Usage in an integration module::

    from marcel_core.skills.integrations import register

    @register("myservice.action")
    async def action(params: dict, user_slug: str) -> str:
        ...
        return "result text"
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

# Handler signature: (params: dict[str, str], user_slug: str) -> str
IntegrationHandler = Callable[[dict, str], Awaitable[str]]

# Global registry: skill_name -> handler function
_registry: dict[str, IntegrationHandler] = {}


def register(skill_name: str) -> Callable[[IntegrationHandler], IntegrationHandler]:
    """Decorator that registers an async handler under *skill_name*.

    Raises ``ValueError`` if the name is already registered (prevents
    silent overwrites from duplicate imports).
    """

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
    """Import all sibling modules to trigger ``@register`` decorators.

    Safe to call multiple times — already-imported modules are skipped
    by Python's import machinery.
    """
    package_path = __path__
    for info in pkgutil.iter_modules(package_path):
        if info.name.startswith('_'):
            continue
        module_name = f'{__name__}.{info.name}'
        try:
            importlib.import_module(module_name)
        except Exception:
            log.exception('Failed to import integration module %s', module_name)
