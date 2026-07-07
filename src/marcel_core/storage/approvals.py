"""Persistence for command-approval requests — audit trail + pending queue.

Two durable surfaces under ``data_root()/approvals/`` (ADR-260628-ca8f39):

- ``queue/<id>.json`` — a request that expired unanswered, kept so the user
  can approve it later and re-trigger the action as a fresh run.
- ``audit.jsonl`` — an append-only log of every approval decision (who,
  what, outcome, when), so allow-always and every other verdict is auditable.

Records are plain dicts (already serialized by the caller), keeping this
module free of any ``marcel_core.harness`` import.
"""

from __future__ import annotations

import json
import pathlib

from marcel_core.storage._root import data_root


def _approvals_dir() -> pathlib.Path:
    return data_root() / 'approvals'


def _queue_dir() -> pathlib.Path:
    return _approvals_dir() / 'queue'


def _audit_path() -> pathlib.Path:
    return _approvals_dir() / 'audit.jsonl'


def queue_pending(record: dict) -> None:
    """Persist an expired request to the pending queue (keyed by ``record['id']``)."""
    qdir = _queue_dir()
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / f'{record["id"]}.json').write_text(json.dumps(record, indent=2), encoding='utf-8')


def list_queued() -> list[dict]:
    """Return every queued (expired, awaiting later approval) request, newest first."""
    qdir = _queue_dir()
    if not qdir.is_dir():
        return []
    records: list[dict] = []
    for path in qdir.glob('*.json'):
        try:
            records.append(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, json.JSONDecodeError):
            continue
    records.sort(key=lambda r: r.get('created_at', ''), reverse=True)
    return records


def get_queued(approval_id: str) -> dict | None:
    """Return one queued request by id, or ``None``."""
    path = _queue_dir() / f'{approval_id}.json'
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


def remove_queued(approval_id: str) -> None:
    """Drop a queued request (after it is approved/dismissed). Idempotent."""
    (_queue_dir() / f'{approval_id}.json').unlink(missing_ok=True)


def append_audit(record: dict) -> None:
    """Append one approval decision to the append-only audit log."""
    adir = _approvals_dir()
    adir.mkdir(parents=True, exist_ok=True)
    with _audit_path().open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(record) + '\n')


def read_audit() -> list[dict]:
    """Return the audit log entries in order (empty if none)."""
    path = _audit_path()
    if not path.is_file():
        return []
    entries: list[dict] = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries
