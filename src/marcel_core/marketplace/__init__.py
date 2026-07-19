"""Habitat marketplace — the *available* state (FEAT-260718-210a5f).

Implements the first hop of the three-state lifecycle (ADR-260718-7addc8):
trusted sources the admin can browse, and install/update/remove flows that
land every change as a git commit in the zoo repo before anything becomes
discoverable. Nothing fetched executes before the install commit — browsing
parses text (safe YAML/JSON, frontmatter), never imports, never runs.

Layout:

- :mod:`.sources` — the ``<zoo>/sources.yaml`` schema + loader.
- :mod:`.fetchers` — one fetcher per source type behind a shared protocol
  (``agentskills-git``, ``plugin-marketplace``, ``mcp-registry``).
- :mod:`.installer` — staged validate → place → zoo git commit; update with
  local-edit conflict detection; remove with enablement cleanup.
- :mod:`.enablement` — seeding/cleanup of the data-root enablement manifest
  (format per ADR-260707-c9919f; enforcement is FEAT-260707-acb2b6's).
- :mod:`.tool` — the admin-gated conversational surface.
"""

from marcel_core.marketplace.sources import SourceEntry, SourceType, load_sources

__all__ = ['SourceEntry', 'SourceType', 'load_sources']
