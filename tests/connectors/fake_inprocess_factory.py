"""A per-user in-process server factory, standing in for a data-scoped park."""

from odile import FakeMCPServer

BUILT: list[str] = []


def build(user_slug: str):
    """One server per user, closed over the slug — the news-park shape."""
    BUILT.append(user_slug)
    return FakeMCPServer(f'clock-{user_slug}').returns('whoami', user_slug).server
