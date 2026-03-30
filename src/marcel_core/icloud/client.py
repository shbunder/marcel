"""iCloud client wrappers.

Uses caldav for calendar access and imaplib (stdlib) for mail search.
All blocking calls are wrapped in asyncio.to_thread() to avoid blocking the event loop.

Credentials are read from the user's credentials file:
    data/users/{slug}/credentials.env
Expected keys: ICLOUD_APPLE_ID, ICLOUD_APP_PASSWORD

Note: Notes access is not available — Apple provides no standard protocol for
notes, and pyicloud's web-auth flow does not work with app-specific passwords.
"""

from __future__ import annotations

import asyncio
import email
import imaplib
from datetime import datetime, timedelta, timezone
from typing import Any

import caldav

from marcel_core.storage._root import data_root


def _load_user_credentials(slug: str = 'shaun') -> dict[str, str]:
    """Load key=value pairs from data/users/{slug}/credentials.env."""
    creds_path = data_root() / 'users' / slug / 'credentials.env'
    result: dict[str, str] = {}
    if not creds_path.exists():
        return result
    for line in creds_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        result[key.strip()] = value.strip()
    return result


def _credentials(slug: str = 'shaun') -> tuple[str, str]:
    """Return (apple_id, app_password) from the user's credentials.env file."""
    creds = _load_user_credentials(slug)
    apple_id = creds.get('ICLOUD_APPLE_ID', '').strip()
    password = creds.get('ICLOUD_APP_PASSWORD', '').strip()
    if not apple_id or not password:
        raise RuntimeError(f'ICLOUD_APPLE_ID and ICLOUD_APP_PASSWORD must be set in data/users/{slug}/credentials.env')
    return apple_id, password


def _fetch_calendar_events(days_ahead: int = 7) -> list[dict[str, Any]]:
    """Synchronously fetch calendar events for the next `days_ahead` days via CalDAV."""
    apple_id, password = _credentials()
    client = caldav.DAVClient(
        url='https://caldav.icloud.com/',
        username=apple_id,
        password=password,
    )
    principal = client.principal()
    calendars = principal.calendars()

    now = datetime.now(tz=timezone.utc)
    until = now + timedelta(days=days_ahead)

    events: list[dict[str, Any]] = []
    for cal in calendars:
        cal_name = cal.get_display_name() or '(unnamed)'
        try:
            results = cal.search(start=now, end=until, event=True, expand=True)
        except Exception:
            continue
        for ev in results:
            try:
                vevent = ev.vobject_instance.vevent
            except Exception:
                continue
            events.append(
                {
                    'calendar': cal_name,
                    'title': vevent.summary.value if hasattr(vevent, 'summary') else '(no title)',
                    'start': str(vevent.dtstart.value) if hasattr(vevent, 'dtstart') else '',
                    'end': str(vevent.dtend.value) if hasattr(vevent, 'dtend') else '',
                    'location': vevent.location.value if hasattr(vevent, 'location') else '',
                    'description': vevent.description.value if hasattr(vevent, 'description') else '',
                }
            )

    events.sort(key=lambda e: e['start'])
    return events


def _search_mail_imap(query: str, limit: int = 10) -> list[dict[str, str]]:
    """Synchronously search iCloud mail via IMAP using an app-specific password."""
    apple_id, password = _credentials()

    mail = imaplib.IMAP4_SSL('imap.mail.me.com', 993)
    try:
        mail.login(apple_id, password)
        mail.select('INBOX')

        # Build IMAP search string — search in subject and body text
        search_criterion = f'TEXT "{query}"'
        _, data = mail.search(None, search_criterion)
        msg_ids = data[0].split()

        # Fetch most recent `limit` messages matching the search
        results: list[dict[str, str]] = []
        for msg_id in reversed(msg_ids[-limit:]):
            _, msg_data = mail.fetch(msg_id, '(RFC822)')
            # msg_data may contain non-tuple entries (e.g. b')'); skip them
            raw = None
            for part in msg_data:
                if isinstance(part, tuple):
                    raw = part[1]
                    break
            if raw is None:
                continue
            msg = email.message_from_bytes(raw)
            body = ''
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == 'text/plain':
                        body = part.get_payload(decode=True).decode('utf-8', errors='replace')[:500]
                        break
            else:
                body = msg.get_payload(decode=True).decode('utf-8', errors='replace')[:500]

            results.append(
                {
                    'from': msg.get('From', ''),
                    'subject': msg.get('Subject', '(no subject)'),
                    'date': msg.get('Date', ''),
                    'snippet': body.strip(),
                }
            )
        return results
    finally:
        mail.logout()


# ── Public async API ──────────────────────────────────────────────────────────


async def get_calendar_events(days_ahead: int = 7) -> list[dict[str, Any]]:
    """Async wrapper: fetch upcoming calendar events."""
    return await asyncio.to_thread(_fetch_calendar_events, days_ahead)


async def search_mail(query: str, limit: int = 10) -> list[dict[str, str]]:
    """Async wrapper: search iCloud mail."""
    return await asyncio.to_thread(_search_mail_imap, query, limit)
