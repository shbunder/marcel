"""Periodic transaction and balance sync for KBC banking data.

Runs as an asyncio background task, syncing every ``SYNC_INTERVAL`` seconds.
Stays within PSD2 rate limits (4 requests/day without active SCA) by syncing
every 8 hours (3 syncs/day).

Also monitors consent expiry and sends a notification when < 7 days remain.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from marcel_core.kbc import cache, client
from marcel_core.storage._root import data_root
from marcel_core.storage.credentials import load_credentials

log = logging.getLogger(__name__)

SYNC_INTERVAL = 8 * 60 * 60  # 8 hours in seconds
_CONSENT_WARN_DAYS = 7

# Module-level handle for the background task
_sync_task: asyncio.Task[None] | None = None


async def sync_account(slug: str) -> dict[str, Any]:
    """Run a single sync cycle for the user's KBC account.

    Fetches balances and recent transactions from EnableBanking and upserts
    them into the local SQLite cache.

    Returns a summary dict with counts and any warnings.
    """
    summary: dict[str, Any] = {'synced': 0, 'warnings': []}

    try:
        session = await client.get_session(slug)
    except RuntimeError:
        summary['warnings'].append('No KBC bank link found — run kbc.setup first')
        return summary

    if session.get('status') != 'AUTHORIZED':
        summary['warnings'].append(
            f'KBC session status is {session.get("status", "unknown")} — expected AUTHORIZED'
        )
        return summary

    accounts = session.get('accounts', [])
    if not accounts:
        summary['warnings'].append('No accounts found in session')
        return summary

    # Determine date range: from last sync or last 90 days
    last_sync = cache.get_sync_meta(slug, 'last_sync_date')
    if last_sync:
        date_from = last_sync
    else:
        date_from = (date.today() - timedelta(days=90)).isoformat()

    date_to = date.today().isoformat()

    for account in accounts:
        # get_session returns UIDs as strings; create_session returns dicts
        account_uid = account if isinstance(account, str) else account.get('uid', '')
        if not account_uid:
            continue
        try:
            # Sync balances
            balances = await client.get_balances(slug, account_uid)
            cache.upsert_balances(slug, account_uid, balances)

            # Sync transactions (handles pagination)
            txs = await client.get_all_transactions(
                slug, account_uid, date_from=date_from, date_to=date_to,
            )
            if txs:
                cache.upsert_transactions(slug, account_uid, txs)
                summary['synced'] += len(txs)

        except Exception:
            log.exception('Failed to sync account %s for user %s', account_uid, slug)
            summary['warnings'].append(f'Failed to sync account {account_uid}')

    cache.set_sync_meta(slug, 'last_sync_date', date_to)
    cache.set_sync_meta(slug, 'last_sync_at', datetime.now(UTC).isoformat())
    log.info('KBC sync complete for %s: %d transactions', slug, summary['synced'])
    return summary


async def check_consent_expiry(slug: str) -> str | None:
    """Check if the KBC consent is about to expire.

    Returns a warning message if consent expires within ``_CONSENT_WARN_DAYS``
    days, or None if everything is fine.
    """
    try:
        session = await client.get_session(slug)
    except Exception:
        return None

    # EnableBanking sessions have access.valid_until
    access = session.get('access', {})
    valid_until_str = access.get('valid_until', '')

    if not valid_until_str:
        return None

    try:
        expires = datetime.fromisoformat(valid_until_str.replace('Z', '+00:00'))
        days_left = (expires - datetime.now(tz=expires.tzinfo)).days

        if days_left <= _CONSENT_WARN_DAYS:
            return (
                f'Your KBC bank link expires in {days_left} day{"s" if days_left != 1 else ""}. '
                f'Ask Marcel to run "kbc.setup" to re-authenticate.'
            )
    except (ValueError, TypeError):
        log.warning('Could not parse session expiry for user %s', slug)

    return None


def _get_linked_slugs() -> list[str]:
    """Return user slugs that have EnableBanking credentials configured."""
    users_dir = data_root() / 'users'
    if not users_dir.is_dir():
        return []
    slugs = []
    for user_dir in users_dir.iterdir():
        if not user_dir.is_dir():
            continue
        creds = load_credentials(user_dir.name)
        if creds.get('ENABLEBANKING_APP_ID') and creds.get('ENABLEBANKING_SESSION_ID'):
            slugs.append(user_dir.name)
    return slugs


async def _sync_loop() -> None:
    """Background loop that syncs all linked users every ``SYNC_INTERVAL``."""
    # Initial delay — let the app start up before first sync
    await asyncio.sleep(30)

    while True:
        slugs = _get_linked_slugs()
        for slug in slugs:
            try:
                await sync_account(slug)
                warning = await check_consent_expiry(slug)
                if warning:
                    log.warning('Consent expiry alert for %s: %s', slug, warning)
                    cache.set_sync_meta(slug, 'consent_warning', warning)
            except Exception:
                log.exception('KBC sync failed for user %s', slug)

        await asyncio.sleep(SYNC_INTERVAL)


def start_sync_loop() -> None:
    """Start the background sync task.  Safe to call multiple times."""
    global _sync_task
    if _sync_task is not None and not _sync_task.done():
        return
    _sync_task = asyncio.create_task(_sync_loop())
    log.info('KBC sync loop started (interval: %ds)', SYNC_INTERVAL)


def stop_sync_loop() -> None:
    """Cancel the background sync task."""
    global _sync_task
    if _sync_task is not None:
        _sync_task.cancel()
        _sync_task = None
        log.info('KBC sync loop stopped')
