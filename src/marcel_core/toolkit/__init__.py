"""Toolkit — pluggable tools dispatched by the agent's ``toolkit`` tool.

A **toolkit habitat** is a python module that registers async handler
functions via :func:`marcel_tool`. The kernel's native ``toolkit`` tool
(:class:`marcel_core.tools.toolkit`) dispatches agent calls of the form
``toolkit(id="<family>.<action>", params={...})`` to the registered
handler.

Each habitat lives at ``<MARCEL_ZOO_DIR>/toolkit/<name>/`` with an
``__init__.py`` (handlers) and a ``toolkit.yaml`` (contract). Discovery
is automatic at kernel startup; safe to re-run — already-imported
habitats are skipped via ``sys.modules``.

The directory name must match the ``family`` segment of every handler
name the habitat registers. Handlers outside the habitat's namespace
cause the whole habitat to be rolled back — no partial state leaks.

Two isolation modes:

- ``isolation: inprocess`` — habitat code runs in the kernel process.
- ``isolation: uds`` — habitat runs as a subprocess with its own venv,
  the kernel connects over a UDS socket (ISSUE-f60b09).

Back-compat (during ISSUE-3c1534 Phases 1–4):

- ``<zoo>/integrations/`` is walked in addition to ``<zoo>/toolkit/``.
- ``integration.yaml`` is read in addition to ``toolkit.yaml``.
- The ``@register`` decorator is an alias for :func:`marcel_tool`.
- ``IntegrationHandler`` / ``IntegrationMetadata`` are aliases for
  :class:`ToolkitHandler` / :class:`ToolkitMetadata`.
- ``marcel_core.toolkit`` re-exports everything from this
  module as a shim.

All back-compat aliases are removed in Phase 5.

Usage in a zoo toolkit habitat::

    from marcel_core.plugin import marcel_tool

    @marcel_tool("myservice.action")
    async def action(params: dict, user_slug: str) -> str:
        ...
        return "result text"
"""

from __future__ import annotations

import importlib.util
import logging
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# Handler signature: (params: dict[str, str], user_slug: str) -> str
ToolkitHandler = Callable[[dict, str], Awaitable[str]]

# Back-compat alias — removed in Phase 5.
IntegrationHandler = ToolkitHandler

# Global registry: handler name (e.g. "docker.list") -> handler function
_registry: dict[str, ToolkitHandler] = {}


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
class ToolkitMetadata:
    """Declarative metadata for one toolkit habitat.

    Loaded from ``<habitat>/toolkit.yaml`` (or legacy ``integration.yaml``
    during migration). The kernel uses this to resolve ``depends_on:``
    from a skill habitat back to the toolkit's requirements
    (credentials/env/files/packages) — see ISSUE-6ad5c7.

    Handler dispatch is driven by :func:`marcel_tool` (the source of
    truth); ``provides`` here is a declaration used for documentation,
    tooling, and consistency checks.
    """

    name: str
    description: str = ''
    provides: list[str] = field(default_factory=list)
    requires: dict = field(default_factory=dict)
    scheduled_jobs: list[ScheduledJobSpec] = field(default_factory=list)


# Back-compat alias — removed in Phase 5.
IntegrationMetadata = ToolkitMetadata


# Metadata registry: toolkit_name -> ToolkitMetadata.
# Populated when a zoo habitat ships ``toolkit.yaml`` alongside its
# ``__init__.py``. Habitats without a YAML file simply do not appear here.
_metadata: dict[str, ToolkitMetadata] = {}


def get_toolkit_metadata(name: str) -> ToolkitMetadata | None:
    """Return the parsed metadata for toolkit *name*, or ``None``.

    Used by the skill loader to resolve ``depends_on: [<toolkit>]`` in
    SKILL.md frontmatter back to the toolkit's ``requires:`` block.
    """
    return _metadata.get(name)


# Back-compat alias — removed in Phase 5.
get_integration_metadata = get_toolkit_metadata


def list_toolkits() -> list[str]:
    """Return all toolkit names that have published metadata."""
    return sorted(_metadata.keys())


# Back-compat alias — removed in Phase 5.
list_integrations = list_toolkits


# Tool names must follow the ``family.action`` convention: two dot-separated
# segments, each containing only lowercase letters, digits, and underscores.
# Matches the same pattern enforced in registry.py.
_TOOL_NAME_PATTERN: re.Pattern[str] = re.compile(r'^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$')

# Back-compat alias — removed in Phase 5.
_SKILL_NAME_PATTERN = _TOOL_NAME_PATTERN

# Prefix used for sys.modules entries of dynamically-loaded toolkit
# habitats. Kept private so it cannot collide with a future real
# top-level package.
_EXTERNAL_MODULE_PREFIX = '_marcel_ext_toolkit'


def marcel_tool(tool_name: str) -> Callable[[ToolkitHandler], ToolkitHandler]:
    """Decorator that registers an async handler under *tool_name*.

    Raises ``ValueError`` if:
    - the name is already registered (prevents silent overwrites from duplicate imports).
    - the name does not match the ``family.action`` convention.

    Valid names are two dot-separated lowercase segments, e.g. ``"icloud.calendar"``.
    Each segment may contain letters, digits, and underscores.
    """
    if not _TOOL_NAME_PATTERN.match(tool_name):
        raise ValueError(
            f"Invalid tool name '{tool_name}'. "
            "Tool names must follow the 'family.action' convention: two dot-separated "
            'segments using only lowercase letters, digits, and underscores '
            "(e.g. 'icloud.calendar', 'banking.balance')."
        )

    def decorator(fn: ToolkitHandler) -> ToolkitHandler:
        if tool_name in _registry:
            raise ValueError(f"Toolkit tool '{tool_name}' is already registered")
        _registry[tool_name] = fn
        return fn

    return decorator


# Back-compat alias — removed in Phase 5.
register = marcel_tool


def get_handler(tool_name: str) -> ToolkitHandler:
    """Return the handler for *tool_name*, or raise ``KeyError``."""
    try:
        return _registry[tool_name]
    except KeyError:
        raise KeyError(f"No toolkit handler registered for '{tool_name}'") from None


def list_tools() -> list[str]:
    """Return all registered toolkit handler names."""
    return sorted(_registry.keys())


# Back-compat alias — removed in Phase 5.
list_python_skills = list_tools


def discover() -> None:
    """Discover integration habitats from ``<MARCEL_ZOO_DIR>/integrations/``.

    Each subdirectory is loaded as a package. Two isolation modes are
    supported:

    - ``isolation: inprocess`` (default) — the habitat's ``__init__.py``
      is imported into the kernel process; ``@register`` calls populate
      the kernel-local ``_registry`` directly. See
      :func:`_load_external_integration` for the per-package loading
      contract (namespace enforcement, error isolation).
    - ``isolation: uds`` — the habitat runs as a separate subprocess
      with its own venv, listening on a UDS socket under
      ``<data_root>/sockets/<name>.sock``. The kernel registers proxy
      coroutines (one per ``provides:`` entry) that forward JSON-RPC
      calls over the socket. See :func:`_load_uds_habitat`.

    Returns silently when ``MARCEL_ZOO_DIR`` is unset or
    ``<zoo>/integrations/`` does not exist — the kernel ships no
    habitats; operators opt in by pointing the env var at a marcel-zoo
    checkout. Safe to call multiple times: already-imported in-process
    habitats are skipped via ``sys.modules``; already-spawned UDS
    habitats are skipped via the supervisor's handle table.
    """
    try:
        from marcel_core.config import settings

        zoo_dir = settings.zoo_dir
    except Exception:
        log.exception('Failed to resolve zoo_dir for toolkit discovery')
        return

    if zoo_dir is None:
        return

    # Scan both the legacy ``integrations/`` directory and the new ``toolkit/``
    # directory during Phases 1–4. Phase 5 drops the legacy path.
    seen: set[str] = set()
    for subdir_name in ('toolkit', 'integrations'):
        external_dir = zoo_dir / subdir_name
        if not external_dir.is_dir():
            continue
        if subdir_name == 'integrations':
            log.warning(
                'deprecated: <zoo>/integrations/ is scanned for back-compat; '
                'migrate to <zoo>/toolkit/ before ISSUE-3c1534 Phase 5.'
            )
        for entry in sorted(external_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith(('_', '.')):
                continue
            if entry.name in seen:
                # Already loaded from a higher-precedence directory (toolkit/
                # wins over integrations/).
                continue
            seen.add(entry.name)
            if _declared_isolation(entry) == 'uds':
                _load_uds_habitat(entry)
            else:
                _load_external_integration(entry)


def _habitat_yaml_path(pkg_dir: Path) -> Path | None:
    """Return the habitat's YAML contract path, preferring ``toolkit.yaml``.

    Reads ``toolkit.yaml`` if present; otherwise falls back to the legacy
    ``integration.yaml``. Returns ``None`` when neither file exists — the
    habitat has no declarative metadata, discovery treats it as broken.

    Phase 5 drops the ``integration.yaml`` fallback.
    """
    new = pkg_dir / 'toolkit.yaml'
    if new.exists():
        return new
    old = pkg_dir / 'integration.yaml'
    if old.exists():
        log.warning(
            "deprecated: %s uses 'integration.yaml'; rename to 'toolkit.yaml' before ISSUE-3c1534 Phase 5.",
            pkg_dir.name,
        )
        return old
    return None


def _declared_isolation(pkg_dir: Path) -> str:
    """Return the ``isolation:`` mode from the habitat's contract YAML.

    Defaults to ``'inprocess'`` when the key is missing or the YAML is
    malformed — malformed YAML is surfaced later by
    :func:`_load_external_integration`'s existing parser. Keeping this
    probe defensive avoids crashing discovery on a bad file.
    """
    yaml_path = _habitat_yaml_path(pkg_dir)
    if yaml_path is None:
        return 'inprocess'
    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding='utf-8')) or {}
    except yaml.YAMLError:
        return 'inprocess'
    if not isinstance(raw, dict):
        return 'inprocess'
    value = raw.get('isolation', 'inprocess')
    return value if isinstance(value, str) else 'inprocess'


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
            _load_toolkit_metadata(pkg_dir)
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


# ---------------------------------------------------------------------------
# UDS-isolated habitats (ISSUE-f60b09 Phase 1)
# ---------------------------------------------------------------------------


def _load_uds_habitat(pkg_dir: Path) -> None:
    """Spawn a UDS-isolated habitat subprocess and register proxy handlers.

    The habitat runs as a separate Python process (see
    :mod:`marcel_core.plugin._uds_bridge`) with its own venv and own
    ``_registry``. The kernel never imports the habitat's ``__init__.py``
    in-process; instead, for each handler name listed in
    ``integration.yaml``'s ``provides:``, the kernel registers a proxy
    coroutine that forwards JSON-RPC calls over the habitat's UDS
    socket.

    Errors during spawn (YAML invalid, missing ``provides:``, subprocess
    fails to create socket) are logged and contained — no partial
    registration leaks into ``_registry`` or ``_metadata``. This mirrors
    the rollback discipline of :func:`_load_external_integration`.

    Idempotency: if the supervisor already tracks a habitat of this
    name, the call is a no-op. Matches the ``module_name in sys.modules``
    shortcut on the in-process path.
    """
    from marcel_core.plugin import _uds_supervisor

    if pkg_dir.name in _uds_supervisor.list_habitats():
        return

    yaml_path = _habitat_yaml_path(pkg_dir)
    if yaml_path is None:
        log.error(
            "UDS habitat '%s' has no toolkit.yaml — cannot determine provides: list, skipping",
            pkg_dir.name,
        )
        return

    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding='utf-8')) or {}
    except yaml.YAMLError:
        log.exception(
            "UDS habitat '%s' has invalid %s — skipping",
            pkg_dir.name,
            yaml_path.name,
        )
        return

    if not isinstance(raw, dict):
        log.error(
            "UDS habitat '%s': %s root must be a mapping, skipping",
            pkg_dir.name,
            yaml_path.name,
        )
        return

    provides = raw.get('provides') or []
    if not isinstance(provides, list) or not all(isinstance(p, str) for p in provides):
        log.error(
            "UDS habitat '%s': provides: must be a list of strings (source of truth for handler names), skipping",
            pkg_dir.name,
        )
        return

    if not provides:
        log.warning(
            "UDS habitat '%s' declares empty provides: — spawning will register zero handlers. Skipping.",
            pkg_dir.name,
        )
        return

    bad = [p for p in provides if not p.startswith(f'{pkg_dir.name}.')]
    if bad:
        log.error(
            "UDS habitat '%s': provides: entries outside the '%s.*' namespace: %s. Skipping.",
            pkg_dir.name,
            pkg_dir.name,
            bad,
        )
        return

    # Namespace collision guard: proxy registration must not clobber a handler
    # already in the registry (from a prior in-process habitat with the same
    # handler name, or from a previous discover() call that registered in-process).
    collisions = [p for p in provides if p in _registry]
    if collisions:
        log.error(
            "UDS habitat '%s': handler names already registered: %s. Skipping.",
            pkg_dir.name,
            collisions,
        )
        return

    socket_path = _habitat_socket_path(pkg_dir.name)
    command = _bridge_command(pkg_dir, socket_path)

    try:
        _uds_supervisor.spawn_habitat(pkg_dir.name, command, socket_path)
    except Exception:
        log.exception("UDS habitat '%s' failed to spawn — skipping", pkg_dir.name)
        return

    for handler_name in provides:
        _registry[handler_name] = _make_uds_proxy(handler_name, socket_path)

    # Also parse + publish integration.yaml metadata so ``depends_on:`` from
    # paired skill habitats continues to work. Failures here disable metadata
    # only; handler proxies stay registered (same discipline as the in-process
    # path for non-scheduled-jobs errors).
    try:
        _load_toolkit_metadata(pkg_dir)
    except HabitatRollback as exc:
        # A malformed scheduled_jobs block on a UDS habitat is as bad as
        # on an in-process one: tear the habitat down to avoid a partial
        # scheduler state.
        log.error("UDS habitat '%s' rolled back: %s", pkg_dir.name, exc)
        for handler_name in provides:
            _registry.pop(handler_name, None)
        # Supervisor-level teardown is deferred to kernel shutdown; marking
        # the habitat as "to-remove" mid-run would complicate the poll loop.
        # A rolled-back habitat just leaves a sleeping subprocess until
        # lifespan teardown sweeps it.


def _habitat_socket_path(name: str) -> Path:
    from marcel_core.config import settings

    return settings.data_dir / 'sockets' / f'{name}.sock'


def _bridge_command(pkg_dir: Path, socket_path: Path) -> list[str]:
    """Return the argv to launch *pkg_dir*'s UDS bridge.

    Prefers the habitat's own ``.venv/bin/python`` if present (Phase 2+
    when ``make zoo-setup`` creates per-habitat venvs); falls back to
    the kernel's ``sys.executable`` so Phase 1 fixture habitats with no
    declared deps work out of the box.
    """
    from marcel_core.plugin import _uds_supervisor

    python = _uds_supervisor.habitat_python(pkg_dir)
    return [python, '-m', 'marcel_core.plugin._uds_bridge', str(pkg_dir), str(socket_path)]


def _make_uds_proxy(method: str, socket_path: Path) -> IntegrationHandler:
    """Return a coroutine that forwards calls for *method* over *socket_path*.

    Phase 1 opens one connection per call — simple, no state. Each
    request carries a fixed ``id`` because the connection is single-use;
    connection pooling (which requires unique ids) is a Phase 5 concern.

    Connect retries briefly on ``ConnectionRefusedError`` /
    ``FileNotFoundError`` (total window ≈ ``_UDS_CONNECT_TOTAL_TIMEOUT``).
    Both errors are transient during supervisor respawn: the bridge's
    ``unlink-then-bind`` race leaves a window where the socket file
    exists but is not yet accepting. Retrying masks that window without
    masking a habitat that's genuinely down (persistent refusal after
    the window → real error).

    Errors surface as ``RuntimeError`` with a prefix identifying the
    habitat and method — the ``integration`` tool's existing exception
    handler wraps them into user-facing error strings, so no new error
    type is introduced in Phase 1.
    """
    import json
    import struct

    async def proxy(params: dict, user_slug: str) -> str:
        reader, writer = await _uds_connect_with_retry(method, socket_path)
        try:
            body = json.dumps(
                {
                    'jsonrpc': '2.0',
                    'id': 1,
                    'method': method,
                    'params': {'params': params, 'user_slug': user_slug},
                }
            ).encode()
            writer.write(struct.pack('>I', len(body)) + body)
            await writer.drain()

            hdr = await reader.readexactly(4)
            (length,) = struct.unpack('>I', hdr)
            resp = json.loads(await reader.readexactly(length))
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

        if 'error' in resp:
            err = resp['error']
            raise RuntimeError(f'uds habitat error in {method!r}: {err.get("message", err)!s}')
        return resp.get('result', '')

    return proxy


# Transient-connect retry knobs. Small values — the window we're masking
# is the few hundred ms between a bridge's unlink-then-bind on respawn.
_UDS_CONNECT_TOTAL_TIMEOUT = 3.0
_UDS_CONNECT_INITIAL_DELAY = 0.05


async def _uds_connect_with_retry(method: str, socket_path: Path):
    """Open a UDS connection, retrying on transient 'not yet ready' errors.

    Returns ``(reader, writer)`` on success; raises ``RuntimeError`` with
    habitat context if the total window expires or a non-transient error
    is raised (e.g. ``PermissionError`` from wrong socket mode).
    """
    import asyncio

    deadline = asyncio.get_running_loop().time() + _UDS_CONNECT_TOTAL_TIMEOUT
    delay = _UDS_CONNECT_INITIAL_DELAY
    while True:
        try:
            return await asyncio.open_unix_connection(str(socket_path))
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            if asyncio.get_running_loop().time() + delay >= deadline:
                raise RuntimeError(
                    f'uds habitat unavailable for {method!r} after {_UDS_CONNECT_TOTAL_TIMEOUT:.1f}s of retries: {exc}'
                ) from exc
            await asyncio.sleep(delay)
            delay = min(delay * 2, 0.5)


_VALID_REQUIRES_KEYS = frozenset({'credentials', 'env', 'files', 'packages'})

_VALID_NOTIFY_VALUES = frozenset({'always', 'on_failure', 'on_output', 'silent'})


def _load_toolkit_metadata(pkg_dir: Path) -> None:
    """Parse ``toolkit.yaml`` from *pkg_dir* and populate ``_metadata``.

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

    Reads ``toolkit.yaml`` preferentially; falls back to legacy
    ``integration.yaml`` via :func:`_habitat_yaml_path` during Phases 1–4.
    """
    yaml_path = _habitat_yaml_path(pkg_dir)
    if yaml_path is None:
        log.warning(
            "Toolkit habitat '%s' has no toolkit.yaml — depends_on: resolution against it will not work",
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
