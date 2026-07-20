"""Habitat Protocol — uniform discovery surface for the eagerly-discovered habitats.

Of the five habitat kinds in the taxonomy, four are discovered at startup
(:class:`ChannelHabitat`, :class:`SkillHabitat`,
:class:`SubagentHabitat`, :class:`JobHabitat`); connectors are the fifth kind
but resolve per-user at agent-build time, not here. Each has its own native
loader with different signatures (side-effecting ``discover()`` vs
list-returning ``load_agent_docs(user_slug)`` vs per-user ``load_skills(user_slug)``).

This module adds a uniform wrapper so the orchestrator, logging, and
test assertions can treat all four the same way. Wrappers are
**additive** — the native loaders keep working unchanged; the Protocol
just absorbs the signature differences.

Use :func:`marcel_core.plugin.orchestrator.discover_all_habitats` as the
entry point; this module is the Protocol definition + the four concrete
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

    - ``kind``: one of ``'channel'``, ``'skill'``,
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

    Backed by :func:`marcel_core.capabilities.subagents.load_agent_docs`
    (FEAT-260718-b6d1da); the wrapper just maps the docs onto the uniform
    surface. Discovery here is the global view (no user chain) — it feeds
    the boot summary, not an agent build.
    """

    name: str
    source: str
    kind: str = 'subagent'

    @classmethod
    def discover_all(cls, zoo_dir: Path | None) -> list[SubagentHabitat]:
        from marcel_core.capabilities.subagents import load_agent_docs

        return [cls(name=doc.name, source=doc.source) for doc in load_agent_docs()]


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
