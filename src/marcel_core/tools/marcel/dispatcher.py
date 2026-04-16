"""The ``marcel`` tool — one entry point that routes to many actions.

This is the only function advertised to the pydantic-ai agent. It delegates
to the per-action implementations in sibling modules.
"""

from __future__ import annotations

import logging

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps

from .conversations import compact as _compact, search_conversations as _search_conversations
from .memory import (
    read_memory as _read_memory,
    save_memory as _save_memory,
    search_memory as _search_memory,
)
from .notifications import notify as _notify
from .settings import get_model as _get_model, list_models as _list_models, set_model as _set_model
from .skills import read_skill as _read_skill, read_skill_resource as _read_skill_resource
from .ui import render as _render

log = logging.getLogger(__name__)


async def marcel(
    ctx: RunContext[MarcelDeps],
    action: str,
    name: str | None = None,
    query: str | None = None,
    message: str | None = None,
    type_filter: str | None = None,
    max_results: int | None = None,
    component: str | None = None,
    props: dict | None = None,
    resource: str | None = None,
) -> str:
    """Marcel's internal utilities for managing skills, memory, conversations, and settings.

    Actions:
      read_skill           Load full documentation for a skill (name= required).
      read_skill_resource  Load a named resource file from a skill directory (name= skill, resource= filename or stem).
      search_memory        Search memory files by keyword (query= required).
      read_memory          Load the full content of a specific memory file (name= required, from the memory index).
      save_memory          Save a memory file (name= required as filename, message= required as file content including frontmatter).
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
        name: Skill name for read_skill / read_skill_resource; filename for save_memory; channel name for get_model; "channel:provider:model" for set_model; optional title for render.
        query: Search query for search_memory / search_conversations.
        message: Progress message for notify; file content for save_memory.
        type_filter: Optional memory type filter for search_memory.
        max_results: Max results for search actions (default: 10 for memory, 5 for conversations).
        component: Component name for render (e.g. "transaction_list", "balance_card").
        props: Component props for render — a dict matching the component's JSON Schema.
        resource: Resource filename or stem for read_skill_resource (e.g. "feeds", "feeds.yaml", "SETUP.md").

    Returns:
        Action result string.
    """
    match action:
        case 'read_skill':
            return await _read_skill(ctx, name)
        case 'read_skill_resource':
            return await _read_skill_resource(ctx, name, resource)
        case 'search_memory':
            return await _search_memory(ctx, query, type_filter, max_results)
        case 'read_memory':
            return _read_memory(ctx, name)
        case 'save_memory':
            return _save_memory(ctx, name, message)
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
                f'Available: read_skill, read_skill_resource, search_memory, read_memory, save_memory, '
                f'search_conversations, compact, notify, list_models, get_model, set_model, render'
            )
