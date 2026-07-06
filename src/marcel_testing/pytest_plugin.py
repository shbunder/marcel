"""Pytest plugin — the ``terrarium`` fixture plus odile's no-network guard.

Load it from a repo's root ``conftest.py`` (the marcel and marcel-zoo
conftests both do)::

    pytest_plugins = ['marcel_testing.pytest_plugin']

Provides:

- everything from ``odile.pytest_plugin`` — most importantly the autouse
  session guard that makes real model-provider requests impossible;
- ``terrarium`` (function-scoped) — a fresh sealed
  :class:`~marcel_testing.Terrarium` rooted in this test's temp dir.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

# Re-exported so loading this plugin gives the odile guard + fixtures too.
from odile.pytest_plugin import fake_world, no_real_model_requests  # noqa: F401

from marcel_testing.terrarium import Terrarium


@pytest.fixture
def terrarium(tmp_path: Path) -> Iterator[Terrarium]:
    """A sealed Marcel world for this test — state, registries, and HTTP isolated."""
    with Terrarium(tmp_path / 'marcel-data') as sealed_world:
        yield sealed_world
