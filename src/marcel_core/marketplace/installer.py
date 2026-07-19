"""Install / update / remove flows (FEAT-260718-210a5f, FR2–FR4).

Every flow is **two-step and token-gated**: a review call renders the
human-readable summary and returns a token bound to the exact
``(source, candidate, ref)`` triple; the mutating call requires that token
back, so nothing can be installed that was not first rendered for review.
The token gate is channel-agnostic — the Telegram approval-button machinery
only exists on one channel, so the review gate is enforced structurally here
instead (deviation from FR2's "reuse the approval machinery" recorded on the
board). Every review and every mutation is appended to the existing audit
log (``storage.approvals.append_audit``, FR5).

Atomicity (FR3): candidates are fetched and validated in a temporary staging
directory; on any failure nothing exists under the zoo. Placement + git
commit happen last, and a failed commit rolls the placed files back out.
Git runs as argv lists with explicit ``cwd`` — never through a shell (the
``rollback.py`` precedent; the git_* tools' string interpolation is exactly
what this module must not copy).

Provenance: installs write ``.marcel-provenance.yaml`` inside the habitat
folder (source, url, ref, per-file content hashes). Update uses it to detect
household edits — any hash mismatch aborts loudly rather than overwriting a
local modification — and remove uses it only for display.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from marcel_core.marketplace.enablement import remove as remove_enablement, seed as seed_enablement
from marcel_core.marketplace.fetchers import Candidate, FetchError, browse_source, fetch_candidate
from marcel_core.marketplace.sources import SourceEntry, get_source

log = logging.getLogger(__name__)

PROVENANCE_FILENAME = '.marcel-provenance.yaml'
_KIND_ROOTS = {'skill': 'skills', 'connector': 'connectors'}


class InstallError(RuntimeError):
    """A flow aborted — the message names the violated rule, admin-readably."""


@dataclass
class Review:
    """A rendered review, gating one exact candidate state."""

    candidate: Candidate
    summary: str
    token: str


def _zoo_repo() -> Path:
    from marcel_core.config import settings

    zoo = settings.zoo_dir
    if zoo is None:
        raise InstallError('No zoo configured (MARCEL_ZOO_DIR) — nowhere to install habitats.')
    if not (zoo / '.git').is_dir():
        raise InstallError(f'The zoo at {zoo} is not a git checkout — installs must be git-committed (Recoverable).')
    return zoo


def _run_git(args: list[str], cwd: Path) -> str:
    try:
        proc = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise InstallError(f'git {args[0]} timed out') from exc
    if proc.returncode != 0:
        raise InstallError(f'git {args[0]} failed: {proc.stderr.strip() or proc.stdout.strip()}')
    return proc.stdout


def _token(entry: SourceEntry, candidate: Candidate) -> str:
    material = f'{entry.name}:{candidate.kind}:{candidate.name}:{candidate.ref}'
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def _file_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob('*')):
        if path.is_file() and path.name != PROVENANCE_FILENAME:
            hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _audit(action: str, *, user_slug: str, channel: str, summary: str, args: dict) -> None:
    from datetime import UTC, datetime

    from marcel_core.storage.approvals import append_audit

    append_audit(
        {
            'tool_name': f'marketplace.{action}',
            'user_slug': user_slug,
            'channel': channel,
            'summary': summary,
            'args': args,
            'resolved_at': datetime.now(UTC).isoformat(),
        }
    )


def _find_candidate(source_name: str, candidate_name: str) -> tuple[SourceEntry, Candidate]:
    entry = get_source(source_name)
    try:
        candidates = browse_source(entry)
    except FetchError as exc:
        raise InstallError(str(exc)) from exc
    for candidate in candidates:
        if candidate.name == candidate_name:
            return entry, candidate
    listing = ', '.join(sorted(c.name for c in candidates)) or '(none)'
    raise InstallError(f'{candidate_name!r} is not in source {source_name!r}. Available: {listing}.')


def _render_summary(entry: SourceEntry, candidate: Candidate, *, verb: str = 'Install') -> str:
    lines = [
        f'{verb} **{candidate.name}** ({candidate.kind}) from source `{entry.name}`',
        f'- Source: {entry.url} @ {candidate.ref[:12]}',
        f'- Description: {candidate.description or "(none)"}',
    ]
    if candidate.kind == 'skill':
        meta = candidate.metadata.get('metadata') or {}
        if isinstance(meta, dict) and meta:
            lines.append(f'- Marcel metadata: {", ".join(f"{k}={v}" for k, v in sorted(meta.items()))}')
        lines.append(f'- Files ({len(candidate.files)}): {", ".join(candidate.files[:12])}')
        if candidate.has_scripts:
            lines.append(
                '- ⚠️ Ships scripts/executable files — installing a skill is installing '
                'software; read them before confirming.'
            )
    else:
        remotes = candidate.metadata.get('remotes') or []
        urls = [str(r.get('url')) for r in remotes if isinstance(r, dict) and r.get('url')]
        lines.append(f'- Remote endpoint(s): {", ".join(urls) or "(none — not installable)"}')
    return '\n'.join(lines)


def review_install(source_name: str, candidate_name: str, *, user_slug: str, channel: str) -> Review:
    """Step 1 of the install gate: render the summary, mint the token."""
    entry, candidate = _find_candidate(source_name, candidate_name)
    review = Review(candidate=candidate, summary=_render_summary(entry, candidate), token=_token(entry, candidate))
    _audit(
        'review',
        user_slug=user_slug,
        channel=channel,
        summary=f'reviewed {candidate.kind} {candidate.name} from {source_name}',
        args={'source': source_name, 'name': candidate.name, 'ref': candidate.ref, 'token': review.token},
    )
    return review


def _validate_staged(kind: str, staged: Path) -> None:
    """Structural validation of a staged candidate — the violated rule is named.

    Skills go through the public skills-v2 frontmatter validator; connectors
    through the connector-schema validator. Both are pure functions usable on
    an arbitrary path — nothing imports or executes (NFR1).
    """
    if kind == 'skill':
        from marcel_core.skills.loader import _parse_frontmatter, validate_skill_frontmatter

        skill_md = staged / 'SKILL.md'
        if not skill_md.is_file():
            raise InstallError('Invalid skill: no SKILL.md in the candidate.')
        fm, _body = _parse_frontmatter(skill_md.read_text(encoding='utf-8'))
        error = validate_skill_frontmatter(fm, staged.name)
        if error:
            raise InstallError(f'Invalid skill: {error}')
    elif kind == 'connector':
        from marcel_core.connectors.loader import validate_connector_config

        connector_yaml = staged / 'connector.yaml'
        if not connector_yaml.is_file():
            raise InstallError('Invalid connector: no connector.yaml in the candidate.')
        raw = yaml.safe_load(connector_yaml.read_text(encoding='utf-8'))
        _config, error = validate_connector_config(raw, staged.name)
        if error:
            raise InstallError(f'Invalid connector: {error}')
    else:  # pragma: no cover - kinds come from our own fetchers
        raise InstallError(f'Unknown habitat kind {kind!r}.')


def _declared_default(kind: str, staged: Path) -> str:
    if kind == 'skill':
        from marcel_core.skills.loader import _parse_frontmatter

        fm, _ = _parse_frontmatter((staged / 'SKILL.md').read_text(encoding='utf-8'))
        meta = fm.get('metadata') or {}
        value = meta.get('marcel-default-enabled', 'all') if isinstance(meta, dict) else 'all'
    else:
        raw = yaml.safe_load((staged / 'connector.yaml').read_text(encoding='utf-8')) or {}
        value = raw.get('default_enabled', 'all')
    return value if value in ('all', 'admin', 'none') else 'all'


def _dest_root(zoo: Path, kind: str, placement_slug: str | None) -> Path:
    root = _KIND_ROOTS[kind]
    if placement_slug is None:
        return zoo / root
    from marcel_core.auth import valid_user_slug

    if not valid_user_slug(placement_slug):
        raise InstallError(f'Invalid placement slug {placement_slug!r}.')
    return zoo / 'users' / placement_slug / root


@dataclass
class InstallResult:
    path: Path
    commit: str
    seeded: str


def install(
    source_name: str,
    candidate_name: str,
    token: str,
    *,
    user_slug: str,
    channel: str,
    placement_slug: str | None = None,
) -> InstallResult:
    """Step 2: fetch → validate (staged) → place → zoo git commit → seed.

    ``token`` must come from :func:`review_install` for the same candidate at
    the same ref — a moved source invalidates it, forcing a fresh review.
    """
    zoo = _zoo_repo()
    entry, candidate = _find_candidate(source_name, candidate_name)
    if token != _token(entry, candidate):
        raise InstallError(
            'Review token does not match this candidate (the source moved, or the review was for '
            'something else). Browse and review again before installing.'
        )

    dest_parent = _dest_root(zoo, candidate.kind, placement_slug)
    dest = dest_parent / candidate.name
    if dest.exists():
        raise InstallError(f'{candidate.name!r} is already installed at {dest} — use the update flow.')

    with tempfile.TemporaryDirectory(prefix='marcel-install-') as tmp:
        try:
            staged = fetch_candidate(entry, candidate, Path(tmp))
        except FetchError as exc:
            raise InstallError(str(exc)) from exc
        _validate_staged(candidate.kind, staged)
        default = _declared_default(candidate.kind, staged)

        provenance = {
            'source': entry.name,
            'url': entry.url,
            'ref': candidate.ref,
            'files': _file_hashes(staged),
        }
        (staged / PROVENANCE_FILENAME).write_text(yaml.safe_dump(provenance, sort_keys=True), encoding='utf-8')

        dest_parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged), str(dest))

    try:
        _run_git(['add', '--', str(dest.relative_to(zoo))], cwd=zoo)
        _run_git(
            [
                'commit',
                '-q',
                '-m',
                f'marketplace: install {candidate.kind} {candidate.name} from {entry.name}@{candidate.ref[:12]}',
            ],
            cwd=zoo,
        )
        commit = _run_git(['rev-parse', 'HEAD'], cwd=zoo).strip()
    except InstallError:
        shutil.rmtree(dest, ignore_errors=True)
        _run_git(['checkout', '--', '.'], cwd=zoo)
        raise

    seeded = seed_enablement(_KIND_ROOTS[candidate.kind], candidate.name, default)
    _audit(
        'install',
        user_slug=user_slug,
        channel=channel,
        summary=f'installed {candidate.kind} {candidate.name} from {source_name}@{candidate.ref[:12]}',
        args={'source': source_name, 'name': candidate.name, 'ref': candidate.ref, 'commit': commit, 'seeded': seeded},
    )
    return InstallResult(path=dest, commit=commit, seeded=seeded)


def _installed_dir(zoo: Path, kind: str, name: str) -> Path:
    dest = zoo / _KIND_ROOTS[kind] / name
    if not dest.is_dir():
        raise InstallError(f'No installed {kind} named {name!r} under {zoo / _KIND_ROOTS[kind]}.')
    return dest


def _load_provenance(installed: Path) -> dict:
    path = installed / PROVENANCE_FILENAME
    if not path.is_file():
        raise InstallError(
            f'{installed.name!r} is not source-tracked (no {PROVENANCE_FILENAME}) — it was hand-installed; '
            'update it by hand in the zoo repo.'
        )
    raw = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    return raw if isinstance(raw, dict) else {}


def review_update(kind: str, name: str, *, user_slug: str, channel: str) -> Review:
    """Step 1 of update: conflict check, upstream diff summary, token."""
    zoo = _zoo_repo()
    installed = _installed_dir(zoo, kind, name)
    provenance = _load_provenance(installed)

    recorded: dict[str, str] = provenance.get('files') or {}
    current = _file_hashes(installed)
    edited = sorted(set(f for f, h in recorded.items() if current.get(f) != h) | (set(current) - set(recorded)))
    if edited:
        raise InstallError(
            f'{name!r} has local household edits ({", ".join(edited[:8])}) — updating would overwrite '
            'them. Reconcile in the zoo repo first (commit or revert the edits), then retry.'
        )

    entry, candidate = _find_candidate(str(provenance.get('source', '')), name)
    if candidate.ref == provenance.get('ref'):
        raise InstallError(f'{name!r} is already at the source ref {candidate.ref[:12]} — nothing to update.')

    added = sorted(set(candidate.files) - set(recorded))
    removed = sorted(set(recorded) - set(candidate.files))
    summary = _render_summary(entry, candidate, verb='Update') + (
        f'\n- Upstream change: {provenance.get("ref", "?")[:12]} → {candidate.ref[:12]}'
        f'\n- Files added: {", ".join(added) or "none"} · removed: {", ".join(removed) or "none"}'
    )
    review = Review(candidate=candidate, summary=summary, token=_token(entry, candidate))
    _audit(
        'review',
        user_slug=user_slug,
        channel=channel,
        summary=f'reviewed update of {kind} {name}',
        args={'source': entry.name, 'name': name, 'ref': candidate.ref, 'token': review.token},
    )
    return review


def update(kind: str, name: str, token: str, *, user_slug: str, channel: str) -> InstallResult:
    """Step 2 of update: re-fetch at the reviewed ref, replace, commit."""
    zoo = _zoo_repo()
    installed = _installed_dir(zoo, kind, name)
    provenance = _load_provenance(installed)
    entry, candidate = _find_candidate(str(provenance.get('source', '')), name)
    if token != _token(entry, candidate):
        raise InstallError('Review token does not match — review the update again before applying.')

    with tempfile.TemporaryDirectory(prefix='marcel-update-') as tmp:
        try:
            staged = fetch_candidate(entry, candidate, Path(tmp))
        except FetchError as exc:
            raise InstallError(str(exc)) from exc
        _validate_staged(kind, staged)
        new_provenance = {
            'source': entry.name,
            'url': entry.url,
            'ref': candidate.ref,
            'files': _file_hashes(staged),
        }
        (staged / PROVENANCE_FILENAME).write_text(yaml.safe_dump(new_provenance, sort_keys=True), encoding='utf-8')

        backup = installed.with_name(f'.{name}.updating')
        installed.rename(backup)
        try:
            shutil.move(str(staged), str(installed))
        except OSError:
            backup.rename(installed)
            raise
        shutil.rmtree(backup)

    try:
        _run_git(['add', '--', str(installed.relative_to(zoo))], cwd=zoo)
        _run_git(
            ['commit', '-q', '-m', f'marketplace: update {kind} {name} to {entry.name}@{candidate.ref[:12]}'],
            cwd=zoo,
        )
        commit = _run_git(['rev-parse', 'HEAD'], cwd=zoo).strip()
    except InstallError:
        _run_git(['checkout', '--', str(installed.relative_to(zoo))], cwd=zoo)
        raise

    _audit(
        'update',
        user_slug=user_slug,
        channel=channel,
        summary=f'updated {kind} {name} to {candidate.ref[:12]}',
        args={'source': entry.name, 'name': name, 'ref': candidate.ref, 'commit': commit},
    )
    return InstallResult(path=installed, commit=commit, seeded='(unchanged)')


def remove(kind: str, name: str, *, user_slug: str, channel: str) -> str:
    """Delete + commit + enablement cleanup. Returns the removal commit sha."""
    zoo = _zoo_repo()
    installed = _installed_dir(zoo, kind, name)
    rel = str(installed.relative_to(zoo))
    _run_git(['rm', '-r', '-q', '--', rel], cwd=zoo)
    _run_git(['commit', '-q', '-m', f'marketplace: remove {kind} {name}'], cwd=zoo)
    commit = _run_git(['rev-parse', 'HEAD'], cwd=zoo).strip()
    cleaned = remove_enablement(_KIND_ROOTS[kind], name)
    _audit(
        'remove',
        user_slug=user_slug,
        channel=channel,
        summary=f'removed {kind} {name}' + (' (enablement entry cleaned)' if cleaned else ''),
        args={'name': name, 'kind': kind, 'commit': commit},
    )
    return commit
