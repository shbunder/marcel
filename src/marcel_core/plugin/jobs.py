"""Job template discovery — file-backed habitat loader.

Job *templates* are reusable defaults used during conversational job creation:
the user says "set up a sync for my news", the agent picks the ``sync``
template, fills in placeholders, and calls ``create_job``. Before this
module, those templates lived in a Python dict inside
``marcel_core.jobs.templates``. They now live as ``template.yaml`` files in
habitat directories, editable without touching kernel code.

Two sources are scanned, in order:

1. ``<MARCEL_ZOO_DIR>/jobs/<name>/template.yaml`` — zoo-provided defaults
   (``sync``, ``check``, ``scrape``, ``digest``, …).
2. ``<data_root>/jobs/<name>/template.yaml`` — per-install overrides. A
   template.yaml here with the same name wins over the zoo version. This
   mirrors the skill loader's precedence (data root wins on collision).

A ``<data_root>/jobs/<slug>/`` directory without ``template.yaml`` is a job
*instance* (it has ``JOB.md`` + ``state.json`` + ``runs/``) and is ignored
by this loader. Template and instance directories coexist in the same tree.

``template.yaml`` schema::

    description: str                  # one-line human description
    default_trigger: dict             # {type, interval_seconds, cron, ...}
    system_prompt: str                # system prompt for the job agent
    task_template: str | None         # optional; usually '{placeholder}' string
    notify: str                       # 'always' | 'on_failure' | 'on_output' | 'silent'
    model: str                        # fully-qualified pydantic-ai model id

    # Optional (ISSUE-ea6d47) — selects how the job dispatches its work
    # when the trigger fires. Omitted ⇒ 'agent' (historical default).
    dispatch_type: str                # 'tool' | 'subagent' | 'agent'
    tool: str                         # required if dispatch_type=tool (toolkit handler name)
    tool_params: dict                 # optional; kwargs passed to the handler
    subagent: str                     # required if dispatch_type=subagent (subagent name)
    subagent_task: str                # optional; '{user_slug}' placeholder supported

Extra keys are preserved and returned to the caller. Missing required keys
(``description``, ``system_prompt``, ``notify``, ``model``) cause the
habitat to be skipped with a logged error — a broken habitat must never
abort discovery of its siblings. A ``dispatch_type`` value outside
``{'tool', 'subagent', 'agent'}`` or missing the accompanying
``tool:``/``subagent:`` key is treated the same way.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

_REQUIRED_KEYS: tuple[str, ...] = ('description', 'system_prompt', 'notify', 'model')

_VALID_DISPATCH_TYPES: frozenset[str] = frozenset({'tool', 'subagent', 'agent'})


def discover_templates() -> dict[str, dict[str, Any]]:
    """Load every job template habitat into a single dict.

    Keys are template names (the habitat directory name); values are the
    parsed ``template.yaml`` body as a ``dict[str, Any]``. Returns an empty
    dict when no sources exist — the caller decides whether that is an
    error or a recoverable "no templates configured" state.

    This is a cold read on every call. Templates are not cached: the set is
    small, discovery is cheap, and live-editing a YAML during development
    should be reflected immediately without a restart.
    """
    result: dict[str, dict[str, Any]] = {}

    for pkg_dir in _iter_zoo_template_dirs():
        template = _load_template_file(pkg_dir)
        if template is not None:
            result[pkg_dir.name] = template

    for pkg_dir in _iter_data_root_template_dirs():
        template = _load_template_file(pkg_dir)
        if template is not None:
            result[pkg_dir.name] = template

    return result


def _iter_zoo_template_dirs() -> list[Path]:
    """Enumerate ``<MARCEL_ZOO_DIR>/jobs/*/`` directories.

    Returns an empty list when the zoo dir is unset or the ``jobs/`` subtree
    does not exist — both are valid configurations.
    """
    try:
        from marcel_core.config import settings

        zoo_dir = settings.zoo_dir
    except Exception:
        log.exception('Failed to resolve zoo_dir for job template discovery')
        return []

    if zoo_dir is None:
        return []

    external_dir = zoo_dir / 'jobs'
    if not external_dir.is_dir():
        return []

    return [
        entry for entry in sorted(external_dir.iterdir()) if entry.is_dir() and not entry.name.startswith(('_', '.'))
    ]


def _iter_data_root_template_dirs() -> list[Path]:
    """Enumerate ``<data_root>/jobs/*/`` directories that hold a template.

    A directory qualifies only if it contains ``template.yaml`` — otherwise
    it is a job instance and is skipped. This filter lets templates and
    instances share one tree without ambiguity.
    """
    from marcel_core.storage._root import data_root

    jobs_dir = data_root() / 'jobs'
    if not jobs_dir.is_dir():
        return []

    return [
        entry
        for entry in sorted(jobs_dir.iterdir())
        if entry.is_dir() and not entry.name.startswith(('_', '.')) and (entry / 'template.yaml').exists()
    ]


def _load_template_file(pkg_dir: Path) -> dict[str, Any] | None:
    """Parse one ``template.yaml`` and validate required keys.

    Returns the parsed body on success, ``None`` on any parse/validation
    failure (with the failure logged). A missing ``template.yaml`` silently
    returns ``None`` so callers can use this as a "maybe-template" probe.
    """
    template_path = pkg_dir / 'template.yaml'
    if not template_path.exists():
        return None

    try:
        raw = yaml.safe_load(template_path.read_text(encoding='utf-8'))
    except Exception:
        log.exception("Failed to parse job template '%s'", pkg_dir.name)
        return None

    if not isinstance(raw, dict):
        log.error(
            "Job template '%s' must be a mapping, got %s",
            pkg_dir.name,
            type(raw).__name__,
        )
        return None

    missing = [k for k in _REQUIRED_KEYS if k not in raw]
    if missing:
        log.error(
            "Job template '%s' is missing required keys: %s",
            pkg_dir.name,
            ', '.join(missing),
        )
        return None

    dispatch_type = raw.get('dispatch_type', 'agent')
    if dispatch_type not in _VALID_DISPATCH_TYPES:
        log.error(
            "Job template '%s' has invalid dispatch_type %r (expected one of %s)",
            pkg_dir.name,
            dispatch_type,
            sorted(_VALID_DISPATCH_TYPES),
        )
        return None
    if dispatch_type == 'tool' and 'tool' not in raw:
        log.error(
            "Job template '%s' declares dispatch_type='tool' but is missing the required `tool:` key", pkg_dir.name
        )
        return None
    if dispatch_type == 'subagent' and 'subagent' not in raw:
        log.error(
            "Job template '%s' declares dispatch_type='subagent' but is missing the required `subagent:` key",
            pkg_dir.name,
        )
        return None

    return raw
