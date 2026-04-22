# ruff: noqa: I001
"""Back-compat shim for the old import path.

Redirects ``marcel_core.skills.integrations`` to :mod:`marcel_core.toolkit`
so external zoo forks that still import from the old path keep working
during the ISSUE-3c1534 migration. Phase 5 removes this file entirely
once downstreams have migrated.

Silent on import (no DeprecationWarning fired at load time) to keep test
output clean. The ``integration.yaml`` / ``integrations/`` discovery paths
and the ``integration`` tool alias emit deprecation logs at actual use,
which is the right place to surface the migration message.

All symbols are re-exported directly — public (``register``,
``marcel_tool``, ``discover``, …) and private (``_registry``,
``_metadata``, ``_EXTERNAL_MODULE_PREFIX``) — so code that monkeypatches
internals continues to work.
"""

from __future__ import annotations

from marcel_core.toolkit import (  # noqa: F401
    HabitatRollback,
    IntegrationHandler,
    IntegrationMetadata,
    ScheduledJobSpec,
    ToolkitHandler,
    ToolkitMetadata,
    _EXTERNAL_MODULE_PREFIX,
    _metadata,
    _registry,
    discover,
    get_handler,
    get_integration_metadata,
    list_integrations,
    list_python_skills,
    marcel_tool,
    register,
)
