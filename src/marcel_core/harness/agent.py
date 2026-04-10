"""Marcel agent wrapper around pydantic-ai Agent.

Provides a configured Agent instance with tools and instructions.
"""

from __future__ import annotations

import logging

from pydantic_ai import Agent

from marcel_core.config import settings
from marcel_core.harness.context import MarcelDeps
from marcel_core.jobs import tool as job_tools
from marcel_core.tools import claude_code as claude_code_tool, core as core_tools, integration as integration_tools

log = logging.getLogger(__name__)

# Canonical model registry — models available to users.
# Keys are the pydantic-ai model strings; values are human-readable display names.
ANTHROPIC_MODELS: dict[str, str] = {
    'claude-sonnet-4-6': 'Claude Sonnet 4.6 (fast, recommended)',
    'claude-opus-4-6': 'Claude Opus 4.6 (most capable)',
    'claude-haiku-4-5-20251001': 'Claude Haiku 4.5 (fastest)',
}

OPENAI_MODELS: dict[str, str] = {
    'gpt-4o': 'GPT-4o (fast, multimodal)',
    'gpt-4o-mini': 'GPT-4o mini (fastest, cheapest)',
    'o1': 'o1 (reasoning)',
    'o3-mini': 'o3-mini (fast reasoning)',
}

# Default model when no preference is set
DEFAULT_MODEL = 'claude-sonnet-4-6'

# Map friendly model names to Bedrock model IDs
_BEDROCK_MODEL_MAP = {
    'claude-sonnet-4-6': 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0',
    'claude-opus-4-6': 'eu.anthropic.claude-opus-4-6-v1',
    'claude-haiku-4-5-20251001': 'eu.anthropic.claude-haiku-4-5-20251001-v1:0',
}


def all_models() -> dict[str, str]:
    """Return all available models across all providers."""
    return {**ANTHROPIC_MODELS, **OPENAI_MODELS}


def _resolve_model_string(model_name: str) -> str:
    """Resolve a short model name to a fully-qualified pydantic-ai model string.

    Selects the provider automatically based on available credentials:
    1. ``AWS_REGION`` set → AWS Bedrock (``bedrock:…``)
    2. OpenAI model + ``OPENAI_API_KEY`` → OpenAI (``openai:…``)
    3. ``ANTHROPIC_API_KEY`` set → Anthropic (``anthropic:…``)
    4. ``OPENAI_API_KEY`` set → OpenAI fallback (``openai:…``)

    Returns:
        A pydantic-ai model string (e.g. ``'anthropic:claude-sonnet-4-6'``).

    Raises:
        RuntimeError: If no authentication method is available.
    """
    # 1. AWS Bedrock
    aws_region = settings.aws_region
    if aws_region:
        bedrock_model_id = _BEDROCK_MODEL_MAP.get(model_name, model_name)
        log.info(
            'Creating model with AWS Bedrock: region=%s model=%s bedrock_id=%s',
            aws_region,
            model_name,
            bedrock_model_id,
        )
        return f'bedrock:{bedrock_model_id}'

    # 2. OpenAI model with API key
    if model_name in OPENAI_MODELS and settings.openai_api_key:
        log.info('Creating OpenAI model with API key: model=%s', model_name)
        return f'openai:{model_name}'

    # 3. Anthropic API key
    if settings.anthropic_api_key:
        log.info('Creating Anthropic model with API key: model=%s', model_name)
        return f'anthropic:{model_name}'

    # 4. OpenAI API key (fallback for OpenAI models)
    if settings.openai_api_key:
        log.info('Creating OpenAI model with API key: model=%s', model_name)
        return f'openai:{model_name}'

    raise RuntimeError(
        f'No API key found for model {model_name!r}. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env.local.'
    )


def create_marcel_agent(
    model: str = DEFAULT_MODEL,
    system_prompt: str = '',
    role: str = 'user',
) -> Agent[MarcelDeps, str]:
    """Create a configured Marcel agent with a role-appropriate tool set.

    Admin users receive the full suite of power tools (bash, file I/O, git, claude_code).
    Regular users receive only integration, memory_search, and notify — enough for a
    household assistant without exposing arbitrary shell access.

    Args:
        model: The model name (e.g., 'claude-sonnet-4-6', 'gpt-4o').
               The function handles provider selection (Anthropic, OpenAI, Bedrock proxy).
        system_prompt: The system prompt string (must be provided).
        role: The user's role — ``'admin'`` or ``'user'``.

    Returns:
        Configured pydantic-ai Agent instance.
    """
    # Strip provider prefix if present (e.g., 'anthropic:' or 'openai:')
    clean_model = model.split(':', 1)[-1] if ':' in model else model

    resolved_model = _resolve_model_string(clean_model)
    log.info('Creating Marcel agent: model=%s resolved=%s role=%s', clean_model, resolved_model, role)

    if not system_prompt:
        system_prompt = 'You are Marcel, a helpful AI assistant.'

    agent: Agent[MarcelDeps, str] = Agent(
        resolved_model,
        deps_type=MarcelDeps,
        system_prompt=system_prompt,
        retries=2,
    )

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

    # All users get integration dispatch, memory search, and notifications
    agent.tool(integration_tools.integration)
    agent.tool(integration_tools.memory_search)
    agent.tool(integration_tools.notify)

    # Job management tools
    agent.tool(job_tools.create_job)
    agent.tool(job_tools.list_jobs)
    agent.tool(job_tools.get_job)
    agent.tool(job_tools.update_job)
    agent.tool(job_tools.delete_job)
    agent.tool(job_tools.run_job_now)
    agent.tool(job_tools.job_templates)

    log.info('Created Marcel agent with model=%s role=%s', model, role)
    return agent
