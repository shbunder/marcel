"""Regression tests for the kernel lifespan startup order (ISSUE-efbaaa)
and the first-boot zoo-summary log line (ISSUE-792e8e).

The scheduler's ``rebuild_schedule()`` → ``_ensure_habitat_jobs()`` reads the
integration ``_metadata`` dict to decide which ``habitat:*`` jobs to materialize
and which to treat as orphans. If ``discover()`` has not populated ``_metadata``
by the time the scheduler starts, every habitat-scheduled job gets orphan-deleted
on cold start — the exact failure observed in prod before this fix.
"""

from __future__ import annotations

import logging
import pathlib
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_lifespan_runs_discover_before_scheduler_start(tmp_path, monkeypatch):
    """`discover()` must populate `_metadata` before `scheduler.start()` fires."""
    # Keep startup side effects contained to a tmp dir.
    monkeypatch.setenv('MARCEL_DATA_DIR', str(tmp_path / 'data'))
    (tmp_path / 'data').mkdir()

    call_order: list[str] = []

    with (
        patch('marcel_core.toolkit.discover') as discover,
        patch('marcel_core.main.scheduler') as scheduler,
        patch('marcel_core.main._background_summarization_loop'),
    ):
        discover.side_effect = lambda *_a, **_kw: call_order.append('discover')
        scheduler.start.side_effect = lambda *_a, **_kw: call_order.append('scheduler.start')
        scheduler.stop.side_effect = lambda *_a, **_kw: None

        from marcel_core.main import app, lifespan

        async with lifespan(app):
            pass

    assert call_order.index('discover') < call_order.index('scheduler.start'), (
        f'discover() must run before scheduler.start() so _metadata is populated '
        f'when rebuild_schedule() → _ensure_habitat_jobs() runs. Actual order: {call_order}'
    )


# ---------------------------------------------------------------------------
# ISSUE-792e8e: zoo-summary log line
# ---------------------------------------------------------------------------


def _seed_zoo(root: pathlib.Path, populated: dict[str, int]) -> pathlib.Path:
    """Create ``root/{channels,integrations,skills,jobs,agents}/`` with N
    non-hidden habitat subdirs each, per *populated*. Missing keys get an
    empty directory. Absent keys are not created.
    """
    root.mkdir(parents=True, exist_ok=True)
    for kind, count in populated.items():
        kind_dir = root / kind
        kind_dir.mkdir(exist_ok=True)
        for i in range(count):
            (kind_dir / f'hab{i}').mkdir()
    return root


def test_log_zoo_summary_missing_env(monkeypatch, caplog):
    """Unset MARCEL_ZOO_DIR logs a WARNING pointing at the fix."""
    monkeypatch.delenv('MARCEL_ZOO_DIR', raising=False)
    from marcel_core import main as main_module

    with caplog.at_level(logging.WARNING, logger='marcel_core.main'):
        # settings is a module-level singleton; rebuild for this test so the
        # env-var change is reflected.
        monkeypatch.setattr(main_module.settings, 'marcel_zoo_dir', None)
        main_module._log_zoo_summary()

    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any('MARCEL_ZOO_DIR is unset' in m for m in msgs), msgs
    assert any('make zoo-setup' in m for m in msgs), msgs


def test_log_zoo_summary_nonexistent_dir(monkeypatch, caplog, tmp_path):
    """Pointing at a nonexistent dir logs a WARNING with the bad path."""
    missing = tmp_path / 'does-not-exist'
    from marcel_core import main as main_module

    monkeypatch.setattr(main_module.settings, 'marcel_zoo_dir', str(missing))
    with caplog.at_level(logging.WARNING, logger='marcel_core.main'):
        main_module._log_zoo_summary()

    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(str(missing) in m and 'does not exist' in m for m in msgs), msgs


def test_log_zoo_summary_empty_zoo(monkeypatch, caplog, tmp_path):
    """Zoo dir exists but has zero habitats → WARNING with all-zero counts."""
    zoo = _seed_zoo(tmp_path / 'zoo', {})
    from marcel_core import main as main_module

    monkeypatch.setattr(main_module.settings, 'marcel_zoo_dir', str(zoo))
    with caplog.at_level(logging.WARNING, logger='marcel_core.main'):
        main_module._log_zoo_summary()

    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any('zoo at' in m and 'is empty' in m for m in msgs), msgs


def test_log_zoo_summary_populated_zoo(monkeypatch, caplog, tmp_path):
    """Populated zoo logs one INFO line with per-kind counts, no WARNING."""
    zoo = _seed_zoo(
        tmp_path / 'zoo',
        {'channels': 1, 'integrations': 3, 'skills': 7, 'jobs': 4, 'agents': 2},
    )
    # Also drop a hidden + underscore entry to confirm they're excluded.
    (zoo / 'skills' / '.hidden').mkdir()
    (zoo / 'skills' / '_private').mkdir()

    from marcel_core import main as main_module

    monkeypatch.setattr(main_module.settings, 'marcel_zoo_dir', str(zoo))
    with caplog.at_level(logging.INFO, logger='marcel_core.main'):
        main_module._log_zoo_summary()

    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any(
        f'zoo at {zoo}' in m and 'channels=1' in m and 'integrations=3' in m and 'skills=7' in m for m in infos
    ), infos
    # Hidden + underscore subdirs must not be counted.
    assert all('skills=9' not in m for m in infos), 'hidden/underscore dirs were not excluded'
    # No WARNING when at least one habitat exists.
    warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert not any('zoo at' in m for m in warns), warns
