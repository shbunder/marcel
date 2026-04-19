"""Regression tests for the kernel lifespan startup order (ISSUE-efbaaa).

The scheduler's ``rebuild_schedule()`` → ``_ensure_habitat_jobs()`` reads the
integration ``_metadata`` dict to decide which ``habitat:*`` jobs to materialize
and which to treat as orphans. If ``discover()`` has not populated ``_metadata``
by the time the scheduler starts, every habitat-scheduled job gets orphan-deleted
on cold start — the exact failure observed in prod before this fix.
"""

from __future__ import annotations

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
        patch('marcel_core.defaults.seed_defaults') as seed,
        patch('marcel_core.skills.integrations.discover') as discover,
        patch('marcel_core.main.scheduler') as scheduler,
        patch('marcel_core.main._background_summarization_loop'),
    ):
        seed.side_effect = lambda *_a, **_kw: call_order.append('seed_defaults')
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
