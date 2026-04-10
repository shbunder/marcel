"""Skills registry — loads skills.json and merges python integrations.

The registry is loaded once and cached for the lifetime of the process.
Call :func:`reload` to force a refresh (e.g., after hot-loading a new
integration module at runtime).
"""

import json
from pathlib import Path

from marcel_core.skills.integrations import discover, list_python_skills

_SKILLS_JSON = Path(__file__).parent / 'skills.json'

_cache: dict[str, dict] | None = None


def _load() -> dict[str, dict]:
    """Load skills.json and merge in python integration entries (cached)."""
    global _cache
    if _cache is not None:
        return _cache

    registry: dict[str, dict] = json.loads(_SKILLS_JSON.read_text(encoding='utf-8'))

    # Auto-discover python integration modules and add them to the registry.
    discover()
    for name in list_python_skills():
        if name not in registry:
            registry[name] = {'type': 'python', 'handler': name}

    _cache = registry
    return registry


def reload() -> None:
    """Invalidate the registry cache so the next access reloads from disk."""
    global _cache
    _cache = None


def get_skill(name: str) -> dict:
    """Return the config dict for `name`, or raise KeyError with a clear message."""
    registry = _load()
    if name not in registry:
        available = list(registry)
        raise KeyError(
            f"Unknown skill '{name}'." + (f' Available: {available}' if available else ' No skills are registered yet.')
        )
    return registry[name]


def list_skills() -> list[str]:
    """Return all registered skill names."""
    return list(_load().keys())
