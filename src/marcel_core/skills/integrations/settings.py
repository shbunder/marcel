"""Settings integration: per-channel model selection.

Registered skills:
- ``settings.list_models``  — list available models
- ``settings.get_model``    — get the current model for this channel
- ``settings.set_model``    — set the model for this channel
"""

from __future__ import annotations

from marcel_core.harness.agent import all_models, default_model
from marcel_core.storage.settings import load_channel_model, save_channel_model

from . import register


@register('settings.list_models')
async def list_models(params: dict, user_slug: str) -> str:
    """Return a formatted list of available models."""
    models = all_models()
    lines = ['Available models:\n']
    for model_id, display_name in models.items():
        lines.append(f'  {model_id} — {display_name}')
    lines.append(f'\nDefault: {default_model()}')
    return '\n'.join(lines)


@register('settings.get_model')
async def get_model(params: dict, user_slug: str) -> str:
    """Return the active model for the given channel.

    Params:
        channel: The channel to query (e.g. 'telegram', 'cli').
    """
    channel = params.get('channel', '')
    if not channel:
        return 'Error: channel parameter is required'
    model = load_channel_model(user_slug, channel) or default_model()
    return f'Current model for {channel}: {model}'


@register('settings.set_model')
async def set_model(params: dict, user_slug: str) -> str:
    """Set the preferred model for a channel.

    Params:
        channel: The channel to update (e.g. 'telegram', 'cli').
        model:   Fully-qualified pydantic-ai model string
                 (e.g. 'anthropic:claude-sonnet-4-6', 'openai:gpt-4o').
                 Any pydantic-ai-supported provider:model is accepted.
    """
    channel = params.get('channel', '')
    model = params.get('model', '')

    if not channel:
        return 'Error: channel parameter is required'
    if not model:
        return 'Error: model parameter is required'

    if ':' not in model:
        return (
            f'Error: model {model!r} must be fully qualified as "provider:model" '
            '(e.g. "anthropic:claude-sonnet-4-6", "openai:gpt-4o").'
        )
    provider, model_id = model.split(':', 1)
    if not provider.strip() or not model_id.strip():
        return f'Error: model {model!r} must have non-empty provider and model halves.'

    save_channel_model(user_slug, channel, model)
    display_name = all_models().get(model, '(off-registry)')
    return f'Model for {channel} set to {model} ({display_name})'
