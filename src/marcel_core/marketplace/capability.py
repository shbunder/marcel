"""Marketplace tool-bundle capability (FEAT-260721-51f9e3).

Domain-owns-factory: the marketplace domain declares its admin-only tool.
Installing a habitat is installing software — admin tier (three-state
lifecycle, FEAT-260718-210a5f). Attached by ``composition`` only inside the
admin block, so non-admins never see it (structural role gate).
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic_ai.capabilities import Capability

from marcel_core.harness.context import MarcelDeps
from marcel_core.marketplace import tool as marketplace_tools

MARKETPLACE_TOOLS: list[tuple[str, Callable, str | None]] = [
    ('marketplace', marketplace_tools.marketplace, 'admin'),
]


def build_marketplace_tool_capability(tool_filter: set[str] | None) -> Capability[MarcelDeps] | None:
    """Admin marketplace bundle, narrowed by filter (None when empty)."""
    from marcel_core.tools.capability import build_tool_bundle

    return build_tool_bundle('marketplace-tools', 'Habitat marketplace management.', MARKETPLACE_TOOLS, tool_filter)
