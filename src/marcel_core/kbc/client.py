"""GoCardless Bank Account Data API client.

Handles authentication (secret_id + secret_key → JWT access token),
requisition management, and account data retrieval (balances, transactions).

Credentials are read from the user's credential store:
    GOCARDLESS_SECRET_ID, GOCARDLESS_SECRET_KEY
    GOCARDLESS_REQUISITION_ID (stored after initial bank link)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from marcel_core.storage.credentials import load_credentials, save_credentials

log = logging.getLogger(__name__)

_BASE_URL = 'https://bankaccountdata.gocardless.com/api/v2'

# KBC Belgium institution ID in GoCardless
KBC_INSTITUTION_ID = 'KBC_KREDBEBB'

# Access token buffer — refresh 5 minutes before actual expiry
_TOKEN_BUFFER = 300


@dataclass
class _TokenCache:
    """In-memory cache for the GoCardless access/refresh tokens."""

    access: str = ''
    refresh: str = ''
    access_expires_at: float = 0.0
    refresh_expires_at: float = 0.0


# Per-slug token caches (process-lifetime)
_tokens: dict[str, _TokenCache] = {}


def _creds(slug: str) -> tuple[str, str]:
    """Return (secret_id, secret_key) from the user's credential store."""
    creds = load_credentials(slug)
    sid = creds.get('GOCARDLESS_SECRET_ID', '').strip()
    skey = creds.get('GOCARDLESS_SECRET_KEY', '').strip()
    if not sid or not skey:
        raise RuntimeError(f'GOCARDLESS_SECRET_ID and GOCARDLESS_SECRET_KEY must be set in credentials for user {slug}')
    return sid, skey


def _requisition_id(slug: str) -> str:
    """Return the stored requisition ID, or raise if not linked yet."""
    creds = load_credentials(slug)
    req_id = creds.get('GOCARDLESS_REQUISITION_ID', '').strip()
    if not req_id:
        raise RuntimeError('No KBC bank link found. Run kbc.setup first to link your bank account.')
    return req_id


async def _ensure_token(slug: str, client: httpx.AsyncClient) -> str:
    """Return a valid access token, refreshing or re-authenticating as needed."""
    cache = _tokens.get(slug, _TokenCache())
    _tokens[slug] = cache
    now = time.time()

    # Access token still valid?
    if cache.access and now < cache.access_expires_at - _TOKEN_BUFFER:
        return cache.access

    # Try refresh token
    if cache.refresh and now < cache.refresh_expires_at - _TOKEN_BUFFER:
        resp = await client.post(f'{_BASE_URL}/token/refresh/', json={'refresh': cache.refresh})
        resp.raise_for_status()
        data = resp.json()
        cache.access = data['access']
        cache.access_expires_at = now + data['access_expires']
        return cache.access

    # Full re-auth
    secret_id, secret_key = _creds(slug)
    resp = await client.post(
        f'{_BASE_URL}/token/new/',
        json={'secret_id': secret_id, 'secret_key': secret_key},
    )
    resp.raise_for_status()
    data = resp.json()
    cache.access = data['access']
    cache.refresh = data['refresh']
    cache.access_expires_at = now + data['access_expires']
    cache.refresh_expires_at = now + data['refresh_expires']
    return cache.access


async def _authed_get(slug: str, path: str, *, params: dict[str, str] | None = None) -> Any:
    """Make an authenticated GET request to the GoCardless API."""
    async with httpx.AsyncClient(timeout=30) as client:
        token = await _ensure_token(slug, client)
        resp = await client.get(
            f'{_BASE_URL}{path}',
            headers={'Authorization': f'Bearer {token}'},
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def _authed_post(slug: str, path: str, *, json: dict[str, Any]) -> Any:
    """Make an authenticated POST request to the GoCardless API."""
    async with httpx.AsyncClient(timeout=30) as client:
        token = await _ensure_token(slug, client)
        resp = await client.post(
            f'{_BASE_URL}{path}',
            headers={'Authorization': f'Bearer {token}'},
            json=json,
        )
        resp.raise_for_status()
        return resp.json()


# ── Public API ───────────────────────────────────────────────────────────────


async def create_requisition(slug: str, redirect_url: str = 'https://gocardless.com') -> dict[str, Any]:
    """Create a new requisition for KBC Belgium.

    Returns the full requisition object including the ``link`` field that
    the user must open to authenticate with KBC Mobile.
    """
    data = await _authed_post(
        slug,
        '/requisitions/',
        json={
            'institution_id': KBC_INSTITUTION_ID,
            'redirect': redirect_url,
            'reference': f'marcel-{slug}',
            'user_language': 'en',
        },
    )
    # Persist the requisition ID
    creds = load_credentials(slug)
    creds['GOCARDLESS_REQUISITION_ID'] = data['id']
    save_credentials(slug, creds)
    log.info('Created KBC requisition %s for user %s', data['id'], slug)
    return data


async def get_requisition_status(slug: str) -> dict[str, Any]:
    """Return the current requisition status and linked accounts."""
    req_id = _requisition_id(slug)
    return await _authed_get(slug, f'/requisitions/{req_id}/')


async def list_accounts(slug: str) -> list[dict[str, Any]]:
    """Return metadata for all accounts linked via the current requisition."""
    req = await get_requisition_status(slug)
    account_ids: list[str] = req.get('accounts', [])
    accounts = []
    for aid in account_ids:
        meta = await _authed_get(slug, f'/accounts/{aid}/')
        details = await _authed_get(slug, f'/accounts/{aid}/details/')
        merged = {**meta, **details.get('account', {})}
        accounts.append(merged)
    return accounts


async def get_balances(slug: str, account_id: str) -> list[dict[str, Any]]:
    """Return balance entries for a specific account."""
    data = await _authed_get(slug, f'/accounts/{account_id}/balances/')
    return data.get('balances', [])


async def get_transactions(
    slug: str,
    account_id: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return transactions for an account.

    Returns a dict with ``booked`` and ``pending`` transaction lists.
    Dates should be ISO format (YYYY-MM-DD).
    """
    params: dict[str, str] = {}
    if date_from:
        params['date_from'] = date_from
    if date_to:
        params['date_to'] = date_to
    data = await _authed_get(slug, f'/accounts/{account_id}/transactions/', params=params)
    return data.get('transactions', {'booked': [], 'pending': []})


async def get_agreement_details(slug: str) -> dict[str, Any] | None:
    """Return the end-user agreement details for the current requisition.

    Returns None if no agreement is linked.
    """
    req = await get_requisition_status(slug)
    agreement_id = req.get('agreement')
    if not agreement_id:
        return None
    return await _authed_get(slug, f'/agreements/enduser/{agreement_id}/')
