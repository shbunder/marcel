"""Marcel agent wrapper around pydantic-ai Agent.

Provides a configured Agent instance with tools and instructions.
"""

from __future__ import annotations

import logging

from pydantic_ai import Agent

from marcel_core.harness.context import MarcelDeps
from marcel_core.jobs import tool as job_tools
from marcel_core.tools import (
    charts as chart_tools,
    claude_code as claude_code_tool,
    core as core_tools,
    integration as integration_tools,
    marcel as marcel_tools,
)
from marcel_core.tools.web import web as web_tool
from marcel_core.tracing import get_instrumentation_settings

log = logging.getLogger(__name__)

# Suggested models shown by list_models. Keys are pydantic-ai qualified strings
# (``provider:model``); values are human-readable display names. This is a
# curated suggestion list, not a whitelist — set_model accepts any qualified
# string, so any pydantic-ai-supported model works without a code change.
KNOWN_MODELS: dict[str, str] = {
    'anthropic:claude-sonnet-4-6': 'Claude Sonnet 4.6 (fast, recommended)',
    'anthropic:claude-opus-4-6': 'Claude Opus 4.6 (most capable)',
    'anthropic:claude-haiku-4-5-20251001': 'Claude Haiku 4.5 (fastest)',
    'openai:gpt-4o': 'GPT-4o (fast, multimodal)',
    'openai:gpt-4o-mini': 'GPT-4o mini (fastest, cheapest)',
    'openai:o1': 'o1 (reasoning)',
    'openai:o3-mini': 'o3-mini (fast reasoning)',
}

DEFAULT_MODEL = 'anthropic:claude-sonnet-4-6'


def all_models() -> dict[str, str]:
    """Return the curated list of suggested models for list_models UX."""
    return dict(KNOWN_MODELS)


def create_marcel_agent(
    model: str = DEFAULT_MODEL,
    system_prompt: str = '',
    role: str = 'user',
) -> Agent[MarcelDeps, str]:
    """Create a configured Marcel agent with a role-appropriate tool set.

    Admin users receive the full suite of power tools (bash, file I/O, git, claude_code).
    Regular users receive only integration and the unified marcel utils tool — enough
    for a household assistant without exposing arbitrary shell access.

    Args:
        model: Fully-qualified pydantic-ai model string, e.g.
               ``'anthropic:claude-sonnet-4-6'`` or ``'openai:gpt-4o'``.
               Passed verbatim to ``Agent()``; pydantic-ai handles provider
               dispatch and credential lookup.
        system_prompt: The system prompt string (must be provided).
        role: The user's role — ``'admin'`` or ``'user'``.

    Returns:
        Configured pydantic-ai Agent instance.
    """
    if not system_prompt:
        system_prompt = 'You are Marcel, a helpful AI assistant.'

    agent: Agent[MarcelDeps, str] = Agent(
        model,
        deps_type=MarcelDeps,
        instructions=system_prompt,
        retries=2,
        end_strategy='exhaustive',
        instrument=get_instrumentation_settings(),
    )

    # Web god-tool — search + browser actions unified behind one dispatcher.
    # Always registered; the dispatcher itself returns a clean error for
    # browser actions when playwright isn't installed, so ``search`` still
    # works in playwright-less environments.
    agent.tool(web_tool)

    if role == 'admin':
        # Full power tools — bash, file I/O, git, and Claude Code delegation
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
        agent.tool(claude_code_tool.claude_code)

    # Chart/image generation — available to all users
    agent.tool(chart_tools.generate_chart)

    # All users get integration dispatch and the unified marcel utils tool
    agent.tool(integration_tools.integration)
    agent.tool(marcel_tools.marcel)

    # Job management tools
    agent.tool(job_tools.create_job)
    agent.tool(job_tools.list_jobs)
    agent.tool(job_tools.get_job)
    agent.tool(job_tools.update_job)
    agent.tool(job_tools.delete_job)
    agent.tool(job_tools.run_job_now)
    agent.tool(job_tools.job_templates)
    agent.tool(job_tools.job_cache_write)
    agent.tool(job_tools.job_cache_read)

    log.info('agent ready: model=%s role=%s', model, role)
    return agent
