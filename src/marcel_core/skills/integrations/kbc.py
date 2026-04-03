"""Banking integration — account, balance, and transaction access.

Registers ``kbc.setup``, ``kbc.complete_setup``, ``kbc.accounts``,
``kbc.balance``, ``kbc.transactions``, ``kbc.status``, and ``kbc.sync``
as python integration skills, callable through the ``integration`` tool.

Supports multiple banks (KBC, ING, etc.) via EnableBanking. Data is
served from a local SQLite cache that syncs every 8 hours via the
background sync task in ``marcel_core.kbc.sync``.
"""

from __future__ import annotations

import json

from marcel_core.kbc import cache, client
from marcel_core.kbc.sync import sync_account
from marcel_core.skills.integrations import register


@register('kbc.setup')
async def setup(params: dict, user_slug: str) -> str:
    """Start a bank link flow via EnableBanking.

    Returns the authentication URL the user must open to authorize access.
    """
    bank = params.get('bank', 'KBC').upper()
    country = params.get('country', client.SUPPORTED_BANKS.get(bank, {}).get('country', 'BE'))
    redirect = params.get('redirect_url', 'https://enablebanking.com')
    data = await client.start_authorization(
        user_slug, redirect_url=redirect, bank=bank, country=country,
    )
    return json.dumps(
        {
            'status': 'authorization_started',
            'bank': bank,
            'auth_url': data.get('url', ''),
            'instructions': (
                f'Open the auth_url in your browser to link your {bank} account. '
                f'After authenticating, you will be redirected. '
                f'Copy the full redirect URL and provide it to complete setup '
                f'using kbc.complete_setup with bank="{bank}".'
            ),
        },
        indent=2,
    )


@register('kbc.complete_setup')
async def complete_setup(params: dict, user_slug: str) -> str:
    """Complete the bank link by exchanging the authorization code for a session.

    The code is extracted from the redirect URL query parameter.
    """
    code = params.get('code', '')
    if not code:
        return json.dumps({'error': 'code parameter is required — extract it from the redirect URL'})

    bank = params.get('bank', 'KBC').upper()
    country = params.get('country', client.SUPPORTED_BANKS.get(bank, {}).get('country', 'BE'))
    session = await client.create_session(user_slug, code, bank=bank, country=country)
    accounts = session.get('accounts', [])
    return json.dumps(
        {
            'status': 'linked',
            'bank': bank,
            'session_id': session.get('session_id', ''),
            'accounts': len(accounts),
            'message': f'Successfully linked {len(accounts)} {bank} account(s). Running initial sync...',
        },
        indent=2,
    )


@register('kbc.status')
async def status(params: dict, user_slug: str) -> str:
    """Check the status of all linked bank sessions."""
    sessions = client.get_stored_sessions(user_slug)
    if not sessions:
        return json.dumps({'error': 'No bank links found. Run kbc.setup to link a bank account.'})

    results: list[dict] = []
    for entry in sessions:
        bank_name = entry.get('bank', 'Unknown')
        session_id = entry.get('session_id', '')
        try:
            session = await client.get_session(user_slug, session_id)
            result: dict = {
                'bank': bank_name,
                'status': session.get('status', 'unknown'),
                'accounts': len(session.get('accounts', [])),
                'linked': session.get('status') == 'AUTHORIZED',
            }
            access = session.get('access', {})
            if access.get('valid_until'):
                result['valid_until'] = access['valid_until']
            results.append(result)
        except Exception as e:
            results.append({'bank': bank_name, 'status': 'error', 'error': str(e)})

    warning = cache.get_sync_meta(user_slug, 'consent_warning')
    output: dict = {'banks': results}
    if warning:
        output['consent_warning'] = warning

    return json.dumps(output, indent=2)


@register('kbc.accounts')
async def accounts(params: dict, user_slug: str) -> str:
    """List linked bank accounts across all banks."""
    accts = await client.list_accounts(user_slug)
    return json.dumps(accts, indent=2)


@register('kbc.balance')
async def balance(params: dict, user_slug: str) -> str:
    """Get current balance from the local cache.

    Falls back to a live API call if cache is empty.
    """
    cached = cache.get_balances(user_slug)
    if cached:
        last_sync = cache.get_sync_meta(user_slug, 'last_sync_at')
        return json.dumps({'balances': cached, 'last_synced': last_sync}, indent=2)

    # Cache empty — try live across all sessions
    all_balances: list[dict] = []
    for entry in client.get_stored_sessions(user_slug):
        session_id = entry.get('session_id', '')
        if not session_id:
            continue
        try:
            session = await client.get_session(user_slug, session_id)
            for account in session.get('accounts', []):
                uid = account if isinstance(account, str) else account.get('uid', '')
                if uid:
                    bals = await client.get_balances(user_slug, uid)
                    cache.upsert_balances(user_slug, uid, bals)
                    all_balances.extend(bals)
        except Exception:
            pass
    return json.dumps({'balances': all_balances, 'source': 'live'}, indent=2)


@register('kbc.transactions')
async def transactions(params: dict, user_slug: str) -> str:
    """Query cached transactions.

    All parameters are optional — returns the most recent transactions
    by default.  The agent should set appropriate filters based on the
    user's natural language question.
    """
    date_from = params.get('date_from')
    date_to = params.get('date_to')
    search = params.get('search')
    min_amount = float(params['min_amount']) if params.get('min_amount') else None
    max_amount = float(params['max_amount']) if params.get('max_amount') else None
    limit = int(params.get('limit', '200'))

    rows = cache.get_transactions(
        user_slug,
        date_from=date_from,
        date_to=date_to,
        search=search,
        min_amount=min_amount,
        max_amount=max_amount,
        limit=limit,
    )

    last_sync = cache.get_sync_meta(user_slug, 'last_sync_at')
    return json.dumps({'transactions': rows, 'count': len(rows), 'last_synced': last_sync}, indent=2)


@register('kbc.sync')
async def manual_sync(params: dict, user_slug: str) -> str:
    """Trigger an immediate sync of transactions and balances from all linked banks."""
    summary = await sync_account(user_slug)
    return json.dumps(summary, indent=2)
