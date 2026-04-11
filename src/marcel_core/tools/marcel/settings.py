"""Model/settings actions for the ``marcel`` tool."""

from __future__ import annotations

import logging

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps

log = logging.getLogger(__name__)


def list_models() -> str:
    """Return a formatted list of available models."""
    from marcel_core.harness.agent import DEFAULT_MODEL, all_models

    models = all_models()
    lines = ['Available models:\n']
    for model_id, display_name in models.items():
        lines.append(f'  {model_id} \u2014 {display_name}')
    lines.append(f'\nDefault: {DEFAULT_MODEL}')
    return '\n'.join(lines)


def get_model(ctx: RunContext[MarcelDeps], channel: str | None) -> str:
    """Return the active model for the given channel."""
    if not channel:
        # Default to the current channel
        channel = ctx.deps.channel

    from marcel_core.harness.agent import DEFAULT_MODEL
    from marcel_core.storage.settings import load_channel_model

    log.info('[marcel:get_model] user=%s channel=%s', ctx.deps.user_slug, channel)
    model = load_channel_model(ctx.deps.user_slug, channel) or DEFAULT_MODEL
    return f'Current model for {channel}: {model}'


def set_model(ctx: RunContext[MarcelDeps], value: str | None) -> str:
    """Set the preferred model for a channel.

    Args:
        value: "channel:model" string, e.g. "telegram:claude-opus-4-6".
    """
    if not value or ':' not in value:
        return 'Error: name= must be "channel:model" (e.g. "telegram:claude-opus-4-6").'

    channel, model = value.split(':', 1)
    channel = channel.strip()
    model = model.strip()

    if not channel or not model:
        return 'Error: both channel and model are required (e.g. "telegram:claude-opus-4-6").'

    from marcel_core.harness.agent import all_models
    from marcel_core.storage.settings import save_channel_model

    available = all_models()
    if model not in available:
        model_list = ', '.join(available.keys())
        return f'Error: unknown model {model!r}. Available: {model_list}'

    log.info('[marcel:set_model] user=%s channel=%s model=%s', ctx.deps.user_slug, channel, model)
    save_channel_model(ctx.deps.user_slug, channel, model)
    display_name = available[model]
    return f'Model for {channel} set to {model} ({display_name})'
