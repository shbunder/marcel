"""EnableBanking API client for KBC bank account data.

Handles JWT authentication (RS256 with private key), session management,
and account data retrieval (balances, transactions).

Credentials are read from the user's credential store:
    ENABLEBANKING_APP_ID — application UUID (also the .pem filename)
The private key file lives at: data/users/{slug}/enablebanking.pem
Session ID is persisted after initial bank link: ENABLEBANKING_SESSION_ID
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import httpx
import jwt

from marcel_core.storage._root import data_root
from marcel_core.storage.credentials import load_credentials, save_credentials

log = logging.getLogger(__name__)

_BASE_URL = 'https://api.enablebanking.com'

# KBC Belgium
KBC_ASPSP_NAME = 'KBC'
KBC_COUNTRY = 'BE'


def _app_id(slug: str) -> str:
    """Return the EnableBanking application ID from the user's credential store."""
    creds = load_credentials(slug)
    app_id = creds.get('ENABLEBANKING_APP_ID', '').strip()
    if not app_id:
        raise RuntimeError(
            f'ENABLEBANKING_APP_ID must be set in credentials for user {slug}'
        )
    return app_id


def _private_key_path(slug: str) -> Path:
    return data_root() / 'users' / slug / 'enablebanking.pem'


def _load_private_key(slug: str) -> str:
    """Load the PEM private key for JWT signing."""
    path = _private_key_path(slug)
    if not path.exists():
        raise RuntimeError(
            f'EnableBanking private key not found at {path}. '
            f'Download it from the EnableBanking dashboard.'
        )
    return path.read_text()


def _make_jwt(slug: str) -> str:
    """Create a signed JWT for EnableBanking API authentication."""
    app_id = _app_id(slug)
    private_key = _load_private_key(slug)
    now = int(time.time())
    payload = {
        'iss': 'enablebanking.com',
        'aud': 'api.enablebanking.com',
        'iat': now,
        'exp': now + 3600,
    }
    return jwt.encode(payload, private_key, algorithm='RS256', headers={'kid': app_id})


def _session_id(slug: str) -> str:
    """Return the stored session ID, or raise if not linked yet."""
    creds = load_credentials(slug)
    sid = creds.get('ENABLEBANKING_SESSION_ID', '').strip()
    if not sid:
        raise RuntimeError(
            'No KBC bank link found. Run kbc.setup first to link your bank account.'
        )
    return sid


async def _authed_get(slug: str, path: str, *, params: dict[str, str] | None = None) -> Any:
    """Make an authenticated GET request to the EnableBanking API."""
    token = _make_jwt(slug)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f'{_BASE_URL}{path}',
            headers={'Authorization': f'Bearer {token}'},
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def _authed_post(slug: str, path: str, *, json: dict[str, Any]) -> Any:
    """Make an authenticated POST request to the EnableBanking API."""
    token = _make_jwt(slug)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f'{_BASE_URL}{path}',
            headers={'Authorization': f'Bearer {token}'},
            json=json,
        )
        resp.raise_for_status()
        return resp.json()


async def _authed_delete(slug: str, path: str) -> None:
    """Make an authenticated DELETE request to the EnableBanking API."""
    token = _make_jwt(slug)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.delete(
            f'{_BASE_URL}{path}',
            headers={'Authorization': f'Bearer {token}'},
        )
        resp.raise_for_status()


# ── Public API ───────────────────────────────────────────────────────────────


async def start_authorization(
    slug: str,
    redirect_url: str = 'https://enablebanking.com',
) -> dict[str, Any]:
    """Start the bank authorization flow for KBC Belgium.

    Returns a dict with ``url`` (redirect the user here) and
    ``authorization_id``.
    """
    from datetime import UTC, datetime, timedelta

    valid_until = (datetime.now(UTC) + timedelta(days=90)).isoformat()
    data = await _authed_post(slug, '/auth', json={
        'access': {'valid_until': valid_until},
        'aspsp': {'name': KBC_ASPSP_NAME, 'country': KBC_COUNTRY},
        'state': f'marcel-{slug}',
        'redirect_url': redirect_url,
        'psu_type': 'personal',
    })
    log.info('Started KBC authorization for user %s', slug)
    return data


async def create_session(slug: str, auth_code: str) -> dict[str, Any]:
    """Exchange an authorization code for a session.

    Persists the session_id in the user's credential store.
    Returns the full session response including account list.
    """
    data = await _authed_post(slug, '/sessions', json={'code': auth_code})
    session_id = data.get('session_id', '')
    if session_id:
        creds = load_credentials(slug)
        creds['ENABLEBANKING_SESSION_ID'] = session_id
        save_credentials(slug, creds)
        log.info('Created EnableBanking session %s for user %s', session_id, slug)
    return data


async def get_session(slug: str) -> dict[str, Any]:
    """Return the current session status and account list."""
    sid = _session_id(slug)
    return await _authed_get(slug, f'/sessions/{sid}')


async def list_accounts(slug: str) -> list[dict[str, Any]]:
    """Return account data from the current session."""
    session = await get_session(slug)
    return session.get('accounts', [])


async def get_balances(slug: str, account_uid: str) -> list[dict[str, Any]]:
    """Return balance entries for a specific account."""
    data = await _authed_get(slug, f'/accounts/{account_uid}/balances')
    return data.get('balances', [])


async def get_transactions(
    slug: str,
    account_uid: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    continuation_key: str | None = None,
) -> dict[str, Any]:
    """Return transactions for an account.

    Returns a dict with ``transactions`` list and optional
    ``continuation_key`` for pagination.
    """
    params: dict[str, str] = {}
    if date_from:
        params['date_from'] = date_from
    if date_to:
        params['date_to'] = date_to
    if continuation_key:
        params['continuation_key'] = continuation_key
    return await _authed_get(
        slug, f'/accounts/{account_uid}/transactions', params=params,
    )


async def get_all_transactions(
    slug: str,
    account_uid: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch all transactions, handling pagination automatically."""
    all_txs: list[dict[str, Any]] = []
    cont_key: str | None = None

    while True:
        data = await get_transactions(
            slug, account_uid,
            date_from=date_from, date_to=date_to,
            continuation_key=cont_key,
        )
        all_txs.extend(data.get('transactions', []))
        cont_key = data.get('continuation_key')
        if not cont_key:
            break

    return all_txs
