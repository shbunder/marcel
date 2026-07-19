"""Source fetchers — browse and materialize candidates (FEAT-260718-210a5f, FR1).

One fetcher per :class:`~marcel_core.marketplace.sources.SourceType`, behind a
shared protocol so new source types are additive. Two operations:

- ``browse(entry)`` — read-only candidate listing. Parses text only
  (frontmatter, JSON catalogs); never imports, never executes, never writes
  under a zoo or data root (NFR1). Git browsing clones shallow into a
  temporary directory that is deleted before returning.
- ``fetch(entry, candidate, staging)`` — materialize one candidate's files
  into a caller-owned staging directory for validation. Still nothing
  executes; the installer owns everything after staging.

Git URLs come exclusively from the admin-curated ``sources.yaml`` (the trust
root), are passed after ``--`` so a name can never become a flag, and clones
run with ``GIT_TERMINAL_PROMPT=0`` so a bad URL fails instead of hanging.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from marcel_core.marketplace.sources import SourceEntry, SourceType

log = logging.getLogger(__name__)

_GIT_TIMEOUT_SECONDS = 120
_REGISTRY_TIMEOUT_SECONDS = 20


class FetchError(RuntimeError):
    """A source could not be browsed or fetched — message is admin-readable."""


@dataclass
class Candidate:
    """One installable habitat, as seen from a source (nothing on disk yet)."""

    source: str
    kind: str  # 'skill' | 'connector'
    name: str
    description: str
    path_in_source: str  # repo-relative dir, or the registry entry id
    ref: str  # resolved commit sha / registry version — pinned at browse time
    metadata: dict = field(default_factory=dict)  # parsed frontmatter / registry entry
    files: list[str] = field(default_factory=list)
    has_scripts: bool = False


def _run_git(args: list[str], *, cwd: Path | None = None) -> str:
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
    try:
        proc = subprocess.run(
            ['git', *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise FetchError(f'git {args[0]} timed out after {_GIT_TIMEOUT_SECONDS}s') from exc
    if proc.returncode != 0:
        raise FetchError(f'git {args[0]} failed: {proc.stderr.strip() or proc.stdout.strip()}')
    return proc.stdout


def _clone_shallow(entry: SourceEntry, dest: Path) -> str:
    """Shallow-clone the source at its pinned ref; returns the resolved sha."""
    url = entry.url.removeprefix('file://')
    branch = ['--branch', entry.ref] if entry.ref else []
    _run_git(['clone', '--depth', '1', '--quiet', *branch, '--', url, str(dest)])
    return _run_git(['rev-parse', 'HEAD'], cwd=dest).strip()


def _scan_skill_dirs(root: Path, source_name: str, ref: str) -> list[Candidate]:
    """Candidates for every ``<dir>/SKILL.md`` under *root* — text parse only."""
    from marcel_core.skills.loader import _parse_frontmatter

    candidates: list[Candidate] = []
    for skill_md in sorted(root.glob('*/SKILL.md')):
        skill_dir = skill_md.parent
        if skill_dir.name.startswith(('_', '.')):
            continue
        fm, _body = _parse_frontmatter(skill_md.read_text(encoding='utf-8', errors='replace'))
        files = sorted(str(p.relative_to(skill_dir)) for p in skill_dir.rglob('*') if p.is_file())
        candidates.append(
            Candidate(
                source=source_name,
                kind='skill',
                name=str(fm.get('name') or skill_dir.name),
                description=str(fm.get('description', '')),
                path_in_source=str(skill_dir.relative_to(root.parent if root.name else root)),
                ref=ref,
                metadata={k: v for k, v in fm.items() if isinstance(k, str)},
                files=files,
                has_scripts=any(f.startswith('scripts/') or f.endswith(('.py', '.sh')) for f in files),
            )
        )
    return candidates


class AgentskillsGitFetcher:
    """A git repo (optionally a subdir) of agentskills-format skill folders."""

    def browse(self, entry: SourceEntry) -> list[Candidate]:
        with tempfile.TemporaryDirectory(prefix='marcel-browse-') as tmp:
            clone = Path(tmp) / 'repo'
            sha = _clone_shallow(entry, clone)
            root = clone / entry.subdir if entry.subdir else clone
            if not root.is_dir():
                raise FetchError(f'source {entry.name!r}: subdir {entry.subdir!r} does not exist in the repo')
            found = _scan_skill_dirs(root, entry.name, sha)
        if not found:
            log.info('marketplace: source %s has no skill folders', entry.name)
        return found

    def fetch(self, entry: SourceEntry, candidate: Candidate, staging: Path) -> Path:
        with tempfile.TemporaryDirectory(prefix='marcel-fetch-') as tmp:
            clone = Path(tmp) / 'repo'
            sha = _clone_shallow(entry, clone)
            if candidate.ref and sha != candidate.ref:
                raise FetchError(
                    f'source {entry.name!r} moved since browse ({candidate.ref[:12]} → {sha[:12]}) — '
                    'browse again and re-review before installing'
                )
            root = clone / entry.subdir if entry.subdir else clone
            src = root / Path(candidate.path_in_source).name
            if not (src / 'SKILL.md').is_file():
                raise FetchError(f'candidate {candidate.name!r} has no SKILL.md at {candidate.path_in_source!r}')
            dest = staging / src.name
            shutil.copytree(src, dest)
        return dest


class PluginMarketplaceFetcher:
    """A Claude-plugin-style repo: ``.claude-plugin/marketplace.json`` names
    plugins; each plugin's ``skills/`` folders are the installable units."""

    def browse(self, entry: SourceEntry) -> list[Candidate]:
        with tempfile.TemporaryDirectory(prefix='marcel-browse-') as tmp:
            clone = Path(tmp) / 'repo'
            sha = _clone_shallow(entry, clone)
            index = clone / '.claude-plugin' / 'marketplace.json'
            if not index.is_file():
                raise FetchError(f'source {entry.name!r}: no .claude-plugin/marketplace.json in the repo')
            try:
                listing = json.loads(index.read_text(encoding='utf-8'))
            except ValueError as exc:
                raise FetchError(f'source {entry.name!r}: marketplace.json is not valid JSON: {exc}') from exc
            candidates: list[Candidate] = []
            for plugin in listing.get('plugins', []):
                plugin_root = clone / str(plugin.get('source', '')).lstrip('./')
                skills_root = plugin_root / 'skills'
                if skills_root.is_dir():
                    candidates.extend(_scan_skill_dirs(skills_root, entry.name, sha))
        return candidates

    def fetch(self, entry: SourceEntry, candidate: Candidate, staging: Path) -> Path:
        with tempfile.TemporaryDirectory(prefix='marcel-fetch-') as tmp:
            clone = Path(tmp) / 'repo'
            sha = _clone_shallow(entry, clone)
            if candidate.ref and sha != candidate.ref:
                raise FetchError(
                    f'source {entry.name!r} moved since browse ({candidate.ref[:12]} → {sha[:12]}) — '
                    'browse again and re-review before installing'
                )
            matches = [p.parent for p in clone.rglob(f'{Path(candidate.path_in_source).name}/SKILL.md')]
            if not matches:
                raise FetchError(f'candidate {candidate.name!r} no longer exists in the source')
            dest = staging / matches[0].name
            shutil.copytree(matches[0], dest)
        return dest


class McpRegistryFetcher:
    """A read-only HTTP MCP-server catalog (e.g. the official registry).

    Browse maps registry entries onto connector candidates; fetch writes a
    ``connector.yaml`` for the selected entry into staging (FR4) — remote
    servers as ``http`` transport, package-declared local servers are refused
    (running arbitrary packages is beyond the review gate's power to make
    safe). Auth material is never fetched: the connector routes through the
    normal SETUP flow after install.
    """

    def browse(self, entry: SourceEntry) -> list[Candidate]:
        import httpx

        try:
            resp = httpx.get(
                f'{entry.url.rstrip("/")}/v0/servers',
                timeout=_REGISTRY_TIMEOUT_SECONDS,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise FetchError(f'source {entry.name!r}: registry query failed: {exc}') from exc
        try:
            listing = resp.json()
        except ValueError as exc:
            raise FetchError(f'source {entry.name!r}: registry returned invalid JSON') from exc

        candidates: list[Candidate] = []
        for server in listing.get('servers', []):
            name = str(server.get('name', '')).rsplit('/', 1)[-1].lower().replace('_', '-')
            if not name:
                continue
            candidates.append(
                Candidate(
                    source=entry.name,
                    kind='connector',
                    name=name,
                    description=str(server.get('description', '')),
                    path_in_source=str(server.get('name', name)),
                    ref=str(server.get('version', 'latest')),
                    metadata=server if isinstance(server, dict) else {},
                )
            )
        return candidates

    def fetch(self, entry: SourceEntry, candidate: Candidate, staging: Path) -> Path:
        remotes = candidate.metadata.get('remotes') or []
        remote_url = next(
            (str(r.get('url')) for r in remotes if isinstance(r, dict) and r.get('url')),
            None,
        )
        if remote_url is None:
            raise FetchError(
                f'{candidate.name!r} declares no remote endpoint — package-run servers are not '
                'installable from a registry (write the connector park by hand if you trust it)'
            )
        auth_mode = 'oauth' if any(r.get('auth') == 'oauth' for r in remotes if isinstance(r, dict)) else 'none'
        dest = staging / candidate.name
        dest.mkdir(parents=True)
        connector_yaml = {
            'name': candidate.name,
            'description': candidate.description or f'MCP server from {entry.name}',
            'server': {'transport': 'http', 'url': remote_url},
            'auth': {'mode': auth_mode, 'per_user': auth_mode != 'none'},
        }
        if auth_mode == 'oauth':
            # Issuer discovery is SETUP-flow work; the placeholder keeps the
            # schema honest about what the admin must still provide.
            connector_yaml['auth']['oauth'] = {'issuer': remote_url, 'client_id': 'CONFIGURE-ME'}
        import yaml

        (dest / 'connector.yaml').write_text(yaml.safe_dump(connector_yaml, sort_keys=False), encoding='utf-8')
        return dest


_FETCHERS = {
    SourceType.AGENTSKILLS_GIT: AgentskillsGitFetcher(),
    SourceType.PLUGIN_MARKETPLACE: PluginMarketplaceFetcher(),
    SourceType.MCP_REGISTRY: McpRegistryFetcher(),
}


def fetcher_for(entry: SourceEntry):
    """The fetcher instance for a source entry."""
    return _FETCHERS[entry.type]


def browse_source(entry: SourceEntry) -> list[Candidate]:
    return fetcher_for(entry).browse(entry)


def fetch_candidate(entry: SourceEntry, candidate: Candidate, staging: Path) -> Path:
    return fetcher_for(entry).fetch(entry, candidate, staging)
