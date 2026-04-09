"""Marcel agent wrapper around pydantic-ai Agent.

Provides a configured Agent instance with tools and instructions.
"""

from __future__ import annotations

import logging
import os

from pydantic_ai import Agent

from marcel_core.harness.context import MarcelDeps, build_instructions
from marcel_core.tools import core as core_tools
from marcel_core.tools import claude_code as claude_code_tool
from marcel_core.tools import integration as integration_tools

log = logging.getLogger(__name__)


def _resolve_model_string(model: str) -> str:
    """Resolve model string to support Bedrock ARNs.

    If AWS_REGION is set and model looks like an ARN, use Bedrock provider.
    """
    aws_region = os.environ.get('AWS_REGION')
    bedrock_base_url = os.environ.get('ANTHROPIC_BEDROCK_BASE_URL')

    # If model is a Bedrock ARN and we have AWS config, use bedrock provider
    if aws_region and 'arn:aws:bedrock' in model:
        # pydantic-ai bedrock provider format: bedrock:{region}/{model_id}
        # Extract region from ARN or use AWS_REGION
        model_id = model.split('/')[-1]  # Get last part of ARN
        return f'bedrock:{aws_region}/{model_id}'

    # If using bedrock base URL proxy, use anthropic provider with custom base_url
    if bedrock_base_url:
        # For now, just use the model as-is with anthropic provider
        # The bedrock proxy at localhost:9090 translates to bedrock
        return model if ':' in model else f'anthropic:{model}'

    # Default: assume anthropic provider
    return model if ':' in model else f'anthropic:{model}'


def create_marcel_agent(model: str = 'anthropic:claude-sonnet-4-6', system_prompt: str = '') -> Agent[MarcelDeps, str]:
    """Create a configured Marcel agent with all tools.

    Args:
        model: The model identifier (e.g., 'anthropic:claude-sonnet-4-6', 'openai:gpt-4', ARN for Bedrock).
        system_prompt: The system prompt string (must be provided).

    Returns:
        Configured pydantic-ai Agent instance.
    """
    resolved_model = _resolve_model_string(model)
    log.info('Creating Marcel agent: input_model=%s resolved_model=%s', model, resolved_model)

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
