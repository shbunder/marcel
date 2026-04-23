"""Habitat Protocol — uniform discovery surface across the five habitat kinds.

Marcel discovers five kinds of habitats at startup
(:class:`ToolkitHabitat`, :class:`ChannelHabitat`, :class:`SkillHabitat`,
:class:`SubagentHabitat`, :class:`JobHabitat`). Each has its own native
loader with different signatures (side-effecting ``discover()`` vs
list-returning ``load_agents()`` vs per-user ``load_skills(user_slug)``).

This module adds a uniform wrapper so the orchestrator, logging, and
test assertions can treat all five the same way. Wrappers are
**additive** — the native loaders keep working unchanged; the Protocol
just absorbs the signature differences.

Use :func:`marcel_core.plugin.orchestrator.discover_all_habitats` as the
entry point; this module is the Protocol definition + the five concrete
wrappers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class Habitat(Protocol):
    """The minimal uniform surface for any habitat kind.

    Kind-specific wrappers carry richer metadata (provides, capabilities,
    frontmatter) as extra attributes; the Protocol itself only guarantees
    the three fields the orchestrator and logging need.

    - ``kind``: one of ``'toolkit'``, ``'channel'``, ``'skill'``,
      ``'subagent'``, ``'job'``. Used for per-kind grouping in the
      orchestrator's return dict and in logs.
    - ``name``: the habitat's local identifier (directory name for
      filesystem-backed kinds, ``ChannelPlugin.name`` for channels).
    - ``source``: where the habitat was loaded from — typically a path
      string or a coarse tag like ``'zoo'`` / ``'data'``. Used for
      logging and straggler grep, not for behaviour.
    """

    kind: str
    name: str
    source: str


@dataclass(frozen=True, slots=True)
class ToolkitHabitat:
    """Wraps a discovered toolkit habitat (``<zoo>/toolkit/<name>/``).

    Carries the ``provides`` handler-id list from the habitat's
    ``toolkit.yaml`` so callers don't have to re-read ``_metadata``.
    """

    name: str
    source: str
    provides: tuple[str, ...]
    kind: str = 'toolkit'

    @classmethod
    def discover_all(cls, zoo_dir: Path | None) -> list[ToolkitHabitat]:
        """Trigger toolkit discovery and wrap every resulting habitat.

        Calls :func:`marcel_core.toolkit.discover` — the standard
        side-effecting loader that imports each habitat's ``__init__.py``
        and registers handlers via ``@marcel_tool``. Post-call state is
        read from ``_metadata``. Safe to call repeatedly; discovery is
        idempotent via ``sys.modules``.
        """
        from marcel_core.toolkit import _metadata, discover

        discover()

        if zoo_dir is None:
            return []

        result: list[ToolkitHabitat] = []
        for habitat_name, meta in sorted(_metadata.items()):
            # Prefer toolkit/ over integrations/ (Phase 3 of ISSUE-3c1534).
            for subdir in ('toolkit', 'integrations'):
                path = zoo_dir / subdir / habitat_name
                if path.is_dir():
                    result.append(
                        cls(
                            name=habitat_name,
                            source=str(path),
                            provides=tuple(meta.provides),
                        )
                    )
                    break
        return result


@dataclass(frozen=True, slots=True)
class ChannelHabitat:
    """Wraps a discovered channel plugin (``<zoo>/channels/<name>/``)."""

    name: str
    source: str
    has_router: bool
    kind: str = 'channel'

    @classmethod
    def discover_all(cls, zoo_dir: Path | None) -> list[ChannelHabitat]:
        """Trigger channel discovery and wrap every registered plugin.

        The native :func:`marcel_core.plugin.channels.discover` is
        idempotent (sys.modules-guarded), so calling it here is safe
        even though ``main.py`` also calls it at module-load time so
        its router-mount loop can see registered plugins.
        """
        from marcel_core.plugin.channels import discover, get_channel, list_channels

        discover()

        result: list[ChannelHabitat] = []
        for channel_name in list_channels():
            plugin = get_channel(channel_name)
            if plugin is None:
                continue
            source = str(zoo_dir / 'channels' / channel_name) if zoo_dir is not None else f'<channel:{channel_name}>'
            result.append(
                cls(
                    name=channel_name,
                    source=source,
                    has_router=plugin.router is not None,
                )
            )
        return result


@dataclass(frozen=True, slots=True)
class SkillHabitat:
    """Wraps a skill habitat directory on disk (``<zoo>/skills/<name>/``).

    Discovery is filesystem-only — requirement-based filtering (the
    reason :func:`marcel_core.skills.loader.load_skills` takes a
    ``user_slug``) stays in the loader and runs per-user-turn, not at
    kernel startup.
    """

    name: str
    source: str
    kind: str = 'skill'

    @classmethod
    def discover_all(cls, zoo_dir: Path | None) -> list[SkillHabitat]:
        if zoo_dir is None:
            return []
        skills_dir = zoo_dir / 'skills'
        if not skills_dir.is_dir():
            return []
        result: list[SkillHabitat] = []
        for entry in sorted(skills_dir.iterdir()):
            if entry.is_dir() and not entry.name.startswith(('_', '.')):
                result.append(cls(name=entry.name, source=str(entry)))
        return result


@dataclass(frozen=True, slots=True)
class SubagentHabitat:
    """Wraps a loaded subagent definition (markdown under ``<zoo>/agents/``).

    Backed by :func:`marcel_core.agents.loader.load_agents` which already
    returns `AgentDoc` instances; the wrapper just maps them onto the
    uniform surface.
    """

    name: str
    source: str
    kind: str = 'subagent'

    @classmethod
    def discover_all(cls, zoo_dir: Path | None) -> list[SubagentHabitat]:
        from marcel_core.agents.loader import load_agents

        return [cls(name=doc.name, source=doc.source) for doc in load_agents()]


@dataclass(frozen=True, slots=True)
class JobHabitat:
    """Wraps a job template habitat (``<zoo>/jobs/<name>/template.yaml``).

    Filesystem-based discovery so we don't depend on the template
    loader's required-key validation for the habitat count — a template
    that fails validation still exists on disk and should appear in
    logs as a discovered-but-broken entry.
    """

    name: str
    source: str
    kind: str = 'job'

    @classmethod
    def discover_all(cls, zoo_dir: Path | None) -> list[JobHabitat]:
        if zoo_dir is None:
            return []
        jobs_dir = zoo_dir / 'jobs'
        if not jobs_dir.is_dir():
            return []
        result: list[JobHabitat] = []
        for entry in sorted(jobs_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith(('_', '.')):
                continue
            if not (entry / 'template.yaml').exists():
                continue  # instance directory, not a template
            result.append(cls(name=entry.name, source=str(entry)))
        return result
