"""``<zoo>/sources.yaml`` — the trusted-source registry (FEAT-260718-210a5f, FR1).

The registry is itself trusted admin surface: editing it is zoo-repo work,
and a bad entry is a code-execution risk gated only by the install review
step — so the schema is strict (``extra='forbid'``), URLs are validated, and
the file is always ``yaml.safe_load``-ed (NFR1).
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

log = logging.getLogger(__name__)

SOURCES_FILENAME = 'sources.yaml'


class SourceType(str, Enum):
    """What protocol a source speaks."""

    AGENTSKILLS_GIT = 'agentskills-git'  # git repo (or subdir) of spec-format skills
    PLUGIN_MARKETPLACE = 'plugin-marketplace'  # Claude-plugin-style repo with marketplace.json
    MCP_REGISTRY = 'mcp-registry'  # read-only HTTP catalog of MCP servers


class SourceEntry(BaseModel):
    """One trusted source."""

    model_config = ConfigDict(extra='forbid')

    name: str = Field(min_length=1, max_length=64, pattern=r'^[a-z0-9][a-z0-9._-]*$')
    type: SourceType
    url: str = Field(min_length=1)
    ref: str | None = None  # git ref pin (branch/tag/sha); None = default branch
    subdir: str | None = None  # skills live under this path inside the repo
    description: str = ''

    @model_validator(mode='after')
    def _url_matches_type(self) -> SourceEntry:
        """Git sources take https or local paths (fixtures); registries https only.

        Plain ``http://`` is refused outright — a browse response drives what
        the admin is later asked to install, so a LAN interceptor on the
        fetch path is a supply-chain position (NFR1). Local paths exist for
        the Terrarium fixture repos and for household-curated local mirrors.
        """
        is_https = self.url.startswith('https://')
        is_local = self.url.startswith('/') or self.url.startswith('file://')
        if self.type is SourceType.MCP_REGISTRY:
            if not is_https:
                raise ValueError(f'source {self.name!r}: mcp-registry urls must be https')
            if self.ref or self.subdir:
                raise ValueError(f'source {self.name!r}: mcp-registry takes no ref/subdir')
        elif not (is_https or is_local):
            raise ValueError(f'source {self.name!r}: git sources take https:// urls or absolute local paths')
        if self.subdir is not None and ('..' in self.subdir or self.subdir.startswith('/')):
            raise ValueError(f'source {self.name!r}: subdir must be a relative path inside the repo')
        return self


class SourcesConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    sources: list[SourceEntry] = Field(default_factory=list)

    @model_validator(mode='after')
    def _unique_names(self) -> SourcesConfig:
        names = [s.name for s in self.sources]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f'duplicate source name(s): {", ".join(sorted(dupes))}')
        return self


def sources_path() -> Path | None:
    """Where ``sources.yaml`` lives — ``None`` when no zoo is configured."""
    from marcel_core.config import settings

    zoo = settings.zoo_dir
    return None if zoo is None else zoo / SOURCES_FILENAME


def load_sources() -> list[SourceEntry]:
    """Load and validate the registry; missing file ⇒ empty (never an error).

    A malformed file raises ``ValueError`` with the pydantic detail — the
    registry is the trust root, so unlike habitat discovery it fails loud
    rather than degrading.
    """
    path = sources_path()
    if path is None or not path.is_file():
        return []
    raw = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    if not isinstance(raw, dict):
        raise ValueError(f'{path}: sources.yaml must be a mapping with a `sources:` list')
    try:
        return SourcesConfig.model_validate(raw).sources
    except Exception as exc:
        raise ValueError(f'{path}: {exc}') from exc


def get_source(name: str) -> SourceEntry:
    """One source by name, or a loud error listing what exists."""
    entries = load_sources()
    for entry in entries:
        if entry.name == name:
            return entry
    listing = ', '.join(sorted(e.name for e in entries)) or '(none configured)'
    raise ValueError(f'No source named {name!r} in sources.yaml. Configured: {listing}.')
