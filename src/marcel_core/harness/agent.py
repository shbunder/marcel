"""Marcel agent wrapper around pydantic-ai Agent.

Provides a configured Agent instance with tools and instructions.
"""

from __future__ import annotations

import logging

from pydantic_ai import Agent

from marcel_core.harness.context import MarcelDeps, build_instructions
from marcel_core.tools import core as core_tools

log = logging.getLogger(__name__)


def create_marcel_agent(model: str = 'anthropic:claude-sonnet-4-6') -> Agent[MarcelDeps, str]:
    """Create a configured Marcel agent with all tools.

    Args:
        model: The model identifier (e.g., 'anthropic:claude-sonnet-4-6', 'openai:gpt-4').

    Returns:
        Configured pydantic-ai Agent instance.
    """
    agent: Agent[MarcelDeps, str] = Agent(
        model,
        deps_type=MarcelDeps,
        result_type=str,
        system_prompt=build_instructions,  # Callable for dynamic prompts
        retries=2,
    )

    # Register core tools
    agent.tool(core_tools.bash)
    agent.tool(core_tools.read_file)
    agent.tool(core_tools.write_file)
    agent.tool(core_tools.edit_file)
    agent.tool(core_tools.git_status)
    agent.tool(core_tools.git_diff)
    agent.tool(core_tools.git_log)
    agent.tool(core_tools.git_add)
    agent.tool(core_tools.git_commit)
    agent.tool(core_tools.git_push)

    log.info('Created Marcel agent with model=%s', model)
    return agent
