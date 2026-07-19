"""Marcel plugin API — stable surface for external habitats.

External zoo habitats (integrations, skills, channels, jobs, agents) should
import *exclusively* from this package. Anything re-exported here is a
stability promise: it will not break between Marcel versions without a
matching migration note. Anything **not** re-exported here is internal and
may change at any time — zoo code that reaches past this surface owns its
own breakage.

Surface today:

- :func:`get_logger` — declare
  and log from a handler.
- :mod:`marcel_core.plugin.credentials` — encrypted per-user credential
  load/save (used by zoo banking + icloud habitats).
- :mod:`marcel_core.plugin.paths` — per-user data and cache directories,
  user enumeration (banking sync, news cache).
- :mod:`marcel_core.plugin.models` — model registry + per-channel model
  preference (settings habitat).
- :mod:`marcel_core.plugin.rss` — RSS/Atom feed fetcher (news habitat).

- :class:`ChannelPlugin`, :func:`register_channel`, :func:`get_channel`,
  :func:`list_channels` — channel habitat contract (see
  :mod:`marcel_core.plugin.channels`).

- :func:`discover_templates` — job template habitat loader (see
  :mod:`marcel_core.plugin.jobs`).

Other habitat types (skills, agents) will add their surfaces here as
their plugin plumbing lands (see ISSUE-2ccc10).

``ToolkitHandler`` and ``marcel_tool`` are still re-exported for exactly one
release as deprecation shims — the toolkit habitat retired with
FEAT-260718-c232d9 and ``@marcel_tool`` registers nothing (it warns and
returns the function unchanged). Executable integrations are connector
habitats now: a ``connector.yaml`` plus an MCP server under
``<MARCEL_ZOO_DIR>/connectors/<name>/`` — see docs/connectors.md for the
migration guide.
"""

from __future__ import annotations

import logging

from marcel_core.plugin import credentials, models, paths, rss
from marcel_core.plugin.channels import (
    ChannelPlugin,
    get_channel,
    list_channels,
    register_channel,
)
from marcel_core.plugin.jobs import discover_templates
from marcel_core.toolkit import (
    ToolkitHandler,
    marcel_tool,
)

__all__ = [
    'ChannelPlugin',
    'ToolkitHandler',
    'credentials',
    'discover_templates',
    'get_channel',
    'get_logger',
    'list_channels',
    'marcel_tool',
    'models',
    'paths',
    'register_channel',
    'rss',
]


def get_logger(name: str) -> logging.Logger:
    """Return a logger for a plugin module.

    Prefer this over a raw ``logging.getLogger`` so the kernel can later
    apply plugin-specific filtering or formatting without requiring every
    plugin to be rewritten.
    """
    return logging.getLogger(name)
