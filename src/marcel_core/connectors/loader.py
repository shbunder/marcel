"""Connector habitat discovery, validation and scoping (FEAT-260718-230bf8).

A connector habitat is a directory ``connectors/<name>/`` holding a
``connector.yaml`` (schema in :mod:`marcel_core.connectors.models`) and,
optionally, a bundled ``server/`` (in-process/stdio FastMCP) and ``SETUP.md``.

Connectors resolve through the **same three-root, per-user scoping chain as
skills** (FEAT-260718-85b545), most-specific wins on name collision:
``<data>/users/<slug>/connectors/`` → ``<zoo>/users/<slug>/connectors/`` →
``<zoo>/connectors/``. Discovery is isolated: a malformed connector is logged
and skipped, never breaking discovery of the others (FR1, mirroring
``plugin/orchestrator.py``). Role scoping drops ``scope: admin`` connectors from
a non-admin catalog.

This module owns discovery/validation/scoping only. The MCP toolset factory,
per-user auth, and lifecycle live in sibling modules landed by later stories.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from marcel_core.connectors.models import ConnectorConfig, Scope

log = logging.getLogger(__name__)

_CONNECTOR_YAML = 'connector.yaml'
_VALID_ROLES = frozenset({'user', 'admin'})


def _connectors_root() -> Path:
    """The data-root connectors directory parent (``<data>``)."""
    from marcel_core.config import settings

    return settings.data_dir


def _connector_dirs(user_slug: str | None = None) -> list[tuple[Path, str]]:
    """Return ``(path, source)`` roots in least→most-specific order.

    1. ``<zoo>/connectors/`` — global habitats (``source='zoo-global'``)
    2. ``<zoo>/users/<slug>/connectors/`` — git-managed per-user (``'zoo-user'``)
    3. ``<data>/users/<slug>/connectors/`` — runtime-installed per-user
       (``'data-user'``)

    A missing root is simply absent (never an error). With ``user_slug`` ``None``
    only the global root is returned. Mirrors ``skills.loader._skill_dirs``.
    """
    from marcel_core.config import settings

    if user_slug:
        from marcel_core.auth import valid_user_slug

        if not valid_user_slug(user_slug):
            # Defense in depth: callers validate at the API boundary, but a slug
            # is about to be joined into a filesystem path either way.
            log.warning('connectors: refusing discovery for invalid slug')
            return []

    dirs: list[tuple[Path, str]] = []
    zoo = settings.zoo_dir
    if zoo is not None and (zoo / 'connectors').is_dir():
        dirs.append((zoo / 'connectors', 'zoo-global'))
    if user_slug:
        if zoo is not None and (zoo / 'users' / user_slug / 'connectors').is_dir():
            dirs.append((zoo / 'users' / user_slug / 'connectors', 'zoo-user'))
        data_user = _connectors_root() / 'users' / user_slug / 'connectors'
        if data_user.is_dir():
            dirs.append((data_user, 'data-user'))
    return dirs


@dataclass
class ConnectorDoc:
    """A discovered, validated connector."""

    config: ConnectorConfig
    source: str  # 'zoo-global' | 'zoo-user' | 'data-user'
    connector_dir: Path

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def setup_md(self) -> Path | None:
        candidate = self.connector_dir / 'SETUP.md'
        return candidate if candidate.is_file() else None


def validate_connector_config(raw: object, dir_name: str) -> tuple[ConnectorConfig | None, str | None]:
    """Validate a parsed ``connector.yaml`` mapping.

    Returns ``(config, None)`` on success or ``(None, error)`` with an
    actionable message. Enforces the schema plus ``name`` == directory name.
    """
    if not isinstance(raw, dict):
        return None, f'{dir_name!r}: connector.yaml must be a mapping, got {type(raw).__name__}'
    try:
        config = ConnectorConfig.model_validate(raw)
    except ValidationError as exc:
        # First error is enough to act on; keep it short and located.
        first = exc.errors()[0]
        loc = '.'.join(str(p) for p in first.get('loc', ())) or '<root>'
        return None, f'{dir_name!r}: connector.yaml invalid at {loc}: {first.get("msg", "invalid")}'
    if config.name != dir_name:
        return None, f'{dir_name!r}: connector.yaml name {config.name!r} must equal the directory name {dir_name!r}'
    return config, None


def _load_connector_dir(connector_dir: Path, source: str) -> ConnectorDoc | None:
    """Load and validate one connector directory, or ``None`` if malformed.

    Isolation contract: every failure mode returns ``None`` with a warning; a
    single bad habitat never raises out of discovery.
    """
    yaml_path = connector_dir / _CONNECTOR_YAML
    if not yaml_path.is_file():
        log.warning('connectors: %s has no connector.yaml — skipping', connector_dir.name)
        return None
    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding='utf-8'))
    except (yaml.YAMLError, OSError) as exc:
        log.warning('connectors: %s connector.yaml unreadable — skipping (%s)', connector_dir.name, exc)
        return None

    config, error = validate_connector_config(raw, connector_dir.name)
    if error is not None:
        log.warning('connectors: skipping nonconformant connector — %s', error)
        return None
    assert config is not None  # narrowed by error is None
    config._connector_dir = connector_dir  # D3: lets server.module resolve park-relative files
    return ConnectorDoc(config=config, source=source, connector_dir=connector_dir)


def load_connectors(user_slug: str | None, role: str = 'user') -> list[ConnectorDoc]:
    """Discover the connectors visible to *user_slug* at *role*, sorted by name.

    Resolves the scoping chain (global → zoo-user → data-user; most-specific
    wins on name collision, shadow logged) and drops ``scope: admin`` connectors
    for a non-admin user.
    """
    if role not in _VALID_ROLES:
        role = 'user'
    by_name: dict[str, ConnectorDoc] = {}

    # Extension-registered connector habitats (api.connector(source),
    # FEAT-260707-acb2b6): loaded least-specific — a zoo/data habitat of the
    # same name overrides an extension-shipped one — and subject to exactly
    # the same validation, role and enablement filters as every other root.
    from marcel_core.plugin.extension import extension_registry

    for raw in extension_registry().connectors:
        ext_dir = Path(raw)
        if not ext_dir.is_dir():
            log.warning('connectors: extension-registered path %s is not a directory — skipped', ext_dir)
            continue
        doc = _load_connector_dir(ext_dir, 'extension')
        if doc is not None:
            by_name[doc.name] = doc

    for path, source in _connector_dirs(user_slug):
        for entry in sorted(path.iterdir()):
            if not entry.is_dir() or entry.name.startswith(('_', '.')):
                continue
            doc = _load_connector_dir(entry, source)
            if doc is None:
                continue
            if doc.name in by_name and by_name[doc.name].source != source:
                log.info(
                    'connectors: %s from %s shadows the %s variant for user %s',
                    doc.name,
                    source,
                    by_name[doc.name].source,
                    user_slug,
                )
            by_name[doc.name] = doc

    # Availability = role ∧ enablement ∧ configuration (ADR-260707-c9919f);
    # same choke-point enforcement as the skills loader (FEAT-260707-acb2b6).
    from marcel_core.marketplace.enablement import enabled_for

    visible = [
        d
        for d in by_name.values()
        if (d.config.scope is Scope.ALL or role == 'admin') and enabled_for('connectors', d.name, user_slug, role)
    ]
    return sorted(visible, key=lambda d: d.name)


def get_connector(name: str, user_slug: str | None, role: str = 'user') -> ConnectorDoc | None:
    """Return a single visible connector by name, or ``None``."""
    for doc in load_connectors(user_slug, role):
        if doc.name == name:
            return doc
    return None
