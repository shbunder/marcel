"""Marcel agent wrapper around pydantic-ai Agent.

Provides a configured Agent instance with tools and instructions.
"""

from __future__ import annotations

import logging
import os

from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel

from marcel_core.harness.context import MarcelDeps
from marcel_core.tools import claude_code as claude_code_tool, core as core_tools, integration as integration_tools

log = logging.getLogger(__name__)

# Map friendly model names to Bedrock model IDs
_BEDROCK_MODEL_MAP = {
    'claude-sonnet-4-6': 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0',
    'claude-opus-4-6': 'eu.anthropic.claude-opus-4-6-v1',
    'claude-haiku-4-5-20251001': 'eu.anthropic.claude-haiku-4-5-20251001-v1:0',
}


def _create_anthropic_model(model_name: str) -> str | AnthropicModel:
    """Create an Anthropic model, choosing the auth method automatically.

    Priority order:
    1. ``AWS_REGION`` set → AWS Bedrock (returns model string)
    2. ``ANTHROPIC_API_KEY`` set → Anthropic API key (returns model string)
    3. ``~/.claude/.credentials.json`` exists → Claude Code OAuth bearer token

    Returns:
        A pydantic-ai model string (Bedrock / API key paths) or an
        ``AnthropicModel`` instance (OAuth path).

    Raises:
        RuntimeError: If no authentication method is available.
    """
    # 1. AWS Bedrock
    aws_region = os.environ.get('AWS_REGION')
    if aws_region:
        bedrock_model_id = _BEDROCK_MODEL_MAP.get(model_name, model_name)
        log.info(
            'Creating model with AWS Bedrock: region=%s model=%s bedrock_id=%s',
            aws_region,
            model_name,
            bedrock_model_id,
        )
        return f'bedrock:{bedrock_model_id}'

    # 2. Standard API key
    if os.environ.get('ANTHROPIC_API_KEY'):
        log.info('Creating Anthropic model with API key: model=%s', model_name)
        return f'anthropic:{model_name}'

    # 3. Claude Code OAuth token
    from marcel_core.harness.oauth import build_anthropic_provider

    log.info('Creating Anthropic model with Claude Code OAuth: model=%s', model_name)
    return build_anthropic_provider(model_name)


def create_marcel_agent(model: str = 'claude-sonnet-4-6', system_prompt: str = '') -> Agent[MarcelDeps, str]:
    """Create a configured Marcel agent with all tools.

    Args:
        model: The model name (e.g., 'claude-sonnet-4-6', 'gpt-4').
               The function handles provider selection (Anthropic, OpenAI, Bedrock proxy).
        system_prompt: The system prompt string (must be provided).

    Returns:
        Configured pydantic-ai Agent instance.
    """
    # Strip provider prefix if present (e.g., 'anthropic:' or 'openai:')
    clean_model = model.split(':', 1)[-1] if ':' in model else model

    # Determine if we need Anthropic (default) or OpenAI
    if 'gpt' in clean_model.lower():
        resolved_model = f'openai:{clean_model}'
        log.info('Creating Marcel agent with OpenAI: model=%s', resolved_model)
    else:
        # Use Anthropic (with Bedrock proxy if configured)
        resolved_model = _create_anthropic_model(clean_model)
        log.info('Creating Marcel agent with Anthropic: model=%s', clean_model)

    if not system_prompt:
        system_prompt = 'You are Marcel, a helpful AI assistant.'

    agent: Agent[MarcelDeps, str] = Agent(
        resolved_model,
        deps_type=MarcelDeps,
        system_prompt=system_prompt,
        retries=2,
    )

    # Register core tools (bash, files, git)
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

    # Register integration tools (dispatch to skills registry)
    agent.tool(integration_tools.integration)
    agent.tool(integration_tools.memory_search)
    agent.tool(integration_tools.notify)

    # Register claude-code delegation tool
    agent.tool(claude_code_tool.claude_code)

    log.info('Created Marcel agent with model=%s', model)
    return agent
