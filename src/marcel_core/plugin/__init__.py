"""Marcel plugin API — stable surface for external habitats.

External zoo habitats (integrations, skills, channels, jobs, agents) should
import *exclusively* from this package. Anything re-exported here is a
stability promise: it will not break between Marcel versions without a
matching migration note. Anything **not** re-exported here is internal and
may change at any time — zoo code that reaches past this surface owns its
own breakage.

The surface currently covers the **integration habitat** only: decorator,
handler type, module logger helper. Other habitat types (skills, channels,
jobs, agents) will add their surfaces here as their plugin plumbing lands
(see ISSUE-2ccc10, ISSUE-7d6b3f, ISSUE-a7d69a).

Example — minimal external integration at
``<data_root>/integrations/demo/__init__.py``::

    from marcel_core.plugin import register, get_logger

    log = get_logger(__name__)

    @register("demo.ping")
    async def ping(params: dict, user_slug: str) -> str:
        log.info("demo.ping called for %s", user_slug)
        return "pong"
"""

from __future__ import annotations

import logging

from marcel_core.skills.integrations import IntegrationHandler, register

__all__ = ['IntegrationHandler', 'get_logger', 'register']


def get_logger(name: str) -> logging.Logger:
    """Return a logger for a plugin module.

    Prefer this over a raw ``logging.getLogger`` so the kernel can later
    apply plugin-specific filtering or formatting without requiring every
    plugin to be rewritten.
    """
    return logging.getLogger(name)
