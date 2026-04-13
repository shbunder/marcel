"""Marcel agent wrapper around pydantic-ai Agent.

Provides a configured Agent instance with tools and instructions.
"""

from __future__ import annotations

import logging

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from marcel_core.config import settings
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

# Prefix for local (self-hosted, OpenAI-compatible) models. Strings of the
# shape ``local:<ollama_tag>`` are intercepted in :func:`create_marcel_agent`
# and routed to ``settings.marcel_local_llm_url`` via ``OpenAIChatModel``.
_LOCAL_PREFIX = 'local:'

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
    """Return the curated list of suggested models for list_models UX.

    When ``marcel_local_llm_url`` and ``marcel_local_llm_model`` are both set,
    the local model is appended so the picker surfaces it. Otherwise the
    suggestion stays hidden to avoid pointing users at a broken route.
    """
    models = dict(KNOWN_MODELS)
    if settings.marcel_local_llm_url and settings.marcel_local_llm_model:
        key = f'{_LOCAL_PREFIX}{settings.marcel_local_llm_model}'
        models[key] = f'Local — {settings.marcel_local_llm_model} (self-hosted)'
    return models


def _build_local_model(model_string: str) -> OpenAIChatModel:
    """Resolve a ``local:<tag>`` string to a configured ``OpenAIChatModel``.

    Pydantic-ai doesn't know about a ``local`` provider, so we substitute an
    ``OpenAIChatModel`` instance pointed at ``settings.marcel_local_llm_url``
    before handing it to ``Agent()``. The tag after ``local:`` is the ollama
    model name (e.g. ``qwen3.5:4b``) which may itself contain a colon — we
    only split off the leading ``local:`` prefix.

    Raises:
        RuntimeError: If ``marcel_local_llm_url`` is not configured.
    """
    if not settings.marcel_local_llm_url:
        raise RuntimeError(
            f'Model {model_string!r} requires a local LLM server, but '
            'MARCEL_LOCAL_LLM_URL is not set. See docs/local-llm.md for setup.'
        )
    tag = model_string[len(_LOCAL_PREFIX) :]
    if not tag:
        raise RuntimeError(f'Empty local model tag in {model_string!r}.')
    provider = OpenAIProvider(base_url=settings.marcel_local_llm_url, api_key='ollama')
    log.info('resolving local model: tag=%s base_url=%s', tag, settings.marcel_local_llm_url)
    return OpenAIChatModel(tag, provider=provider)


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
               The special prefix ``'local:<tag>'`` routes to the self-hosted
               OpenAI-compatible server at ``settings.marcel_local_llm_url``
               (used by the job local-fallback path — see ISSUE-070). All
               other strings pass through to ``Agent()`` verbatim.
        system_prompt: The system prompt string (must be provided).
        role: The user's role — ``'admin'`` or ``'user'``.

    Returns:
        Configured pydantic-ai Agent instance.
    """
    if not system_prompt:
        system_prompt = 'You are Marcel, a helpful AI assistant.'

    model_arg: str | Model
    if model.startswith(_LOCAL_PREFIX):
        model_arg = _build_local_model(model)
    else:
        model_arg = model

    agent: Agent[MarcelDeps, str] = Agent(
        model_arg,
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
