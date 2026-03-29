"""Skills registry — loads and validates skills.json."""

import json
from pathlib import Path

_SKILLS_JSON = Path(__file__).parent / 'skills.json'


def _load() -> dict[str, dict]:
    return json.loads(_SKILLS_JSON.read_text(encoding='utf-8'))


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
