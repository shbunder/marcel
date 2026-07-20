"""The ``marcel`` tool — one entry point that routes to many actions.

This is the only function advertised to the pydantic-ai agent. It delegates
to the per-action implementations in sibling modules.
"""

from __future__ import annotations

import logging

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps

from .conversations import compact as _compact, search_conversations as _search_conversations
from .notifications import notify as _notify
from .settings import get_model as _get_model, list_models as _list_models, set_model as _set_model
from .ui import render as _render

log = logging.getLogger(__name__)


async def marcel(
    ctx: RunContext[MarcelDeps],
    action: str,
    name: str | None = None,
    query: str | None = None,
    message: str | None = None,
    max_results: int | None = None,
    component: str | None = None,
    props: dict | None = None,
) -> str:
    """Marcel's internal utilities for managing skills, conversations, and settings.

    (Memory moved to its own tools — use ``write_memory`` / ``read_memory`` /
    ``search_memory`` / ``delete_memory`` from the Memory capability.)

    Actions:
      search_conversations Search past conversation history (query= required).
      compact              Compress current conversation segment into a summary.
      notify               Send a progress update to the user (message= required).
      list_models          List all available AI models.
      get_model            Get the current model for a channel (name= required, pass channel name).
      set_model            Set the model for a channel (name= required as "channel:provider:model", e.g. "telegram:anthropic:claude-opus-4-6").
      render               Render an A2UI component (component= required, props= required as dict matching the component schema).

    Args:
        ctx: Agent context with user and conversation info.
        action: The action to perform (see above).
        name: Channel name for get_model; "channel:provider:model" for set_model; optional title for render.
        query: Search query for search_conversations.
        message: Progress message for notify.
        max_results: Max results for search_conversations (default 5).
        component: Component name for render (e.g. "transaction_list", "balance_card").
        props: Component props for render — a dict matching the component's JSON Schema.

    Returns:
        Action result string.
    """
    match action:
        case 'read_skill' | 'read_skill_resource':
            return (
                f'The {action!r} action was retired — skills are now deferred capabilities. '
                'Load a skill with the load_capability tool (its docs arrive as instructions); '
                "read a skill resource with that skill's read_skill_resource tool once loaded."
            )
        case 'search_memory' | 'read_memory' | 'save_memory':
            return (
                f'The {action!r} action moved to dedicated tools — use write_memory / '
                'read_memory / search_memory / delete_memory directly.'
            )
        case 'search_conversations':
            return await _search_conversations(ctx, query, max_results)
        case 'compact':
            return await _compact(ctx)
        case 'notify':
            return await _notify(ctx, message)
        case 'list_models':
            return _list_models()
        case 'get_model':
            return _get_model(ctx, name)
        case 'set_model':
            return _set_model(ctx, name)
        case 'render':
            return await _render(ctx, component, props, title=name)
        case _:
            return (
                f'Unknown action: {action!r}. '
                f'Available: search_conversations, compact, notify, list_models, get_model, set_model, render'
            )
