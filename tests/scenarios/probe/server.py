"""Scenario probe park — generic tools whose behavior each test scripts."""

from __future__ import annotations

import inspect

from fastmcp import FastMCP

from tests.scenarios import probe_hooks


async def _dispatch(name: str, /, **kwargs) -> str:
    fn = probe_hooks.HANDLERS.get(name)
    if fn is None:
        return f'probe {name}: no hook installed'
    result = fn(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    return str(result)


def build(user_slug: str) -> FastMCP:
    mcp = FastMCP('probe')

    @mcp.tool
    async def ping(text: str = '') -> str:
        """Echo probe."""
        return await _dispatch('ping', text=text, user_slug=user_slug)

    @mcp.tool
    async def leak() -> str:
        """Returns whatever the test scripts (rewrite-probe)."""
        return await _dispatch('leak', user_slug=user_slug)

    @mcp.tool
    async def note(text: str = '') -> str:
        """Persistence probe."""
        return await _dispatch('note', text=text, user_slug=user_slug)

    @mcp.tool
    async def fetch(url: str = '') -> str:
        """Outbound-HTTP probe (drives the terrarium fake API)."""
        return await _dispatch('fetch', url=url, user_slug=user_slug)

    @mcp.tool
    async def big(n: int = 100000) -> str:
        """Oversized-result probe."""
        return await _dispatch('big', n=n, user_slug=user_slug)

    return mcp
