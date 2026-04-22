"""Fixture habitat for the UDS sidecar end-to-end tests.

Loaded by :mod:`marcel_core.plugin._uds_bridge` inside the spawned
habitat subprocess. Registers three handlers exercising the three
response shapes (success, numeric, error).
"""

from __future__ import annotations

import asyncio

from marcel_core.plugin import register


@register('uds_fixture.echo')
async def echo(params: dict, user_slug: str) -> str:
    """Return the ``message`` param verbatim with the user slug appended.

    Exercises: simple string result, params pass-through, user_slug pass-through.
    """
    message = params.get('message', '<no message>')
    return f'{message} (for {user_slug})'


@register('uds_fixture.add')
async def add(params: dict, user_slug: str) -> str:
    """Sum two params. Small ``await`` to prove the accept loop is concurrent.

    The sleep guarantees that when two clients call this handler simultaneously,
    their responses genuinely overlap in the habitat's event loop.
    """
    await asyncio.sleep(0.05)
    return str(int(params['a']) + int(params['b']))


@register('uds_fixture.boom')
async def boom(params: dict, user_slug: str) -> str:
    """Deliberately raise, so tests can verify error-frame propagation."""
    raise ValueError(f'kaboom ({params.get("tag", "")})')
