"""Marcel plugin API — stable surface for external habitats.

External zoo habitats (integrations, skills, channels, jobs, agents) should
import *exclusively* from this package. Anything re-exported here is a
stability promise: it will not break between Marcel versions without a
matching migration note. Anything **not** re-exported here is internal and
may change at any time — zoo code that reaches past this surface owns its
own breakage.

Surface today (integration habitat focus):

- :func:`register`, :data:`IntegrationHandler`, :func:`get_logger` — declare
  and log from a handler.
- :mod:`marcel_core.plugin.credentials` — encrypted per-user credential
  load/save (used by zoo banking + icloud habitats).
- :mod:`marcel_core.plugin.paths` — per-user data and cache directories,
  user enumeration (banking sync, news cache).
- :mod:`marcel_core.plugin.models` — model registry + per-channel model
  preference (settings habitat).
- :mod:`marcel_core.plugin.rss` — RSS/Atom feed fetcher (news habitat).

Other habitat types (skills, channels, jobs, agents) will add their
surfaces here as their plugin plumbing lands (see ISSUE-2ccc10,
ISSUE-7d6b3f, ISSUE-a7d69a).

Example — minimal external integration at
``<MARCEL_ZOO_DIR>/integrations/demo/__init__.py``::

    from marcel_core.plugin import register, get_logger
    from marcel_core.plugin import credentials, paths

    log = get_logger(__name__)

    @register("demo.ping")
    async def ping(params: dict, user_slug: str) -> str:
        log.info("demo.ping called for %s", user_slug)
        api_key = credentials.load(user_slug).get("DEMO_API_KEY")
        cache = paths.cache_dir(user_slug) / "demo.json"
        return "pong"
"""

from __future__ import annotations

import logging

from marcel_core.plugin import credentials, models, paths, rss
from marcel_core.skills.integrations import IntegrationHandler, register

__all__ = [
    'IntegrationHandler',
    'credentials',
    'get_logger',
    'models',
    'paths',
    'register',
    'rss',
]


def get_logger(name: str) -> logging.Logger:
    """Return a logger for a plugin module.

    Prefer this over a raw ``logging.getLogger`` so the kernel can later
    apply plugin-specific filtering or formatting without requiring every
    plugin to be rewritten.
    """
    return logging.getLogger(name)
