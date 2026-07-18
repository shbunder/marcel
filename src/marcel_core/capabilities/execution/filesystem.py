"""FileSystem capability, filterable to a subset of its tools.

The harness ``FileSystem`` is all-or-nothing — its toolset exposes the full
read/write/edit/list set. Marcel's delegated subagents historically request
individual file tools by name (``tools: [read_file, web]`` for a *read-only*
explorer), so attaching the whole capability would silently widen a
read-only agent to ``write_file``/``edit_file``. ``FilteredFileSystem``
restricts the produced toolset to a named allowlist, preserving the
role-gating rule's "keep allowlists tight" contract.

When ``allowed_tools`` is ``None`` the full toolset is exposed (the main
admin agent). When it is a set, only those tool names survive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.tools import ToolDefinition
from pydantic_ai_harness.filesystem import FileSystem


@dataclass
class FilteredFileSystem(FileSystem):
    """FileSystem whose toolset is narrowed to ``allowed_tools`` when set."""

    allowed_tools: frozenset[str] | None = field(default=None)

    def get_toolset(self) -> Any:
        toolset = super().get_toolset()
        if self.allowed_tools is None:
            return toolset
        allowed = self.allowed_tools

        def _keep(ctx: object, td: ToolDefinition) -> bool:
            return td.name in allowed

        return toolset.filtered(_keep)
