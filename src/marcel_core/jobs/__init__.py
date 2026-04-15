"""Job system — CRUD operations and SKILL.md-style flat storage.

Jobs live directly under the data root at ``<data_root>/jobs/<slug>/``:

- ``JOB.md``          — YAML frontmatter + markdown body (``## System Prompt``
                        and ``## Task`` sections)
- ``state.json``      — mutable runtime state (errors, timestamps)
- ``runs/<user>.jsonl`` — per-user run log (``runs/_system.jsonl`` for
                          system-scope jobs with ``users: []``)

Directory names are slugified from ``job.name``. The directory is chosen
at save time and never renamed, so callers can safely update ``job.name``
without the stored slug drifting.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from marcel_core.jobs.models import JobDefinition, JobRun
from marcel_core.storage._root import data_root

log = logging.getLogger(__name__)


SYSTEM_USER = '_system'
"""Reserved slug for system-scope jobs (``users: []``).

Used as the run-log filename (``runs/_system.jsonl``) and as a sentinel
inside the executor where a user-scoped value would otherwise be required.
The real ``~/.marcel/users/_system/`` directory never exists, so credential
and memory lookups for this slug naturally return empty.
"""

_FRONTMATTER_FIELDS: tuple[str, ...] = (
    'id',
    'name',
    'description',
    'users',
    'status',
    'created_at',
    'trigger',
    'model',
    'skills',
    'request_limit',
    'allow_local_fallback',
    'allow_fallback_chain',
    'notify',
    'channel',
    'max_retries',
    'retry_delay_seconds',
    'backoff_schedule',
    'timeout_seconds',
    'alert_after_consecutive_failures',
    'alert_cooldown_seconds',
    'retention_days',
    'template',
)

_STATE_FIELDS: tuple[str, ...] = (
    'updated_at',
    'consecutive_errors',
    'last_error_at',
    'schedule_errors',
    'last_failure_alert_at',
)

_SYSTEM_PROMPT_HEADER = re.compile(r'^##\s+System\s+Prompt\s*$', re.MULTILINE)
_TASK_HEADER = re.compile(r'^##\s+Task\s*$', re.MULTILINE)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _jobs_root() -> Path:
    """Return the flat jobs root (``<data_root>/jobs``), creating it if needed."""
    d = data_root() / 'jobs'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slugify(name: str) -> str:
    """Slugify a job name to a safe directory identifier.

    Lower-cased, non-alphanumerics collapsed to single ``-``, stripped of
    leading/trailing dashes. Returns ``"job"`` for an empty result so the
    path is always non-empty.
    """
    s = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    return s or 'job'


def _read_frontmatter_only(path: Path) -> dict | None:
    """Return the YAML frontmatter dict from a JOB.md, or None if unreadable."""
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return None
    fm, _ = _parse_frontmatter(text)
    return fm


def _find_job_dir_by_id(job_id: str) -> Path | None:
    """Locate a job's directory by scanning JOB.md frontmatter for a matching id."""
    root = _jobs_root()
    if not root.is_dir():
        return None
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith(('_', '.')):
            continue
        fm = _read_frontmatter_only(d / 'JOB.md')
        if fm is None:
            continue
        if fm.get('id') == job_id:
            return d
    return None


def _resolve_slug(job: JobDefinition) -> str:
    """Return the directory slug for ``job``.

    If the job already exists on disk (matched by ``id``), reuse its current
    directory name — renaming a job does not move its directory. Otherwise
    derive a fresh slug from :attr:`~JobDefinition.name`, appending ``-N``
    to deduplicate against existing directories.
    """
    existing = _find_job_dir_by_id(job.id)
    if existing is not None:
        return existing.name

    base = _slugify(job.name)
    root = _jobs_root()
    candidate = base
    n = 2
    while (root / candidate).exists():
        candidate = f'{base}-{n}'
        n += 1
    return candidate


# ---------------------------------------------------------------------------
# Frontmatter / body parsing
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown file.

    Returns ``(frontmatter, body)``. If no frontmatter is found, returns an
    empty dict and the full text.
    """
    if not text.startswith('---'):
        return {}, text
    end = text.find('\n---', 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + 4 :].lstrip('\n')
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, body


def _parse_body(body: str) -> tuple[str, str]:
    """Extract ``## System Prompt`` and ``## Task`` sections from a JOB.md body."""
    sp_match = _SYSTEM_PROMPT_HEADER.search(body)
    task_match = _TASK_HEADER.search(body)
    if sp_match is None or task_match is None:
        raise ValueError('JOB.md body must contain "## System Prompt" and "## Task" sections')

    if sp_match.start() < task_match.start():
        system_prompt = body[sp_match.end() : task_match.start()].strip()
        task = body[task_match.end() :].strip()
    else:
        task = body[task_match.end() : sp_match.start()].strip()
        system_prompt = body[sp_match.end() :].strip()

    return system_prompt, task


def _render_job_md(job: JobDefinition) -> str:
    """Serialize ``job`` to the JOB.md text format."""
    data = json.loads(job.model_dump_json())
    fm = {k: data[k] for k in _FRONTMATTER_FIELDS if k in data}
    fm_yaml = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, default_flow_style=False)
    body = f'## System Prompt\n\n{job.system_prompt.strip()}\n\n## Task\n\n{job.task.strip()}\n'
    return f'---\n{fm_yaml}---\n\n{body}'


def _state_dict(job: JobDefinition) -> dict:
    """Return the mutable-state slice of ``job`` as a JSON-friendly dict."""
    data = json.loads(job.model_dump_json())
    return {k: data[k] for k in _STATE_FIELDS if k in data}


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def save_job(job: JobDefinition) -> Path:
    """Write a job definition to disk and return its directory path."""
    slug = _resolve_slug(job)
    d = _jobs_root() / slug
    d.mkdir(parents=True, exist_ok=True)

    (d / 'JOB.md').write_text(_render_job_md(job), encoding='utf-8')
    (d / 'state.json').write_text(json.dumps(_state_dict(job), indent=2, default=str), encoding='utf-8')
    return d


def _load_job_from_dir(d: Path) -> JobDefinition | None:
    """Reconstruct a :class:`JobDefinition` from its on-disk directory."""
    job_md = d / 'JOB.md'
    if not job_md.exists():
        return None

    try:
        text = job_md.read_text(encoding='utf-8')
        fm, body = _parse_frontmatter(text)
        system_prompt, task = _parse_body(body)

        data: dict = dict(fm)
        data['system_prompt'] = system_prompt
        data['task'] = task

        state_path = d / 'state.json'
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding='utf-8'))
                if isinstance(state, dict):
                    data.update(state)
            except (OSError, json.JSONDecodeError):
                log.warning('Failed to read state.json for %s', d.name)

        job = JobDefinition.model_validate(data)
    except Exception:
        log.exception('Failed to load job from %s', d)
        return None

    # Self-heal legacy unqualified model names (pre-ISSUE-073).
    if ':' not in job.model:
        old = job.model
        job.model = f'anthropic:{old}'
        log.info('Migrated legacy model name for job %s: %s → %s', job.id, old, job.model)
        save_job(job)

    return job


def load_job(job_id: str) -> JobDefinition | None:
    """Load a single job definition by id, or *None* if it doesn't exist."""
    d = _find_job_dir_by_id(job_id)
    if d is None:
        return None
    return _load_job_from_dir(d)


def list_all_jobs() -> list[JobDefinition]:
    """Return every job on disk, regardless of user scoping."""
    root = _jobs_root()
    if not root.is_dir():
        return []
    jobs: list[JobDefinition] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith(('_', '.')):
            continue
        job = _load_job_from_dir(d)
        if job is not None:
            jobs.append(job)
    return jobs


def list_jobs(user_slug: str) -> list[JobDefinition]:
    """Return jobs that target ``user_slug`` (membership in ``job.users``).

    System-scope jobs (``users: []``) are excluded — they are not "owned"
    by any user. Use :func:`list_system_jobs` for those.
    """
    return [j for j in list_all_jobs() if user_slug in j.users]


def list_system_jobs() -> list[JobDefinition]:
    """Return system-scope jobs (``users: []``)."""
    return [j for j in list_all_jobs() if not j.users]


def delete_job(job_id: str) -> bool:
    """Remove a job directory and all its data. Returns True if deleted."""
    d = _find_job_dir_by_id(job_id)
    if d is None:
        return False
    shutil.rmtree(d)
    return True


# ---------------------------------------------------------------------------
# Run log
# ---------------------------------------------------------------------------


def _runs_dir(job_dir: Path) -> Path:
    """Return the runs directory for a job, creating it if needed."""
    d = job_dir / 'runs'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_file(job_dir: Path, user_slug: str | None) -> Path:
    """Return the per-user run log path (``_system.jsonl`` when user is None)."""
    name = user_slug if user_slug else SYSTEM_USER
    return _runs_dir(job_dir) / f'{name}.jsonl'


def append_run(job_id: str, user_slug: str | None, run: JobRun) -> None:
    """Append a run record to the job's per-user run log."""
    d = _find_job_dir_by_id(job_id)
    if d is None:
        return
    path = _run_file(d, user_slug)
    with path.open('a', encoding='utf-8') as f:
        f.write(run.model_dump_json() + '\n')


def read_runs(job_id: str, user_slug: str | None, *, limit: int = 20) -> list[JobRun]:
    """Read the most recent ``limit`` runs for a job (newest first)."""
    d = _find_job_dir_by_id(job_id)
    if d is None:
        return []
    path = _run_file(d, user_slug)
    if not path.exists():
        return []
    runs: list[JobRun] = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            runs.append(JobRun.model_validate(json.loads(line)))
        except Exception:
            log.warning('Skipping malformed run record in %s', path)
    return list(reversed(runs[-limit:]))


def last_run(job_id: str, user_slug: str | None) -> JobRun | None:
    """Return the most recent run for a (job, user), or None."""
    runs = read_runs(job_id, user_slug, limit=1)
    return runs[0] if runs else None


def cleanup_old_runs(job_id: str, retention_days: int) -> int:
    """Remove run records older than ``retention_days`` across every user log.

    Rewrites each ``runs/<user>.jsonl`` in place, keeping only runs whose
    ``finished_at`` is within the retention window (or have no ``finished_at``).
    Returns the total number of records removed across all per-user files.
    """
    d = _find_job_dir_by_id(job_id)
    if d is None:
        return 0
    runs_dir = d / 'runs'
    if not runs_dir.is_dir():
        return 0

    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    total_removed = 0

    for path in sorted(runs_dir.glob('*.jsonl')):
        kept: list[str] = []
        removed = 0
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                run = JobRun.model_validate(json.loads(line))
                if run.finished_at and run.finished_at < cutoff:
                    removed += 1
                    continue
            except Exception:
                pass  # keep malformed lines to avoid data loss
            kept.append(line)
        if removed > 0:
            path.write_text('\n'.join(kept) + '\n' if kept else '', encoding='utf-8')
        total_removed += removed

    return total_removed


# ---------------------------------------------------------------------------
# Migration from legacy ``<data_root>/users/<slug>/jobs/<id>/`` layout
# ---------------------------------------------------------------------------


def migrate_legacy_jobs() -> int:
    """Convert legacy per-user job directories to the flat layout.

    Walks ``<data_root>/users/*/jobs/*/job.json`` and for each legacy job:

    1. Rewrites the definition as ``<data_root>/jobs/<slug>/JOB.md`` +
       ``state.json``, setting ``users: [<old_user_slug>]``.
    2. Moves ``runs.jsonl`` to ``<data_root>/jobs/<slug>/runs/<old_slug>.jsonl``.
    3. Removes the legacy ``<data_root>/users/<slug>/jobs/`` directory.

    Idempotent — once the legacy directories are gone the function returns 0
    at zero cost. Returns the number of jobs migrated this run.
    """
    users_dir = data_root() / 'users'
    if not users_dir.is_dir():
        return 0

    migrated = 0
    for user_dir in sorted(users_dir.iterdir()):
        if not user_dir.is_dir():
            continue
        user_slug = user_dir.name
        legacy_dir = user_dir / 'jobs'
        if not legacy_dir.is_dir():
            continue

        for job_dir in sorted(legacy_dir.iterdir()):
            if not job_dir.is_dir():
                continue
            legacy_path = job_dir / 'job.json'
            if not legacy_path.exists():
                continue

            try:
                raw = json.loads(legacy_path.read_text(encoding='utf-8'))
            except Exception:
                log.exception('legacy-jobs: failed to read %s', legacy_path)
                continue

            if 'users' not in raw and 'user_slug' in raw:
                raw['users'] = [raw.pop('user_slug')]
            elif 'user_slug' in raw:
                raw.pop('user_slug')

            if ':' not in raw.get('model', ''):
                raw['model'] = f'anthropic:{raw.get("model", "claude-haiku-4-5-20251001")}'

            try:
                job = JobDefinition.model_validate(raw)
            except Exception:
                log.exception('legacy-jobs: failed to validate %s', legacy_path)
                continue

            save_job(job)

            legacy_runs = job_dir / 'runs.jsonl'
            if legacy_runs.exists():
                target_dir = _find_job_dir_by_id(job.id)
                if target_dir is not None:
                    runs_dir = target_dir / 'runs'
                    runs_dir.mkdir(parents=True, exist_ok=True)
                    (runs_dir / f'{user_slug}.jsonl').write_text(
                        legacy_runs.read_text(encoding='utf-8'),
                        encoding='utf-8',
                    )
            migrated += 1

        shutil.rmtree(legacy_dir, ignore_errors=True)

    if migrated:
        log.info('legacy-jobs: migrated %d jobs to flat layout', migrated)
    return migrated
