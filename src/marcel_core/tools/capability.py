"""Tool-bundle capabilities for the general tool modules (FEAT-260721-51f9e3).

Domain-owns-factory (ADR-260720-f23f23): this module declares the dev and
utility tool tiers and packages them as pydantic-ai ``Capability`` bundles.
``harness/agent.py`` aggregates the declarations into ``_TOOL_REGISTRY`` (the
role-gate source of truth feeding ``admin_tool_names()``); ``composition``
attaches the bundles — dev tools only inside the admin block, so the
structural role gate stays "the tool is never registered", exactly as before.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic_ai.capabilities import Capability

from marcel_core.harness.context import MarcelDeps
from marcel_core.tools import (
    charts as chart_tools,
    claude_code as claude_code_tool,
    core as core_tools,
    marcel as marcel_tools,
)

# (name, callable, role_required) — same triple shape _TOOL_REGISTRY always
# used; ``'admin'`` restricts, ``None`` is available to every role.
DEV_TOOLS: list[tuple[str, Callable, str | None]] = [
    ('git_status', core_tools.git_status, 'admin'),
    ('git_diff', core_tools.git_diff, 'admin'),
    ('git_log', core_tools.git_log, 'admin'),
    ('git_add', core_tools.git_add, 'admin'),
    ('git_commit', core_tools.git_commit, 'admin'),
    ('git_push', core_tools.git_push, 'admin'),
    ('claude_code', claude_code_tool.claude_code, 'admin'),
]

UTILITY_TOOLS: list[tuple[str, Callable, str | None]] = [
    ('generate_chart', chart_tools.generate_chart, None),
    ('marcel', marcel_tools.marcel, None),
]


def build_tool_bundle(
    bundle_id: str,
    description: str,
    tools: list[tuple[str, Callable, str | None]],
    role: str,
    tool_filter: set[str] | None,
) -> Capability[MarcelDeps] | None:
    """Package a declared tool list as a ``Capability``, gated and narrowed.

    The ``role_required`` column of each triple is the layer-1 structural
    gate (role-gating rule): an ``'admin'``-declared tool is never selected
    for a non-admin build, even if allowlisted, and even if a future
    declaration lands in an otherwise all-user list. Composition's admin
    block placement is defense-in-depth on top, not the gate itself.
    Returns ``None`` when nothing survives, so composition can skip
    attaching an empty bundle (the explain tier's empty filter, a
    subagent's narrow allowlist).
    """
    selected = [
        fn
        for name, fn, required in tools
        if (required != 'admin' or role == 'admin') and (tool_filter is None or name in tool_filter)
    ]
    if not selected:
        return None
    return Capability(id=bundle_id, description=description, tools=selected, defer_loading=False)


def build_dev_tool_capability(role: str, tool_filter: set[str] | None) -> Capability[MarcelDeps] | None:
    """Admin dev bundle: git_* + claude_code."""
    return build_tool_bundle('dev-tools', 'Git and Claude Code developer tools.', DEV_TOOLS, role, tool_filter)


def build_utility_tool_capability(role: str, tool_filter: set[str] | None) -> Capability[MarcelDeps] | None:
    """All-user utility bundle: generate_chart + the marcel dispatcher."""
    return build_tool_bundle(
        'utility-tools', 'Chart rendering and the marcel utility.', UTILITY_TOOLS, role, tool_filter
    )
