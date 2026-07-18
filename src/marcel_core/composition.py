"""The composition root — the one place capability lists are assembled.

Per ADR-260718-0cf8e8, an agent is a composition of capabilities and this
module is the only place that composition is built. Capability packages
under :mod:`marcel_core.capabilities` never import each other; they meet
here. The list grows as the FEAT-260718-* roadmap features land their
capabilities.
"""

from __future__ import annotations

from pydantic_ai.capabilities import AbstractCapability, Instrumentation
from pydantic_ai_harness.step_persistence import StepPersistence

from marcel_core.capabilities.persistence import persistence_store
from marcel_core.capabilities.policy import MarcelPolicy
from marcel_core.harness.context import MarcelDeps
from marcel_core.tracing import get_instrumentation_settings


def build_capabilities() -> list[AbstractCapability[MarcelDeps]]:
    """Assemble the capability list for a Marcel agent.

    Today: the policy gate (always), step persistence (the store ignores
    runs without a Marcel conversation id — jobs, subagents, the explain
    tier), and instrumentation (when tracing is enabled). Later roadmap
    features append theirs here, keyed on role, channel, and tier as those
    axes become capability-relevant.
    """
    capabilities: list[AbstractCapability[MarcelDeps]] = [
        MarcelPolicy(),
        StepPersistence(store=persistence_store(), agent_name='marcel'),
    ]
    instrumentation = get_instrumentation_settings()
    if instrumentation is not None:
        capabilities.append(Instrumentation(instrumentation))
    return capabilities
