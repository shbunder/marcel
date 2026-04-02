"""Skills registry — loads skills.json and merges python integrations."""

import json
from pathlib import Path

from marcel_core.skills.integrations import discover, list_python_skills

_SKILLS_JSON = Path(__file__).parent / 'skills.json'


def _load() -> dict[str, dict]:
    """Load skills.json and merge in python integration entries."""
    registry: dict[str, dict] = json.loads(_SKILLS_JSON.read_text(encoding='utf-8'))

    # Auto-discover python integration modules and add them to the registry.
    discover()
    for name in list_python_skills():
        if name not in registry:
            registry[name] = {'type': 'python', 'handler': name}

    return registry


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
