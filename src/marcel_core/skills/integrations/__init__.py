"""Pluggable integration modules for the ``integration`` tool.

Each integration module defines async handler functions decorated with
:func:`register`.  At import time the decorator adds the function to a
global registry keyed by dotted skill name (e.g. ``"icloud.calendar"``).

Discovery is automatic. :func:`discover` imports:

1. Every sibling module in this package (first-party integrations shipped
   inside ``marcel_core``).
2. Every integration habitat directory under
   ``<MARCEL_ZOO_DIR>/integrations/`` — external, zoo-sourced integrations
   (ISSUE-3c87dd, ISSUE-6ad5c7). Discovery is a silent no-op when
   ``MARCEL_ZOO_DIR`` is unset.

External habitats are packages: a directory with its own ``__init__.py``
that calls ``@register`` at import time. The directory name must match
the ``family`` segment of every handler name it registers — e.g. an
integration at ``<zoo>/integrations/docker/`` may register
``docker.list`` but **not** ``container.start``. Handlers that violate
this are rejected and the whole integration is rolled back (no partial
registrations leak into the registry).

Errors in one external integration never abort discovery of its siblings
— the failure is logged, the integration is disabled, and the rest load
normally.

Usage in an integration module::

    from marcel_core.plugin import register

    @register("myservice.action")
    async def action(params: dict, user_slug: str) -> str:
        ...
        return "result text"
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import pkgutil
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# Handler signature: (params: dict[str, str], user_slug: str) -> str
IntegrationHandler = Callable[[dict, str], Awaitable[str]]

# Global registry: skill_name -> handler function
_registry: dict[str, IntegrationHandler] = {}


@dataclass
class ScheduledJobSpec:
    """One ``scheduled_jobs:`` entry from ``integration.yaml`` (ISSUE-82f52b).

    A habitat declares zero or more periodic jobs. The kernel scheduler
    materializes each spec as a system-scope :class:`JobDefinition` with
    ``template='habitat:<integration>'`` so the existing agent pipeline runs
    them — same retry, alerting, and observability story as kernel jobs.

    Required:
        name: human-readable name; unique within the habitat *and* across
            every other habitat's scheduled jobs
        handler: a skill name from the habitat's ``provides:`` list
        cron OR interval_seconds: trigger spec (exactly one)

    Optional overrides default to a generated "call handler, report results"
    agent prompt. Set them to inject LLM creativity per job.
    """

    name: str
    handler: str
    cron: str | None = None
    interval_seconds: int | None = None
    params: dict = field(default_factory=dict)
    description: str = ''
    notify: str = 'silent'
    channel: str = 'telegram'
    timezone: str | None = None
    task: str | None = None
    system_prompt: str | None = None
    model: str | None = None


@dataclass
class IntegrationMetadata:
    """Declarative metadata for one integration habitat.

    Loaded from ``<habitat>/integration.yaml``. The kernel uses this to
    resolve ``depends_on:`` from a skill habitat back to the integration's
    requirements (credentials/env/files/packages) — see ISSUE-6ad5c7.

    Handler dispatch is driven by ``@register`` (the source of truth);
    ``provides`` here is a declaration used for documentation, tooling,
    and consistency checks.
    """

    name: str
    description: str = ''
    provides: list[str] = field(default_factory=list)
    requires: dict = field(default_factory=dict)
    scheduled_jobs: list[ScheduledJobSpec] = field(default_factory=list)


# Metadata registry: integration_name -> IntegrationMetadata.
# Populated when an external habitat ships ``integration.yaml`` alongside
# its ``__init__.py``. First-party integrations and habitats without a
# YAML file simply do not appear here.
_metadata: dict[str, IntegrationMetadata] = {}


def get_integration_metadata(name: str) -> IntegrationMetadata | None:
    """Return the parsed metadata for integration *name*, or ``None``.

    Used by the skill loader to resolve ``depends_on: [<integration>]``
    in SKILL.md frontmatter back to the integration's ``requires:`` block.
    """
    return _metadata.get(name)


def list_integrations() -> list[str]:
    """Return all integration names that have published metadata."""
    return sorted(_metadata.keys())


# Skill names must follow the ``family.action`` convention: two dot-separated
# segments, each containing only lowercase letters, digits, and underscores.
# Matches the same pattern enforced in registry.py.
_SKILL_NAME_PATTERN: re.Pattern[str] = re.compile(r'^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$')

# Prefix used for sys.modules entries of dynamically-loaded external
# integrations. Kept private so it cannot collide with a future real
# top-level package.
_EXTERNAL_MODULE_PREFIX = '_marcel_ext_integrations'


def register(skill_name: str) -> Callable[[IntegrationHandler], IntegrationHandler]:
    """Decorator that registers an async handler under *skill_name*.

    Raises ``ValueError`` if:
    - the name is already registered (prevents silent overwrites from duplicate imports).
    - the name does not match the ``family.action`` convention.

    Valid names are two dot-separated lowercase segments, e.g. ``"icloud.calendar"``.
    Each segment may contain letters, digits, and underscores.
    """
    if not _SKILL_NAME_PATTERN.match(skill_name):
        raise ValueError(
            f"Invalid skill name '{skill_name}'. "
            "Skill names must follow the 'family.action' convention: two dot-separated "
            'segments using only lowercase letters, digits, and underscores '
            "(e.g. 'icloud.calendar', 'banking.balance')."
        )

    def decorator(fn: IntegrationHandler) -> IntegrationHandler:
        if skill_name in _registry:
            raise ValueError(f"Integration skill '{skill_name}' is already registered")
        _registry[skill_name] = fn
        return fn

    return decorator


def get_handler(skill_name: str) -> IntegrationHandler:
    """Return the handler for *skill_name*, or raise ``KeyError``."""
    try:
        return _registry[skill_name]
    except KeyError:
        raise KeyError(f"No python integration registered for '{skill_name}'") from None


def list_python_skills() -> list[str]:
    """Return all registered python integration skill names."""
    return sorted(_registry.keys())


def discover() -> None:
    """Discover first-party and external integrations.

    First-party modules are siblings of this package. External habitats
    live at ``<MARCEL_ZOO_DIR>/integrations/<name>/`` and are loaded via
    :func:`_discover_external`. Safe to call multiple times — already-
    imported modules are skipped by Python's import machinery.
    """
    _discover_builtin()
    _discover_external()


def _discover_builtin() -> None:
    """Import all sibling modules to trigger ``@register`` decorators."""
    package_path = __path__
    for info in pkgutil.iter_modules(package_path):
        if info.name.startswith('_'):
            continue
        module_name = f'{__name__}.{info.name}'
        try:
            importlib.import_module(module_name)
        except Exception:
            log.exception('Failed to import integration module %s', module_name)


def _discover_external() -> None:
    """Import external integration habitats from ``<MARCEL_ZOO_DIR>/integrations/``.

    Each subdirectory is loaded as a package. See :func:`_load_external_integration`
    for the per-package loading contract (namespace enforcement, error isolation).

    Returns silently when ``MARCEL_ZOO_DIR`` is unset — the kernel ships no
    habitats; users opt in by pointing the env var at a marcel-zoo checkout.
    """
    try:
        from marcel_core.config import settings

        zoo_dir = settings.zoo_dir
    except Exception:
        log.exception('Failed to resolve zoo_dir for external integration discovery')
        return

    if zoo_dir is None:
        return

    external_dir = zoo_dir / 'integrations'
    if not external_dir.is_dir():
        return

    for entry in sorted(external_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith(('_', '.')):
            continue
        _load_external_integration(entry)


def _load_external_integration(pkg_dir: Path) -> None:
    """Load one external integration package from *pkg_dir*.

    The directory name must match the ``family`` segment of every handler
    name registered by the package. Handlers outside that namespace cause
    the entire integration to be rolled back — no partial state leaks
    into the registry.

    A malformed ``scheduled_jobs:`` block in ``integration.yaml`` is also
    a rollback condition (ISSUE-82f52b): handlers are removed, no
    metadata is published, and the scheduler never sees a partial habitat.
    Errors are logged and contained; the caller continues with the next
    integration regardless of what happens here.
    """
    init_py = pkg_dir / '__init__.py'
    if not init_py.exists():
        log.warning(
            "Integration habitat '%s' has no __init__.py — skipping",
            pkg_dir.name,
        )
        return

    module_name = f'{_EXTERNAL_MODULE_PREFIX}.{pkg_dir.name}'

    # Idempotency — discovery is called from multiple entry points and
    # must behave like the built-in path, which benefits from Python's
    # import cache automatically.
    if module_name in sys.modules:
        return

    before = set(_registry)

    def _rollback_handlers() -> None:
        for name in set(_registry) - before:
            _registry.pop(name, None)
        sys.modules.pop(module_name, None)

    try:
        spec = importlib.util.spec_from_file_location(
            module_name,
            init_py,
            submodule_search_locations=[str(pkg_dir)],
        )
        if spec is None or spec.loader is None:
            log.error(
                "Could not create module spec for integration habitat '%s'",
                pkg_dir.name,
            )
            return

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        added = set(_registry) - before
        invalid = [name for name in added if not name.startswith(f'{pkg_dir.name}.')]
        if invalid:
            _rollback_handlers()
            log.error(
                "Integration habitat '%s' registered handlers outside its namespace: %s. "
                "All handler names must start with '%s.'. Integration disabled.",
                pkg_dir.name,
                sorted(invalid),
                pkg_dir.name,
            )
            return

        # Handlers loaded cleanly — parse integration.yaml.
        #
        # ``provides``/``requires`` failures disable only the metadata; handlers
        # keep working (legacy behaviour from ISSUE-6ad5c7). A malformed
        # ``scheduled_jobs`` block raises HabitatRollback, which kicks the
        # whole habitat out so the scheduler never sees half-registered state
        # (ISSUE-82f52b — mirrors the namespace-check precedent).
        try:
            _load_integration_metadata(pkg_dir)
        except HabitatRollback as exc:
            _rollback_handlers()
            log.error(
                "Integration habitat '%s' rolled back: %s",
                pkg_dir.name,
                exc,
            )
            return
    except Exception:
        _rollback_handlers()
        log.exception(
            "Failed to load integration habitat '%s'",
            pkg_dir.name,
        )


class HabitatRollback(Exception):
    """Raised inside discovery when a habitat must be fully rolled back.

    Caught only by :func:`_load_external_integration`, which removes any
    handlers the package registered before metadata parsing failed. Bubbling
    out of discovery would be a bug.
    """


_VALID_REQUIRES_KEYS = frozenset({'credentials', 'env', 'files', 'packages'})

_VALID_NOTIFY_VALUES = frozenset({'always', 'on_failure', 'on_output', 'silent'})


def _load_integration_metadata(pkg_dir: Path) -> None:
    """Parse ``integration.yaml`` from *pkg_dir* and populate ``_metadata``.

    Validation:
    - ``name`` (if present) must equal the directory name.
    - ``provides`` must be a list of strings, each in the ``<dirname>.*``
      namespace (matches the handler-namespace rule).
    - ``requires`` must be a dict with keys drawn from
      ``credentials``/``env``/``files``/``packages``.
    - ``scheduled_jobs`` (if present) must be a list of valid specs — see
      :func:`_validate_scheduled_jobs`. A malformed entry raises
      :class:`HabitatRollback` so the whole habitat is removed.

    ``provides``/``requires`` failures log an error and skip metadata only
    (handlers keep dispatching). ``scheduled_jobs`` failures are stricter
    because a missing scheduled job is a silent gap users would not notice.
    """
    yaml_path = pkg_dir / 'integration.yaml'
    if not yaml_path.exists():
        log.warning(
            "Integration habitat '%s' has no integration.yaml — depends_on: resolution against it will not work",
            pkg_dir.name,
        )
        return

    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding='utf-8')) or {}
    except yaml.YAMLError:
        log.exception(
            "integration.yaml in habitat '%s' is not valid YAML — metadata skipped",
            pkg_dir.name,
        )
        return

    if not isinstance(raw, dict):
        log.error(
            "integration.yaml in habitat '%s' must be a mapping at the top level — metadata skipped",
            pkg_dir.name,
        )
        return

    name = raw.get('name', pkg_dir.name)
    if name != pkg_dir.name:
        log.error(
            "integration.yaml in habitat '%s' declares name='%s' — must match directory name. Metadata skipped.",
            pkg_dir.name,
            name,
        )
        return

    provides = raw.get('provides', []) or []
    if not isinstance(provides, list) or not all(isinstance(p, str) for p in provides):
        log.error(
            "integration.yaml in habitat '%s' has invalid 'provides' (must be list of strings) — metadata skipped",
            pkg_dir.name,
        )
        return

    bad = [p for p in provides if not p.startswith(f'{pkg_dir.name}.')]
    if bad:
        log.error(
            "integration.yaml in habitat '%s' lists handlers outside its namespace: %s. Metadata skipped.",
            pkg_dir.name,
            bad,
        )
        return

    requires = raw.get('requires') or {}
    if not isinstance(requires, dict):
        log.error(
            "integration.yaml in habitat '%s' has invalid 'requires' (must be a mapping) — metadata skipped",
            pkg_dir.name,
        )
        return

    unknown = set(requires) - _VALID_REQUIRES_KEYS
    if unknown:
        log.warning(
            "integration.yaml in habitat '%s' declares unknown requires keys %s — these will be ignored",
            pkg_dir.name,
            sorted(unknown),
        )

    scheduled_jobs = _validate_scheduled_jobs(pkg_dir.name, raw.get('scheduled_jobs'), provides)

    _metadata[name] = IntegrationMetadata(
        name=name,
        description=str(raw.get('description', '')),
        provides=list(provides),
        requires=dict(requires),
        scheduled_jobs=scheduled_jobs,
    )


def _validate_scheduled_jobs(
    habitat: str,
    raw: object,
    provides: list[str],
) -> list[ScheduledJobSpec]:
    """Validate the ``scheduled_jobs:`` block from ``integration.yaml``.

    Returns the list of parsed specs. Raises :class:`HabitatRollback` on
    any structural error (caught by :func:`_load_external_integration`,
    which then removes the habitat's handlers — see ISSUE-82f52b).

    Validation rules:
    - block must be a list of mappings (``None`` and missing key are OK)
    - each entry must have ``name`` (str), ``handler`` (str in ``provides``)
    - exactly one of ``cron`` / ``interval_seconds`` must be set
    - ``cron`` must be a valid croniter expression
    - ``interval_seconds`` must be a positive integer
    - ``params`` (optional) must be a dict
    - ``notify`` (optional) must be one of ``always|on_failure|on_output|silent``
    - ``name`` must be unique within the habitat
    - ``name`` must not collide with any already-loaded habitat's job names
    """
    if raw is None:
        return []

    if not isinstance(raw, list):
        raise HabitatRollback(f"'scheduled_jobs' must be a list, got {type(raw).__name__}")

    seen_names: set[str] = set()
    existing_names = {spec.name for meta in _metadata.values() for spec in meta.scheduled_jobs}

    parsed: list[ScheduledJobSpec] = []
    for i, entry in enumerate(raw):
        loc = f'scheduled_jobs[{i}]'
        if not isinstance(entry, dict):
            raise HabitatRollback(f'{loc} must be a mapping')

        name = entry.get('name')
        if not isinstance(name, str) or not name.strip():
            raise HabitatRollback(f'{loc} missing required string field "name"')
        if name in seen_names:
            raise HabitatRollback(f'{loc} duplicate name "{name}" within habitat')
        if name in existing_names:
            raise HabitatRollback(f'{loc} name "{name}" collides with a job already declared by another habitat')
        seen_names.add(name)

        handler = entry.get('handler')
        if not isinstance(handler, str):
            raise HabitatRollback(f"{loc} ('{name}') missing required string field 'handler'")
        if handler not in provides:
            raise HabitatRollback(f"{loc} ('{name}') handler '{handler}' is not listed in this habitat's provides:")

        cron = entry.get('cron')
        interval_seconds = entry.get('interval_seconds')
        if (cron is None) == (interval_seconds is None):
            raise HabitatRollback(f"{loc} ('{name}') must set exactly one of 'cron' or 'interval_seconds'")

        if cron is not None:
            if not isinstance(cron, str):
                raise HabitatRollback(f"{loc} ('{name}') 'cron' must be a string")
            try:
                from croniter import croniter

                if not croniter.is_valid(cron):
                    raise HabitatRollback(f"{loc} ('{name}') invalid cron expression: {cron!r}")
            except HabitatRollback:
                raise
            except Exception as exc:
                raise HabitatRollback(f"{loc} ('{name}') failed to validate cron {cron!r}: {exc}") from exc

        if interval_seconds is not None:
            if not isinstance(interval_seconds, int) or isinstance(interval_seconds, bool):
                raise HabitatRollback(f"{loc} ('{name}') 'interval_seconds' must be a positive integer")
            if interval_seconds <= 0:
                raise HabitatRollback(f"{loc} ('{name}') 'interval_seconds' must be a positive integer")

        params = entry.get('params', {}) or {}
        if not isinstance(params, dict):
            raise HabitatRollback(f"{loc} ('{name}') 'params' must be a mapping")

        notify = entry.get('notify', 'silent')
        if notify not in _VALID_NOTIFY_VALUES:
            raise HabitatRollback(
                f"{loc} ('{name}') 'notify' must be one of {sorted(_VALID_NOTIFY_VALUES)}, got {notify!r}"
            )

        parsed.append(
            ScheduledJobSpec(
                name=name,
                handler=handler,
                cron=cron,
                interval_seconds=interval_seconds,
                params=dict(params),
                description=str(entry.get('description', '')),
                notify=notify,
                channel=str(entry.get('channel', 'telegram')),
                timezone=entry.get('timezone'),
                task=entry.get('task'),
                system_prompt=entry.get('system_prompt'),
                model=entry.get('model'),
            )
        )

    return parsed
