"""Unified habitat-discovery orchestrator.

Single entry point that invokes all five kind-specific discoverers in a
fixed order, isolates their failures so a broken kind cannot poison the
others, and returns the discovered habitats grouped by kind.

Used by :func:`marcel_core.main.lifespan` to replace the previous
sequence of per-kind ``discover()`` calls. Each kind's loader still
does the real work — the orchestrator is the uniform surface over
them. See :mod:`marcel_core.plugin.habitat` for the Protocol and the
five wrappers.
"""

from __future__ import annotations

import logging
from pathlib import Path

from marcel_core.plugin.habitat import (
    ChannelHabitat,
    Habitat,
    JobHabitat,
    SkillHabitat,
    SubagentHabitat,
    ToolkitHabitat,
)

log = logging.getLogger(__name__)


# Fixed dispatch order. Toolkit comes first because its @marcel_tool
# registration populates _metadata — scheduler.rebuild_schedule() in
# lifespan reads that dict. Channels second so their routers are
# mountable early. Jobs, agents, and skills are markdown/YAML-only and
# order-insensitive among themselves.
_KINDS: tuple[tuple[str, type], ...] = (
    ('toolkit', ToolkitHabitat),
    ('channel', ChannelHabitat),
    ('job', JobHabitat),
    ('subagent', SubagentHabitat),
    ('skill', SkillHabitat),
)


def discover_all_habitats(zoo_dir: Path | None) -> dict[str, list[Habitat]]:
    """Discover every habitat kind, grouped by kind name.

    For each kind in the fixed dispatch order:

    1. Call the wrapper's ``discover_all(zoo_dir)``.
    2. If the call raises, log the exception and record an empty list
       for that kind — a broken kind never takes down discovery for
       the others.
    3. Log one info line per kind with the count.

    Returns a dict whose keys are the five kind names
    (``'toolkit'``, ``'channel'``, ``'job'``, ``'subagent'``, ``'skill'``)
    and values are the discovered Habitat instances. Keys are always
    present even when a kind yielded zero results.
    """
    result: dict[str, list[Habitat]] = {}
    for kind_name, wrapper_cls in _KINDS:
        try:
            discovered: list[Habitat] = list(wrapper_cls.discover_all(zoo_dir))
        except Exception:
            log.exception('orchestrator: %s discovery failed — isolating from other kinds', kind_name)
            discovered = []
        result[kind_name] = discovered
        log.info('orchestrator: %s discovery → %d habitat(s)', kind_name, len(discovered))

    return result
