"""Post-install health check (FEAT-260707-6130cd, STORY-533eb0, FR4).

``run_doctor`` reports the state an admin needs after install or at any time:
the server responds, the Telegram webhook is registered (if a bot token is
set), the zoo resolves, and users have profiles. Returns ``(exit_code,
report)`` — exit 0 when every hard check passes — so it is scriptable from
``setup.sh --check`` and ``make doctor``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

_HEALTH_TIMEOUT = 5.0
_API = 'https://api.telegram.org'


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    hard: bool = True  # a failed hard check fails the doctor's exit code


def _icon(c: Check) -> str:
    return '✅' if c.ok else ('❌' if c.hard else '⚠️')


async def run_doctor(health_url: str | None = None) -> tuple[int, str]:
    """Run every check and render a human-readable report."""
    checks: list[Check] = [
        await _check_server(health_url),
        await _check_webhook(),
        _check_zoo(),
        _check_users(),
    ]
    lines = [f'{_icon(c)} {c.name}: {c.detail}' for c in checks]
    failed = [c for c in checks if not c.ok and c.hard]
    exit_code = 1 if failed else 0
    header = 'Marcel is healthy.' if exit_code == 0 else f'{len(failed)} check(s) need attention.'
    return exit_code, header + '\n' + '\n'.join(lines)


async def _check_server(health_url: str | None) -> Check:
    import httpx

    from marcel_core.config import settings

    url = health_url or f'http://localhost:{settings.marcel_port}/health'
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
            resp = await client.get(url)
        if resp.status_code == 200:
            return Check('server', True, f'responding at {url}')
        return Check('server', False, f'{url} returned HTTP {resp.status_code} — check `make docker-logs`.')
    except httpx.HTTPError as exc:
        return Check('server', False, f'unreachable at {url} ({type(exc).__name__}) — is the container up?')


async def _check_webhook() -> Check:
    import httpx

    from marcel_core.config import settings

    if not settings.telegram_bot_token:
        return Check('telegram', True, 'no bot token set — Telegram not configured (optional).', hard=False)
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
            resp = await client.post(f'{_API}/bot{settings.telegram_bot_token}/getWebhookInfo', json={})
            resp.raise_for_status()
            info = resp.json().get('result') or {}
    except httpx.HTTPError as exc:
        return Check('telegram', False, f'getWebhookInfo failed ({type(exc).__name__}) — bad token or no network.')
    url = info.get('url', '')
    if not url:
        return Check('telegram', False, 'bot token set but no webhook registered — run `make telegram-setup`.')
    pending = info.get('pending_update_count', 0)
    last_err = info.get('last_error_message')
    detail = f'registered at {url} (pending {pending})'
    if last_err:
        return Check('telegram', False, f'{detail} — last error: {last_err}')
    return Check('telegram', True, detail)


def _check_zoo() -> Check:
    from marcel_core.config import settings

    zoo = settings.zoo_dir
    if zoo is None:
        return Check('zoo', False, 'MARCEL_ZOO_DIR unset — no habitats will load. Run `make zoo-setup`.', hard=False)
    if not zoo.is_dir():
        return Check('zoo', False, f'MARCEL_ZOO_DIR points at {zoo}, which is not a directory. Run `make zoo-setup`.')
    kinds = [k for k in ('skills', 'connectors', 'channels', 'agents', 'jobs') if (zoo / k).is_dir()]
    return Check('zoo', True, f'{zoo} ({", ".join(kinds) or "empty"})')


def _check_users() -> Check:
    from marcel_core.storage.paths import list_user_slugs, user_dir

    slugs = [s for s in list_user_slugs() if not s.startswith('_')]
    if not slugs:
        return Check('users', False, 'no users yet — onboard one with `make add-user USER=<slug>`.', hard=False)
    missing = [s for s in slugs if not (user_dir(s) / 'profile.md').is_file()]
    if missing:
        return Check('users', False, f'{len(slugs)} user(s); missing profile.md: {", ".join(missing)}.')
    return Check('users', True, f'{len(slugs)} user(s) with profiles: {", ".join(slugs)}')
