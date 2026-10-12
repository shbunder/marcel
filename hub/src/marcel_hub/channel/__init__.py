"""The channel gateway (S-04.6): `/channel` for the NUC's sessions, and `/api/approvals`.

`create(app)` makes the gateway (it is also the task registry's Outbox); `install(app, …)` wires
it to the registry, the runner watcher and the app event log, and mounts both routers.
"""

from pathlib import Path

from fastapi import FastAPI

from marcel_hub.channel.gateway import SECRET_FILE, Gateway, Peer, Rejection
from marcel_hub.channel.routes import router as approvals_router
from marcel_hub.channel.socket import router as socket_router
from marcel_hub.runner_client import RunnerWatch
from marcel_hub.tasks.registry import TaskRegistry

__all__ = ['SECRET_FILE', 'Gateway', 'Peer', 'Rejection', 'create', 'install']


def create(app: FastAPI, data_dir: Path) -> Gateway:
    """The gateway, before the registry exists: its Outbox is handed to `tasks.install`."""
    gateway = Gateway(app.state.engine, data_dir / SECRET_FILE)
    app.state.channel = gateway
    return gateway


def install(
    app: FastAPI, gateway: Gateway, registry: TaskRegistry, watch: RunnerWatch | None
) -> None:
    gateway.registry = registry
    gateway.events = app.state.events
    app.state.events.observers.append(gateway.on_app_event)
    if watch is not None:
        watch.listeners.append(gateway)
    app.include_router(socket_router)
    app.include_router(approvals_router)
