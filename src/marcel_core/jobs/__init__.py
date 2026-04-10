"""Job system — CRUD operations and file-based storage.

Jobs are stored per-user at ``<data_root>/users/<slug>/jobs/<job_id>/``:
- ``job.json`` — serialized :class:`JobDefinition`
- ``runs.jsonl`` — append-only log of :class:`JobRun` entries
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from marcel_core.jobs.models import JobDefinition, JobRun
from marcel_core.storage._root import data_root

log = logging.getLogger(__name__)


def _jobs_dir(user_slug: str) -> Path:
    """Return the root jobs directory for a user, creating it if needed."""
    d = data_root() / 'users' / user_slug / 'jobs'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _job_dir(user_slug: str, job_id: str) -> Path:
    """Return the directory for a specific job."""
    return _jobs_dir(user_slug) / job_id


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def save_job(job: JobDefinition) -> None:
    """Write a job definition to disk."""
    d = _job_dir(job.user_slug, job.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / 'job.json').write_text(job.model_dump_json(indent=2), encoding='utf-8')


def load_job(user_slug: str, job_id: str) -> JobDefinition | None:
    """Load a single job definition, or *None* if it doesn't exist."""
    path = _job_dir(user_slug, job_id) / 'job.json'
    if not path.exists():
        return None
    try:
        return JobDefinition.model_validate_json(path.read_text(encoding='utf-8'))
    except Exception:
        log.exception('Failed to load job %s for user %s', job_id, user_slug)
        return None


def list_jobs(user_slug: str) -> list[JobDefinition]:
    """Return all job definitions for a user."""
    jobs_root = _jobs_dir(user_slug)
    result: list[JobDefinition] = []
    for child in sorted(jobs_root.iterdir()):
        if child.is_dir():
            job = load_job(user_slug, child.name)
            if job is not None:
                result.append(job)
    return result


def list_all_jobs() -> list[JobDefinition]:
    """Return all job definitions across all users."""
    users_dir = data_root() / 'users'
    if not users_dir.is_dir():
        return []
    result: list[JobDefinition] = []
    for user_dir in sorted(users_dir.iterdir()):
        if user_dir.is_dir():
            result.extend(list_jobs(user_dir.name))
    return result


def delete_job(user_slug: str, job_id: str) -> bool:
    """Remove a job directory and all its data.  Returns True if deleted."""
    d = _job_dir(user_slug, job_id)
    if not d.exists():
        return False
    import shutil

    shutil.rmtree(d)
    return True


# ---------------------------------------------------------------------------
# Run log
# ---------------------------------------------------------------------------


def append_run(user_slug: str, job_id: str, run: JobRun) -> None:
    """Append a run record to the job's runs.jsonl file."""
    d = _job_dir(user_slug, job_id)
    d.mkdir(parents=True, exist_ok=True)
    with (d / 'runs.jsonl').open('a', encoding='utf-8') as f:
        f.write(run.model_dump_json() + '\n')


def read_runs(user_slug: str, job_id: str, *, limit: int = 20) -> list[JobRun]:
    """Read the most recent *limit* runs for a job (newest first)."""
    path = _job_dir(user_slug, job_id) / 'runs.jsonl'
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
    # Return newest first, limited
    return list(reversed(runs[-limit:]))


def last_run(user_slug: str, job_id: str) -> JobRun | None:
    """Return the most recent run for a job, or None."""
    runs = read_runs(user_slug, job_id, limit=1)
    return runs[0] if runs else None
