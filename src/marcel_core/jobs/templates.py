"""Job templates — sourced from zoo + data-root habitats.

Templates are the defaults the agent uses during conversational job
creation. Historically this module shipped them as a hardcoded Python
dict; since ISSUE-a7d69a they live as ``template.yaml`` files under
``<MARCEL_ZOO_DIR>/jobs/<name>/`` (zoo) or ``<data_root>/jobs/<name>/``
(per-install override). This module is now a thin accessor over
:func:`marcel_core.plugin.jobs.discover_templates`.

Kernel ships **no** fallback template. If the zoo is not configured and
the user has not created any local templates, :data:`TEMPLATES` is empty
and the agent tells the user to set one up. The rationale matches other
habitat types: the kernel is content-free, habitats supply behavior.
"""

from __future__ import annotations

from typing import Any

from marcel_core.plugin.jobs import discover_templates


def _templates() -> dict[str, dict[str, Any]]:
    return discover_templates()


def get_template(name: str) -> dict[str, Any] | None:
    """Return a template by name, or None if not found."""
    return _templates().get(name)


def list_templates() -> list[dict[str, str]]:
    """Return a summary of all available templates."""
    return [{'name': name, 'description': str(tpl.get('description', ''))} for name, tpl in _templates().items()]


def __getattr__(attr: str) -> Any:
    """Expose :data:`TEMPLATES` as a fresh read on every access.

    Callers that imported ``TEMPLATES`` for backward-compat observe the
    current on-disk state each time, so editing a ``template.yaml`` does
    not require a restart.
    """
    if attr == 'TEMPLATES':
        return _templates()
    raise AttributeError(f'module {__name__!r} has no attribute {attr!r}')
