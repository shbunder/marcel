"""cmd MCP tool — exposes the skills registry to the claude_agent_sdk agent."""
from __future__ import annotations

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool
from claude_agent_sdk.types import McpSdkServerConfig

from .executor import run
from .registry import get_skill, list_skills

_CMD_SCHEMA: dict = {
    'type': 'object',
    'properties': {
        'skill': {
            'type': 'string',
            'description': (
                'Dotted skill name from the registry '
                '(e.g. "calendar.list_events"). '
                'Call list_skills first if unsure.'
            ),
        },
        'params': {
            'type': 'object',
            'description': 'Skill-specific arguments as string key-value pairs.',
            'additionalProperties': {'type': 'string'},
        },
    },
    'required': ['skill'],
}


def build_skills_mcp_server(user_slug: str) -> McpSdkServerConfig:
    """Return an in-process MCP server with the cmd tool bound to `user_slug`.

    Args:
        user_slug: The user executing the command (used for per-user auth).

    Returns:
        A :class:`McpSdkServerConfig` ready for ``ClaudeAgentOptions.mcp_servers``.
    """
    available = list_skills()
    description = (
        'Execute a registered integration skill.\n'
        + (
            f'Available skills: {", ".join(available)}'
            if available
            else 'No skills are registered yet — the registry is empty.'
        )
    )

    async def _cmd_impl(args: dict) -> dict:
        skill_name: str = args.get('skill', '')
        params: dict = args.get('params', {})

        try:
            config = get_skill(skill_name)
        except KeyError as exc:
            return {'content': [{'type': 'text', 'text': str(exc)}], 'is_error': True}

        try:
            result = await run(config, params, user_slug)
            return {'content': [{'type': 'text', 'text': result}]}
        except Exception as exc:  # noqa: BLE001
            return {
                'content': [{'type': 'text', 'text': f'Skill execution error: {exc}'}],
                'is_error': True,
            }

    cmd_tool: SdkMcpTool = tool('cmd', description, _CMD_SCHEMA)(_cmd_impl)
    return create_sdk_mcp_server('marcel-skills', tools=[cmd_tool])
