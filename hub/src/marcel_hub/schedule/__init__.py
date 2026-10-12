"""Schedules (S-09.1): the scheduler, its cron, and `/api/schedules`. See `scheduler.py`."""

from pathlib import Path

from fastapi import FastAPI

from marcel_hub.schedule.cron import Cron, CronError
from marcel_hub.schedule.routes import router
from marcel_hub.schedule.scheduler import (
    STATE_FILE,
    Frames,
    Pushes,
    ScheduleBuiltin,
    ScheduleNotFound,
    Scheduler,
)
from marcel_hub.tasks.registry import TaskRegistry

__all__ = [
    'Cron',
    'CronError',
    'ScheduleBuiltin',
    'ScheduleNotFound',
    'Scheduler',
    'install',
]


def install(
    app: FastAPI,
    registry: TaskRegistry,
    frames: Frames,
    pusher: Pushes | None,
    data_dir: Path,
) -> Scheduler:
    """Put the scheduler on `app.state.scheduler`, mount `/api/schedules`, and give the brain's
    `schedule_*` tools the real schedules. Call after `brain_api.install`. The app's lifespan
    starts and stops its loop."""
    scheduler = Scheduler(
        app.state.engine,
        registry,
        frames,
        events=app.state.events,
        pusher=pusher,
        state_path=data_dir / STATE_FILE,
    )
    app.state.scheduler = scheduler
    app.include_router(router)
    app.state.brain_ports.schedules = scheduler
    return scheduler
