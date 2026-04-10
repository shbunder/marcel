"""Relevance-based memory selection via fast model side-query.

This module re-exports the canonical implementation from
:mod:`marcel_core.memory.selector` for backward compatibility with
the v1 agent path (``agent/context.py``).

The canonical implementation uses a pydantic-ai Agent to ask a fast
model (Haiku) to pick the most relevant memory files for a query.
"""

from __future__ import annotations

from marcel_core.memory.selector import (
    MAX_SELECTED,
    SELECTION_THRESHOLD,
    select_relevant_memories,
)

__all__ = [
    'MAX_SELECTED',
    'SELECTION_THRESHOLD',
    'select_relevant_memories',
]
