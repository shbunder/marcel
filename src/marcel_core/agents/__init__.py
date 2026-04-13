"""Subagent definitions — purpose-built child agents delegated to from the main turn.

Each agent is a markdown file with YAML frontmatter under
``<data_root>/agents/``. Defaults are bundled in
``src/marcel_core/defaults/agents/`` and seeded on first startup.

The loader lives in :mod:`marcel_core.agents.loader`; the tool that invokes
an agent lives in :mod:`marcel_core.tools.delegate`. See ISSUE-074 and
``docs/subagents.md`` for the design.
"""

from marcel_core.agents.loader import AgentDoc, AgentNotFoundError, load_agent, load_agents

__all__ = ['AgentDoc', 'AgentNotFoundError', 'load_agent', 'load_agents']
