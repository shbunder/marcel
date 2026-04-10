"""Built-in job templates for common patterns.

Templates are dictionaries of defaults that the agent uses during
conversational job creation.  The agent picks the right template based on
the user's request, fills in placeholders, and calls ``create_job``.
"""

from __future__ import annotations

from typing import Any

TEMPLATES: dict[str, dict[str, Any]] = {
    'sync': {
        'description': 'Periodically sync data from an external service.',
        'default_trigger': {'type': 'interval', 'interval_seconds': 28800},
        'system_prompt': (
            'You are a background sync worker for Marcel. '
            'Your job is to call the specified integration skill to sync data. '
            'Report a brief summary of what was synced. '
            'If there are warnings or errors, include them clearly.'
        ),
        'task_template': 'Run {skill} now and report the results.',
        'notify': 'on_failure',
        'model': 'claude-haiku-4-5-20251001',
    },
    'check': {
        'description': 'Check a condition and optionally alert the user.',
        'default_trigger': {'type': 'event'},
        'system_prompt': (
            'You are a monitoring worker for Marcel. '
            'Check the specified condition and decide whether to alert the user. '
            'Only send a notification if the condition is met. '
            'Be concise and actionable in your alert.'
        ),
        'task_template': 'Check: {condition}. If true, notify the user: {alert_message}',
        'notify': 'on_output',
        'model': 'claude-haiku-4-5-20251001',
    },
    'scrape': {
        'description': 'Scrape a website for new content on a schedule.',
        'default_trigger': {'type': 'interval', 'interval_seconds': 3600},
        'system_prompt': (
            'You are a content scraper for Marcel. '
            'Use the browser tools to visit the specified URL, '
            'extract relevant content, and return a structured summary. '
            'If login is required, use the provided credentials. '
            'Focus on new content since the last run.'
        ),
        'task_template': 'Scrape {url} for new articles/content. {extra_instructions}',
        'notify': 'silent',
        'model': 'claude-haiku-4-5-20251001',
    },
    'digest': {
        'description': 'Compose a digest message from multiple sources.',
        'default_trigger': {'type': 'cron', 'cron': '0 7 * * *'},
        'system_prompt': (
            "You are Marcel's digest composer. "
            'Gather information from the specified sources and compose a single, '
            'well-formatted message for the user. '
            'Be warm, concise, and useful. Include only actionable or interesting items. '
            'Use the notify tool to send the final digest to the user.'
        ),
        'task_template': 'Compose a {digest_type} digest. Sources: {sources}',
        'notify': 'always',
        'model': 'claude-sonnet-4-6',
    },
}


def get_template(name: str) -> dict[str, Any] | None:
    """Return a template by name, or None if not found."""
    return TEMPLATES.get(name)


def list_templates() -> list[dict[str, str]]:
    """Return a summary of all available templates."""
    return [{'name': name, 'description': tpl['description']} for name, tpl in TEMPLATES.items()]
