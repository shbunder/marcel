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

_MEMORY_SEARCH_SCHEMA: dict = {
    'type': 'object',
    'properties': {
        'query': {
            'type': 'string',
            'description': 'Search query — matches against memory names, descriptions, and content.',
        },
        'type': {
            'type': 'string',
            'description': ('Optional type filter. One of: schedule, preference, person, reference, household.'),
        },
        'max_results': {
            'type': 'string',
            'description': 'Maximum number of results to return (default: 10).',
        },
    },
    'required': ['query'],
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

    async def _memory_search_impl(args: dict) -> dict:
        from marcel_core.storage.memory import MemoryType, search_memory_files

        query: str = args.get('query', '')
        if not query:
            return {'content': [{'type': 'text', 'text': 'query is required'}], 'is_error': True}

        type_filter = None
        raw_type = args.get('type')
        if raw_type:
            try:
                type_filter = MemoryType(raw_type)
            except ValueError:
                return {
                    'content': [
                        {
                            'type': 'text',
                            'text': f'Invalid type filter: {raw_type}. '
                            f'Valid types: {", ".join(t.value for t in MemoryType)}',
                        }
                    ],
                    'is_error': True,
                }

        max_results = int(args.get('max_results', '10'))
        results = search_memory_files(
            user_slug,
            query,
            type_filter=type_filter,
            max_results=max_results,
        )

        if not results:
            return {'content': [{'type': 'text', 'text': f'No memories found matching "{query}".'}]}

        lines: list[str] = []
        for r in results:
            tag = f'[{r.type.value}] ' if r.type else ''
            desc = f' — {r.description}' if r.description else ''
            lines.append(f'### {tag}{r.filename}{desc}')
            if r.snippet:
                lines.append(r.snippet)
            lines.append('')

        return {'content': [{'type': 'text', 'text': '\n'.join(lines).strip()}]}

    async def _notify_impl(args: dict) -> dict:
        message: str = args.get('message', '')
        if not message:
            return {'content': [{'type': 'text', 'text': 'ok'}]}

        if channel == 'telegram':
            try:
                from marcel_core.channels.telegram import bot, sessions

                chat_id = sessions.get_chat_id(user_slug)
                if chat_id:
                    from marcel_core.channels.telegram.formatting import escape_html

                    await bot.send_message(int(chat_id), escape_html(message))
            except Exception as exc:  # noqa: BLE001
                return {'content': [{'type': 'text', 'text': f'notify failed: {exc}'}]}

        return {'content': [{'type': 'text', 'text': 'ok'}]}

    integration_tool: SdkMcpTool = tool('integration', description, _INTEGRATION_SCHEMA)(_integration_impl)
    memory_search_tool: SdkMcpTool = tool(
        'memory_search',
        'Search across memory files by keyword. Use this when pre-loaded memories are not enough '
        'and you need to find specific information (e.g. a past appointment, a preference, a person). '
        'Returns matching memory files with snippets.',
        _MEMORY_SEARCH_SCHEMA,
    )(_memory_search_impl)
    notify_tool: SdkMcpTool = tool(
        'notify',
        'Send a short progress update to the user mid-task. '
        'Use this to keep the user informed during long operations (e.g. "Creating issue...", "Writing code...", "Running tests..."). '
        'Always call this at the start of any multi-step task and after each major step.',
        _NOTIFY_SCHEMA,
    )(_notify_impl)
    return create_sdk_mcp_server('marcel-skills', tools=[integration_tool, memory_search_tool, notify_tool])
