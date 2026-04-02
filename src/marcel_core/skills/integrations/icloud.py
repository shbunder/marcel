"""iCloud integration — calendar and mail access via CalDAV / IMAP.

Registers ``icloud.calendar`` and ``icloud.mail`` as python integration
skills, callable through the ``integration`` tool.
"""

from __future__ import annotations

import json

from marcel_core.icloud.client import get_calendar_events, search_mail
from marcel_core.skills.integrations import register


@register('icloud.calendar')
async def calendar(params: dict, user_slug: str) -> str:
    """Fetch upcoming calendar events."""
    days = int(params.get('days_ahead', '7'))
    events = await get_calendar_events(user_slug, days_ahead=days)
    return json.dumps(events, indent=2)


@register('icloud.mail')
async def mail(params: dict, user_slug: str) -> str:
    """Search iCloud Mail inbox."""
    query = params.get('query', '')
    if not query:
        raise ValueError('query parameter is required')
    limit = int(params.get('limit', '10'))
    messages = await search_mail(user_slug, query=query, limit=limit)
    return json.dumps(messages, indent=2)
