"""integration and notify MCP tools — exposes the skills registry and progress notifications to the agent."""

from __future__ import annotations

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool
from claude_agent_sdk.types import McpSdkServerConfig

from .executor import run
from .registry import get_skill, list_skills

_INTEGRATION_SCHEMA: dict = {
    'type': 'object',
    'properties': {
        'skill': {
            'type': 'string',
            'description': (
                'Dotted skill name from the registry (e.g. "icloud.calendar"). '
                'See your skill docs for available commands and parameters.'
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

_NOTIFY_SCHEMA: dict = {
    'type': 'object',
    'properties': {
        'message': {
            'type': 'string',
            'description': 'Short plain-text progress update to send to the user.',
        },
    },
    'required': ['message'],
}


def build_skills_mcp_server(user_slug: str, channel: str = 'cli') -> McpSdkServerConfig:
    """Return an in-process MCP server with the integration and notify tools bound to `user_slug`.

    Args:
        user_slug: The user executing the command (used for per-user auth).
        channel: The originating channel. When 'telegram', the notify tool sends
            real-time progress messages to the user's Telegram chat.

    Returns:
        A :class:`McpSdkServerConfig` ready for ``ClaudeAgentOptions.mcp_servers``.
    """
    available = list_skills()
    description = 'Execute a registered integration skill.\n' + (
        f'Available skills: {", ".join(available)}'
        if available
        else 'No skills are registered yet — the registry is empty.'
    )

    async def _integration_impl(args: dict) -> dict:
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

    async def _notify_impl(args: dict) -> dict:
        message: str = args.get('message', '')
        if not message:
            return {'content': [{'type': 'text', 'text': 'ok'}]}

        if channel == 'telegram':
            try:
                from marcel_core.telegram import bot, sessions

                chat_id = sessions.get_chat_id(user_slug)
                if chat_id:
                    await bot.send_message(int(chat_id), bot.escape_markdown_v2(message))
            except Exception as exc:  # noqa: BLE001
                return {'content': [{'type': 'text', 'text': f'notify failed: {exc}'}]}

        return {'content': [{'type': 'text', 'text': 'ok'}]}

    integration_tool: SdkMcpTool = tool('integration', description, _INTEGRATION_SCHEMA)(_integration_impl)
    notify_tool: SdkMcpTool = tool(
        'notify',
        'Send a short progress update to the user mid-task. '
        'Use this to keep the user informed during long operations (e.g. "Creating issue...", "Writing code...", "Running tests..."). '
        'Always call this at the start of any multi-step task and after each major step.',
        _NOTIFY_SCHEMA,
    )(_notify_impl)
    return create_sdk_mcp_server('marcel-skills', tools=[integration_tool, notify_tool])
