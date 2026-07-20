"""RETIRED — the toolkit habitat became the connector habitat (FEAT-260718-c232d9).

Toolkits were in-process handlers behind one ``toolkit(id=…)`` dispatcher.
Integrations are now **connectors** — MCP servers with per-user auth
(``docs/connectors.md``). This module survives one release as an import shim
(FR4) so a straggler extension fails soft with directions, not an ImportError.


Removal trigger: no in-tree code uses ``@marcel_tool`` (verified); this
shim exists only so an out-of-tree zoo extension fails soft for one
release. Delete it once the marcel-zoo has no ``@marcel_tool`` importers
(tracked as the next SDK-surface cleanup).
"""

from __future__ import annotations

import warnings
from collections.abc import Awaitable, Callable

ToolkitHandler = Callable[[dict, str], Awaitable[str]]


def marcel_tool(name: str) -> Callable[[ToolkitHandler], ToolkitHandler]:
    """Deprecated no-op decorator. Write a connector instead (docs/connectors.md)."""

    def decorator(fn: ToolkitHandler) -> ToolkitHandler:
        warnings.warn(
            f'@marcel_tool({name!r}) is retired: the toolkit habitat became the connector '
            'habitat (FEAT-260718-c232d9). The handler was NOT registered — port it to a '
            'connector; see docs/connectors.md.',
            DeprecationWarning,
            stacklevel=2,
        )
        return fn

    return decorator


__all__ = ['ToolkitHandler', 'marcel_tool']
