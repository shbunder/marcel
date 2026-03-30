"""iCloud MCP tools — exposes get_calendar_events, get_notes, search_mail to the agent."""

from __future__ import annotations

import json

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool
from claude_agent_sdk.types import McpSdkServerConfig

from .client import get_calendar_events, get_notes, search_mail

_CALENDAR_SCHEMA: dict = {
    'type': 'object',
    'properties': {
        'days_ahead': {
            'type': 'integer',
            'description': 'Number of days into the future to fetch events for. Default is 7.',
            'default': 7,
        },
    },
}

_NOTES_SCHEMA: dict = {
    'type': 'object',
    'properties': {},
}

_MAIL_SCHEMA: dict = {
    'type': 'object',
    'properties': {
        'query': {
            'type': 'string',
            'description': 'Text to search for in the mail inbox.',
        },
        'limit': {
            'type': 'integer',
            'description': 'Maximum number of matching messages to return. Default is 10.',
            'default': 10,
        },
    },
    'required': ['query'],
}


def build_icloud_mcp_server() -> McpSdkServerConfig:
    """Return an in-process MCP server with iCloud tools.

    Returns:
        A :class:`McpSdkServerConfig` ready for ``ClaudeAgentOptions.mcp_servers``.
    """

    async def _calendar_impl(args: dict) -> dict:
        days = int(args.get('days_ahead', 7))
        try:
            events = await get_calendar_events(days_ahead=days)
            return {'content': [{'type': 'text', 'text': json.dumps(events, indent=2)}]}
        except Exception as exc:  # noqa: BLE001
            return {'content': [{'type': 'text', 'text': f'iCloud calendar error: {exc}'}], 'is_error': True}

    async def _notes_impl(args: dict) -> dict:
        try:
            notes = await get_notes()
            return {'content': [{'type': 'text', 'text': json.dumps(notes, indent=2)}]}
        except Exception as exc:  # noqa: BLE001
            return {'content': [{'type': 'text', 'text': f'iCloud notes error: {exc}'}], 'is_error': True}

    async def _mail_impl(args: dict) -> dict:
        query: str = args.get('query', '')
        limit: int = int(args.get('limit', 10))
        if not query:
            return {'content': [{'type': 'text', 'text': 'query parameter is required'}], 'is_error': True}
        try:
            messages = await search_mail(query=query, limit=limit)
            return {'content': [{'type': 'text', 'text': json.dumps(messages, indent=2)}]}
        except Exception as exc:  # noqa: BLE001
            return {'content': [{'type': 'text', 'text': f'iCloud mail error: {exc}'}], 'is_error': True}

    calendar_tool: SdkMcpTool = tool(
        'icloud_get_calendar_events',
        "Fetch upcoming events from the user's iCloud Calendar.",
        _CALENDAR_SCHEMA,
    )(_calendar_impl)

    notes_tool: SdkMcpTool = tool(
        'icloud_get_notes',
        "Fetch all notes from the user's iCloud Notes.",
        _NOTES_SCHEMA,
    )(_notes_impl)

    mail_tool: SdkMcpTool = tool(
        'icloud_search_mail',
        "Search the user's iCloud Mail inbox for messages matching a text query.",
        _MAIL_SCHEMA,
    )(_mail_impl)

    return create_sdk_mcp_server(
        'marcel-icloud',
        tools=[calendar_tool, notes_tool, mail_tool],
    )
