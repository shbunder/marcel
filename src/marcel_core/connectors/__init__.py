"""Connector habitat — MCP servers with per-user auth (FEAT-260718-230bf8).

A connector is an MCP server plus the per-user authentication layer needed to
use it (ADR-260718-231cad). This package owns the kernel machinery: the
``connector.yaml`` schema, habitat discovery/scoping, per-user auth, the MCP
toolset factory, and the stdio/in-process lifecycle. Habitats themselves live
in the zoo under ``connectors/<name>/``.
"""

from __future__ import annotations

from marcel_core.connectors.loader import (
    ConnectorDoc,
    get_connector,
    load_connectors,
    validate_connector_config,
)
from marcel_core.connectors.models import (
    AuthMode,
    ConnectorConfig,
    Discovery,
    Scope,
    Transport,
)

__all__ = [
    'AuthMode',
    'ConnectorConfig',
    'ConnectorDoc',
    'Discovery',
    'Scope',
    'Transport',
    'get_connector',
    'load_connectors',
    'validate_connector_config',
]
