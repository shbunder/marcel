"""Skills registry — loads skills.json and merges python integrations.

The registry is loaded once and cached for the lifetime of the process.
Call :func:`reload` to force a refresh (e.g., after hot-loading a new
integration module at runtime).

The registry **auto-reloads** when ``skills.json`` is modified on disk:
each call to :func:`_load` compares the file's mtime against the value
recorded at the last load and invalidates the cache if they differ.
This means dropping a new JSON skill into ``skills.json`` is picked up
on the next request without a server restart.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from marcel_core.skills.integrations import discover, list_python_skills

log = logging.getLogger(__name__)

_SKILLS_JSON = Path(__file__).parent / 'skills.json'

# Skill names must follow the ``family.action`` convention: two dot-separated
# segments, each containing only lowercase letters, digits, and underscores.
SKILL_NAME_PATTERN: re.Pattern[str] = re.compile(r'^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$')

_cache: dict[str, SkillConfig] | None = None
_cache_mtime: float | None = None


@dataclass
class SkillConfig:
    """Typed configuration for a registered skill.

    Replaces the bare ``dict`` that the registry previously returned.
    Fields map directly to the JSON keys in ``skills.json``; unknown keys
    in the raw JSON are silently ignored so forward-compatibility is
    preserved.
    """

    type: Literal['python', 'http', 'shell'] = 'http'
    handler: str | None = None
    """Python handler name — only set when ``type == 'python'``."""
    url: str | None = None
    """Request URL — only set when ``type == 'http'``."""
    method: str = 'GET'
    """HTTP method — only used when ``type == 'http'``."""
    auth: dict = field(default_factory=dict)
    """Auth config dict (keys: type, env_var, location, header_name, param_name, provider)."""
    params: dict = field(default_factory=dict)
    """Parameter spec dict (used by HTTP and shell skills for argument resolution)."""
    response_transform: str = ''
    """Optional transform expression applied to the HTTP response, e.g. ``jq:.data``."""
    command: str | None = None
    """Shell command template with ``{param}`` placeholders — only for ``type == 'shell'``."""

    @classmethod
    def from_dict(cls, d: dict) -> SkillConfig:
        """Build a :class:`SkillConfig` from a raw JSON dict."""
        return cls(
            type=d.get('type', 'http'),
            handler=d.get('handler'),
            url=d.get('url'),
            method=d.get('method', 'GET'),
            auth=d.get('auth', {}),
            params=d.get('params', {}),
            response_transform=d.get('response_transform', ''),
            command=d.get('command'),
        )


def _load() -> dict[str, SkillConfig]:
    """Load skills.json and merge in python integration entries (cached).

    Auto-invalidates the cache if ``skills.json`` has been modified on
    disk since the last load, so JSON skill changes take effect without
    a restart.
    """
    global _cache, _cache_mtime

    # Auto-invalidate when skills.json is modified on disk.
    try:
        current_mtime = _SKILLS_JSON.stat().st_mtime
        if _cache is not None and _cache_mtime != current_mtime:
            log.info('skills.json modified on disk — reloading registry')
            _cache = None
    except OSError:
        pass

    if _cache is not None:
        return _cache

    raw: dict = json.loads(_SKILLS_JSON.read_text(encoding='utf-8'))
    registry: dict[str, SkillConfig] = {}

    for name, raw_cfg in raw.items():
        if not SKILL_NAME_PATTERN.match(name):
            log.warning('Skipping skill with invalid name in skills.json: %r (must be family.action)', name)
            continue
        registry[name] = SkillConfig.from_dict(raw_cfg)

    # Auto-discover python integration modules and add them to the registry.
    discover()
    for name in list_python_skills():
        if name not in registry:
            if not SKILL_NAME_PATTERN.match(name):
                log.warning('Skipping python integration with invalid name: %r', name)
                continue
            registry[name] = SkillConfig(type='python', handler=name)

    try:
        _cache_mtime = _SKILLS_JSON.stat().st_mtime
    except OSError:
        _cache_mtime = None

    _cache = registry
    return registry


def reload() -> None:
    """Invalidate the registry cache so the next access reloads from disk."""
    global _cache, _cache_mtime
    _cache = None
    _cache_mtime = None


def get_skill(name: str) -> SkillConfig:
    """Return the :class:`SkillConfig` for *name*, or raise ``KeyError`` with a clear message."""
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
