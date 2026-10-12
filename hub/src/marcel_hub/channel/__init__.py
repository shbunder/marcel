"""The channel gateway (S-04.6): `/channel` for the NUC's sessions, and `/api/approvals`.

`create(app)` makes the gateway (it is also the task registry's Outbox); `install(app, …)` wires
it to the registry, the runner watcher and the app event log, and mounts both routers.
"""

from pathlib import Path

from fastapi import FastAPI

from marcel_hub import triage
from marcel_hub.channel.gateway import SECRET_FILE, Gateway, Peer, Rejection
from marcel_hub.channel.ports import Push
from marcel_hub.channel.routes import router as approvals_router
from marcel_hub.channel.socket import MAX_FRAME
from marcel_hub.channel.socket import router as socket_router
from marcel_hub.runner_client import Alerts, RunnerWatch
from marcel_hub.schedule.cron import zone
from marcel_hub.tasks.registry import TaskRegistry

TRIAGE_LOG = 'triage.log'

__all__ = ['MAX_FRAME', 'SECRET_FILE', 'Gateway', 'Peer', 'Rejection', 'create', 'install']


def create(
    app: FastAPI, data_dir: Path, push: Push | None = None, alerts: Alerts | None = None
) -> Gateway:
    """The gateway, before the registry exists: its Outbox is handed to `tasks.install`."""
    gateway = Gateway(app.state.engine, data_dir / SECRET_FILE, push=push, alerts=alerts)
    triage.keep_log(data_dir / TRIAGE_LOG)  # S-06.5: the wake decisions, for S-16.2
    app.state.channel = gateway
    return gateway


def install(
    app: FastAPI, gateway: Gateway, registry: TaskRegistry, watch: RunnerWatch | None
) -> None:
    gateway.registry = registry
    gateway.artifacts = getattr(app.state, 'artifacts', None)
    gateway.events = app.state.events
    gateway.memory = getattr(app.state, 'memory', None)  # workers' notes (S-04.14)
    gateway.tz = zone(app.state.settings.tz)
    app.state.events.observers.append(gateway.on_app_event)
    if watch is not None:
        watch.listeners.append(gateway)
    app.include_router(socket_router)
    app.include_router(approvals_router)
