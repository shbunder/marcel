"""Settings integration: per-channel model selection.

Registered skills:
- ``settings.list_models``  — list available models
- ``settings.get_model``    — get the current model for this channel
- ``settings.set_model``    — set the model for this channel
"""

from __future__ import annotations

from marcel_core.harness.agent import DEFAULT_MODEL, all_models
from marcel_core.storage.settings import load_channel_model, save_channel_model

from . import register


@register('settings.list_models')
async def list_models(params: dict, user_slug: str) -> str:
    """Return a formatted list of available models."""
    models = all_models()
    lines = ['Available models:\n']
    for model_id, display_name in models.items():
        lines.append(f'  {model_id} — {display_name}')
    lines.append(f'\nDefault: {DEFAULT_MODEL}')
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
    model = load_channel_model(user_slug, channel) or DEFAULT_MODEL
    return f'Current model for {channel}: {model}'


@register('settings.set_model')
async def set_model(params: dict, user_slug: str) -> str:
    """Set the preferred model for a channel.

    Params:
        channel: The channel to update (e.g. 'telegram', 'cli').
        model:   The model name to use (must be in the available models list).
    """
    channel = params.get('channel', '')
    model = params.get('model', '')

    if not channel:
        return 'Error: channel parameter is required'
    if not model:
        return 'Error: model parameter is required'

    available = all_models()
    if model not in available:
        model_list = ', '.join(available.keys())
        return f'Error: unknown model {model!r}. Available: {model_list}'

    save_channel_model(user_slug, channel, model)
    display_name = available[model]
    return f'Model for {channel} set to {model} ({display_name})'
