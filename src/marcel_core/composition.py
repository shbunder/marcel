"""The composition root — the one place capability lists are assembled.

Per ADR-260718-0cf8e8, an agent is a composition of capabilities and this
module is the only place that composition is built. Capability packages
under :mod:`marcel_core.capabilities` never import each other; they meet
here. The list grows as the FEAT-260718-* roadmap features land their
capabilities.
"""

from __future__ import annotations

from pydantic_ai.capabilities import AbstractCapability, Instrumentation
from pydantic_ai_harness.compaction import ClampOversizedMessages, ClearToolResults
from pydantic_ai_harness.overflowing_tool_output import (
    Band,
    OverflowingToolOutput,
    Spill,
    Truncate,
)
from pydantic_ai_harness.step_persistence import StepPersistence

from marcel_core.capabilities.persistence import persistence_store
from marcel_core.capabilities.persistence.overflow import PasteOverflowStore
from marcel_core.capabilities.policy import MarcelPolicy
from marcel_core.harness.context import MarcelDeps
from marcel_core.tracing import get_instrumentation_settings

# Compaction thresholds (FEAT-260718-ed6d63). Deliberately module constants,
# not settings fields: config.py sits behind the self-mod guard and these
# have no tuning consumer yet — promote them to env-configurable settings
# via the unlock flow when one appears.
COMPACTION_MAX_TOKENS = 30_000
"""ClearToolResults trigger — estimated tokens before old results blank."""
COMPACTION_KEEP_PAIRS = 3
"""Most-recent tool call/return pairs left untouched by clearing."""
CLAMP_MAX_PART_TOKENS = 50_000
"""Single-part ceiling guarding against runaway generations."""
OVERFLOW_SPILL_CHARS = 32_000
"""Tool returns above this spill to the paste store with a preview +
read_tool_result handle (falling back to truncation when no turn user is
stamped — jobs/subagents keep their pre-harness bounded behavior)."""


def build_capabilities() -> list[AbstractCapability[MarcelDeps]]:
    """Assemble the capability list for a Marcel agent.

    Today: the policy gate (always), step persistence (the store ignores
    runs without a Marcel conversation id — jobs, subagents, the explain
    tier), the compaction stack (clamp runaway parts first, then blank old
    tool results past the token trigger — replaces the pre-harness
    age-tier trimming), and instrumentation (when tracing is enabled).
    Later roadmap features append theirs here, keyed on role, channel, and
    tier as those axes become capability-relevant.
    """
    capabilities: list[AbstractCapability[MarcelDeps]] = [
        MarcelPolicy(),
        StepPersistence(store=persistence_store(), agent_name='marcel'),
        OverflowingToolOutput(
            bands=[Band(over=OVERFLOW_SPILL_CHARS, action=Spill(then=Truncate()))],
            store=PasteOverflowStore(),
        ),
        ClampOversizedMessages(max_part_tokens=CLAMP_MAX_PART_TOKENS),
        ClearToolResults(
            max_tokens=COMPACTION_MAX_TOKENS,
            keep_pairs=COMPACTION_KEEP_PAIRS,
            # `marcel` results carry read_skill docs that must stay visible
            # across turns (the read_skills priming depends on it).
            exclude_tools=frozenset({'marcel'}),
        ),
    ]
    instrumentation = get_instrumentation_settings()
    if instrumentation is not None:
        capabilities.append(Instrumentation(instrumentation))
    return capabilities
