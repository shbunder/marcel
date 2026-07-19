"""Connector OAuth callback route (FEAT-260718-230bf8).

The provider redirects the user's **browser** here after they approve access, so
this endpoint is family-facing: every outcome renders a short, plain-language
HTML page. It never surfaces a stack trace, and it never echoes the code, state
or any token back into the page (NFR1/NFR3).

The user's identity is *not* taken from the request — it is read from the
server-side pending record that ``state`` unlocks (see
:mod:`marcel_core.connectors.oauth`), so a forged callback cannot bind a
connection to somebody else's account.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from marcel_core.connectors.loader import get_connector
from marcel_core.connectors.oauth import ConnectorOAuth, OAuthError

log = logging.getLogger(__name__)

router = APIRouter()

# One shared flow object so the in-memory pending-link store (which holds the
# PKCE verifiers) is the same one ``start_link`` wrote to.
_flow = ConnectorOAuth()


def get_flow() -> ConnectorOAuth:
    """The process-wide linking flow (shared pending store)."""
    return _flow


def _page(title: str, message: str, *, ok: bool) -> HTMLResponse:
    tone = '#137333' if ok else '#b3261e'
    body = (
        '<!doctype html><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{title}</title>'
        '<style>body{font-family:system-ui,sans-serif;margin:0;display:grid;'
        'place-items:center;min-height:100vh;background:#faf9f7;color:#1f1f1f}'
        '.card{max-width:28rem;padding:2rem;text-align:center}'
        f'h1{{font-size:1.25rem;margin:0 0 .5rem;color:{tone}}}'
        'p{margin:0;line-height:1.5;color:#444}</style>'
        f'<div class="card"><h1>{title}</h1><p>{message}</p></div>'
    )
    return HTMLResponse(body, status_code=200 if ok else 400)


def _lookup(name: str, slug: str):
    """Resolve a connector for the linking user (admin scope included)."""
    doc = get_connector(name, slug, role='admin')
    return doc.config if doc is not None else None


@router.get('/connectors/callback', response_class=HTMLResponse)
async def connectors_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> HTMLResponse:
    """Finish an OAuth link started by ``ConnectorOAuth.start_link``."""
    if error:
        # The provider declined — log the code, not the (attacker-controllable) text.
        log.info('connectors: callback returned provider error %r', error)
        return _page(
            'Connection cancelled',
            'The service did not grant access. You can close this tab and try connecting again.',
            ok=False,
        )
    if not code or not state:
        return _page(
            'Something was missing',
            'That link was incomplete. Please start connecting again from your chat with Marcel.',
            ok=False,
        )

    try:
        _slug, connector = await get_flow().complete_link(_lookup, code, state)
    except OAuthError as exc:
        return _page('Could not connect', str(exc), ok=False)
    except Exception:
        # Never leak internals to a family-facing page.
        log.exception('connectors: unexpected failure completing OAuth link')
        return _page(
            'Something went wrong',
            'Marcel could not finish connecting that account. Please try again in a moment.',
            ok=False,
        )

    return _page(
        'Connected',
        f'{connector} is now connected. You can close this tab and go back to your chat with Marcel.',
        ok=True,
    )
