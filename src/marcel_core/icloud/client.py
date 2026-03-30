"""iCloud client wrappers.

Uses pyicloud for calendar and notes access, and imaplib (stdlib) for mail search.
All blocking calls are wrapped in asyncio.to_thread() to avoid blocking the event loop.

Credentials are read from the user's credentials file:
    data/users/{slug}/credentials.env
Expected keys: ICLOUD_APPLE_ID, ICLOUD_APP_PASSWORD
"""

from __future__ import annotations

import asyncio
import email
import imaplib
from datetime import datetime, timedelta, timezone
from typing import Any

from pyicloud import PyiCloudService

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


def _cookie_dir() -> str:
    """Return a persistent directory for iCloud session cookies."""
    path = data_root() / 'icloud' / 'cookies'
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _credentials(slug: str = 'shaun') -> tuple[str, str]:
    """Return (apple_id, app_password) from the user's credentials.env file."""
    creds = _load_user_credentials(slug)
    apple_id = creds.get('ICLOUD_APPLE_ID', '').strip()
    password = creds.get('ICLOUD_APP_PASSWORD', '').strip()
    if not apple_id or not password:
        raise RuntimeError(f'ICLOUD_APPLE_ID and ICLOUD_APP_PASSWORD must be set in data/users/{slug}/credentials.env')
    return apple_id, password


def _get_service() -> PyiCloudService:
    """Create (or reuse) a PyiCloudService instance.

    With an app-specific password, Apple's 2FA is bypassed entirely.
    The session cookies are persisted in the data dir to minimise round-trips.
    """
    apple_id, password = _credentials()
    return PyiCloudService(apple_id, password, cookie_directory=_cookie_dir())


def _fetch_calendar_events(days_ahead: int = 7) -> list[dict[str, Any]]:
    """Synchronously fetch calendar events for the next `days_ahead` days."""
    api = _get_service()
    now = datetime.now(tz=timezone.utc)
    until = now + timedelta(days=days_ahead)
    raw = api.calendar.events(from_dt=now, to_dt=until)
    events: list[dict[str, Any]] = []
    for ev in raw:
        events.append(
            {
                'title': ev.get('title', '(no title)'),
                'start': str(ev.get('startDate', '')),
                'end': str(ev.get('endDate', '')),
                'location': ev.get('location', ''),
                'description': ev.get('description', ''),
            }
        )
    return events


def _fetch_notes() -> list[dict[str, Any]]:
    """Synchronously fetch all notes."""
    api = _get_service()
    notes: list[dict[str, Any]] = []
    for folder in api.notes.folders:
        for note in api.notes.show(folder).get('notes', []):
            notes.append(
                {
                    'folder': folder.get('name', 'Notes'),
                    'title': note.get('title', '(untitled)'),
                    'snippet': note.get('snippet', ''),
                    'modified': str(note.get('lastModified', '')),
                }
            )
    return notes


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
            raw = msg_data[0][1]
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


async def get_notes() -> list[dict[str, Any]]:
    """Async wrapper: fetch all iCloud notes."""
    return await asyncio.to_thread(_fetch_notes)


async def search_mail(query: str, limit: int = 10) -> list[dict[str, str]]:
    """Async wrapper: search iCloud mail."""
    return await asyncio.to_thread(_search_mail_imap, query, limit)
