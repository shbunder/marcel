"""A2UI component rendering action for the ``marcel`` tool.

The ``render`` action lets the agent emit a declarative UI component — a
``transaction_list``, ``calendar``, or any other component declared in a
skill's ``components.yaml``. The component and its props are validated
against the :class:`~marcel_core.skills.component_registry.ComponentRegistry`,
stored as an ``a2ui`` artifact, and (on Telegram) delivered immediately as a
"View in app" Mini App button — following the same side-effect pattern as
:func:`marcel_core.tools.charts.generate_chart`.

Channels without a rich-UI frontend see the returned confirmation text but no
button; the artifact is still persisted so other clients can open it later.
"""

from __future__ import annotations

import json
import logging

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps

log = logging.getLogger(__name__)


async def render(
    ctx: RunContext[MarcelDeps],
    component: str | None,
    props: dict | None,
    title: str | None = None,
) -> str:
    """Render an A2UI component to the user's rich-UI surface.

    Validates the component name against the loaded component registry,
    stores the payload as an artifact, and delivers a "View in app" button
    on Telegram (side-effect). For other channels the artifact is persisted
    and its id is returned so the frontend can fetch it on its own.

    Args:
        ctx: Agent context with user and conversation info.
        component: Component name as declared in a skill's ``components.yaml``
            (e.g. ``'transaction_list'``, ``'balance_card'``).
        props: Component props matching the component's JSON Schema.
        title: Optional short title for the artifact (shown in the Mini App
            header). Defaults to a humanized form of the component name.

    Returns:
        A short confirmation string with the artifact id, or an error
        message the agent can relay to the user if validation failed.
    """
    if not component:
        return "render failed: 'component' is required (e.g. 'transaction_list')"

    if props is None:
        props = {}

    # Validate against the component registry. Keep the error message tight
    # so the agent can correct itself on retry.
    try:
        from marcel_core.skills.component_registry import build_registry

        registry = build_registry(ctx.deps.user_slug)
    except Exception as exc:
        log.exception('[marcel:render] failed to build component registry')
        return f'render failed: could not load component registry ({exc})'

    schema = registry.get(component)
    if schema is None:
        available = ', '.join(sorted(c.name for c in registry.list_all())) or '(none)'
        return f'render failed: unknown component {component!r}. Available: {available}'

    # Store as an a2ui artifact. Content is the JSON-encoded props dict;
    # the Mini App viewer parses this back into an object before handing it
    # to A2UIRenderer together with the schema fetched from /api/components.
    try:
        content_json = json.dumps(props, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        return f'render failed: props could not be serialized to JSON ({exc})'

    resolved_title = title or _default_title(component)

    try:
        from marcel_core.storage.artifacts import create_artifact

        artifact_id = create_artifact(
            user_slug=ctx.deps.user_slug,
            conversation_id=ctx.deps.conversation_id or '',
            content_type='a2ui',
            content=content_json,
            title=resolved_title,
            component_name=component,
        )
    except Exception as exc:
        log.exception('[marcel:render] failed to create artifact')
        return f'render failed: could not store artifact ({exc})'

    # Telegram: deliver immediately as a Mini App button. Matches the
    # side-effect pattern used by generate_chart() so the agent only needs
    # to make one tool call.
    if ctx.deps.channel == 'telegram':
        try:
            from marcel_core.plugin import get_channel

            channel = get_channel('telegram')
            if channel is not None and await channel.send_artifact_link(
                ctx.deps.user_slug, artifact_id, resolved_title
            ):
                return f'rendered component {component!r} as artifact {artifact_id}; Mini App button sent to Telegram'
        except Exception as exc:
            log.warning('[marcel:render] failed to deliver Telegram button: %s', exc)
            return (
                f'rendered component {component!r} as artifact {artifact_id}, but failed to send Telegram button: {exc}'
            )

    return f'rendered component {component!r} as artifact {artifact_id}'


def _default_title(component: str) -> str:
    """Derive a human-readable title from a snake_case component name."""
    return component.replace('_', ' ').strip().title() or 'Component'
