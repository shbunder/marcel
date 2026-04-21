"""Subagent definitions — purpose-built child agents delegated to from the main turn.

Each agent is a markdown file with YAML frontmatter. Agents are discovered
from two sources (data root wins on collision):

1. ``<MARCEL_ZOO_DIR>/agents/`` — habitats from the marcel-zoo checkout.
2. ``<data_root>/agents/`` — user-installed/customized subagents.

The kernel ships no default agents — all defaults live in marcel-zoo.

The loader lives in :mod:`marcel_core.agents.loader`; the tool that invokes
an agent lives in :mod:`marcel_core.tools.delegate`. See ISSUE-074,
ISSUE-e22176, and ``docs/subagents.md`` for the design.
"""

from marcel_core.agents.loader import AgentDoc, AgentNotFoundError, load_agent, load_agents

__all__ = ['AgentDoc', 'AgentNotFoundError', 'load_agent', 'load_agents']
