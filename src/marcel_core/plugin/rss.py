"""RSS/Atom feed fetcher for plugin habitats.

Habitats that consume syndication feeds (news sync, release trackers, etc.)
should call :func:`fetch_feed` from here rather than reaching into
``marcel_core.tools.rss``. The programmatic entry point returns a list of
dicts with keys ``title``, ``link``, ``description``, ``published``,
``category``; malformed or non-XML responses raise :class:`ValueError` so
the caller can log a one-line warning without a traceback.

This module is part of the stable plugin surface — the function name and
return shape here won't change between Marcel versions without a migration
note.

Example::

    from marcel_core.plugin import rss

    articles = await rss.fetch_feed("https://example.com/feed.xml")
    for art in articles:
        print(art["title"], art["link"])
"""

from __future__ import annotations

from marcel_core.tools.rss import fetch_feed

__all__ = ['fetch_feed']
