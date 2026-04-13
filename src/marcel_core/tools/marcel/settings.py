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
        value: ``"channel:provider:model"`` string, e.g.
            ``"telegram:anthropic:claude-opus-4-6"``. Split on the first
            ``:`` to separate channel from the fully-qualified pydantic-ai
            model identifier (which itself contains a ``:``). Any
            pydantic-ai-supported ``provider:model`` is accepted.
    """
    if not value or ':' not in value:
        return 'Error: name= must be "channel:provider:model" (e.g. "telegram:anthropic:claude-opus-4-6").'

    channel, model = value.split(':', 1)
    channel = channel.strip()
    model = model.strip()

    if not channel or not model:
        return 'Error: both channel and model are required (e.g. "telegram:anthropic:claude-opus-4-6").'

    if ':' not in model:
        return (
            f'Error: model {model!r} must be fully qualified as "provider:model" '
            '(e.g. "anthropic:claude-sonnet-4-6", "openai:gpt-4o").'
        )
    provider, model_id = model.split(':', 1)
    if not provider.strip() or not model_id.strip():
        return f'Error: model {model!r} must have non-empty provider and model halves.'

    from marcel_core.harness.agent import all_models
    from marcel_core.storage.settings import save_channel_model

    log.info('[marcel:set_model] user=%s channel=%s model=%s', ctx.deps.user_slug, channel, model)
    save_channel_model(ctx.deps.user_slug, channel, model)
    display_name = all_models().get(model, '(off-registry)')
    return f'Model for {channel} set to {model} ({display_name})'
