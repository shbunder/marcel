"""The ``marketplace`` admin tool — conversational browse/install/update/remove.

Admin-tier by registry declaration (structurally invisible to non-admins;
the event-bus gate covers it as layer 2). Install and update are two-step:
``review``/``review_update`` render the summary and return the token the
mutation requires — show the summary to the admin and get an explicit yes
before calling ``install``/``update``. ``remove`` is single-step
(reverting the removal commit restores everything).

All flow work is blocking (git subprocesses, HTTP) and runs in a worker
thread so the event loop never stalls.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps

log = logging.getLogger(__name__)

_ACTIONS = ('sources', 'browse', 'review', 'install', 'review_update', 'update', 'remove')


async def marketplace(
    ctx: RunContext[MarcelDeps],
    action: str,
    *,
    source: str | None = None,
    name: str | None = None,
    kind: str | None = None,
    token: str | None = None,
    placement_slug: str | None = None,
) -> str:
    """Browse trusted sources and install/update/remove skills and connectors.

    Installation is code execution — every mutation needs a fresh review
    first, and always confirm the review summary with the admin before
    calling the mutating action.

    Args:
        ctx: Agent context.
        action: One of:
            - ``sources`` — list the trusted sources from ``sources.yaml``.
            - ``browse`` — list a source's candidates (requires ``source``).
            - ``review`` — render the install review for one candidate
              (requires ``source`` + ``name``); returns the summary and the
              token ``install`` needs.
            - ``install`` — perform a reviewed install (requires ``source``,
              ``name``, ``token``; optional ``placement_slug`` for a
              per-user placement instead of global).
            - ``review_update`` — render the update review for an installed
              habitat (requires ``kind`` + ``name``).
            - ``update`` — apply a reviewed update (requires ``kind``,
              ``name``, ``token``).
            - ``remove`` — uninstall (requires ``kind`` + ``name``); commits
              the removal and cleans the enablement manifest.
        source: Source name from ``sources.yaml``.
        name: Candidate / installed habitat name.
        kind: ``skill`` or ``connector`` (update/remove).
        token: The review token returned by the matching review action.
        placement_slug: Optional user slug for ``users/<slug>/`` placement.

    Returns:
        A human-readable result; errors come back as readable text, never
        exceptions.
    """
    from marcel_core.marketplace import installer
    from marcel_core.marketplace.fetchers import FetchError, browse_source
    from marcel_core.marketplace.sources import get_source, load_sources

    who = {'user_slug': ctx.deps.user_slug, 'channel': ctx.deps.channel}

    if kind is not None and kind not in ('skill', 'connector'):
        return f'marketplace error: kind must be skill or connector, not {kind!r}.'

    def _need(**required: str | None) -> str | None:
        missing = [param for param, value in required.items() if not value]
        if missing:
            return f'marketplace error: action {action!r} requires {", ".join(missing)}.'
        return None

    try:
        match action:
            case 'sources':
                entries = load_sources()
                if not entries:
                    return 'No trusted sources configured — add entries to <zoo>/sources.yaml.'
                return '\n'.join(f'- **{e.name}** ({e.type.value}): {e.description or e.url}' for e in entries)

            case 'browse':
                if err := _need(source=source):
                    return err
                assert source is not None
                candidates = await asyncio.to_thread(browse_source, get_source(source))
                if not candidates:
                    return f'Source {source!r} has no installable candidates.'
                return '\n'.join(
                    f'- **{c.name}** ({c.kind}): {c.description or "(no description)"}'
                    + (' ⚠️ ships scripts' if c.has_scripts else '')
                    for c in candidates
                )

            case 'review':
                if err := _need(source=source, name=name):
                    return err
                assert source is not None and name is not None
                review = await asyncio.to_thread(installer.review_install, source, name, **who)
                return f'{review.summary}\n\nConfirm with the admin, then install with token `{review.token}`.'

            case 'install':
                if err := _need(source=source, name=name, token=token):
                    return err
                assert source is not None and name is not None and token is not None
                result = await asyncio.to_thread(
                    installer.install, source, name, token, placement_slug=placement_slug, **who
                )
                return (
                    f'Installed to {result.path} (zoo commit `{result.commit[:12]}`; '
                    f'enablement: {result.seeded}). Revert the commit to uninstall.'
                )

            case 'review_update':
                if err := _need(kind=kind, name=name):
                    return err
                assert kind is not None and name is not None
                review = await asyncio.to_thread(installer.review_update, kind, name, **who)
                return f'{review.summary}\n\nConfirm with the admin, then update with token `{review.token}`.'

            case 'update':
                if err := _need(kind=kind, name=name, token=token):
                    return err
                assert kind is not None and name is not None and token is not None
                result = await asyncio.to_thread(installer.update, kind, name, token, **who)
                return f'Updated {name} (zoo commit `{result.commit[:12]}`).'

            case 'remove':
                if err := _need(kind=kind, name=name):
                    return err
                assert kind is not None and name is not None
                commit = await asyncio.to_thread(installer.remove, kind, name, **who)
                return f'Removed {name} (zoo commit `{commit[:12]}`); enablement manifest cleaned.'

            case _:
                return f'marketplace error: unknown action {action!r}. Actions: {", ".join(_ACTIONS)}.'
    except (installer.InstallError, FetchError, ValueError) as exc:
        return f'marketplace error: {exc}'
