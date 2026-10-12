"""Tasks: the registry, the state machine from `contracts/task-states.md`, and `/api/tasks`."""

from fastapi import FastAPI

from marcel_hub.tasks.ports import MemoryOutbox, NoRunner, Outbox, Publisher, Runner
from marcel_hub.tasks.registry import TaskRegistry
from marcel_hub.tasks.routes import router
from marcel_hub.tasks.states import IllegalTransition

__all__ = ['IllegalTransition', 'TaskRegistry', 'install']


def install(
    app: FastAPI,
    runner: Runner | None = None,
    outbox: Outbox | None = None,
    publisher: Publisher | None = None,
) -> TaskRegistry:
    """Put a registry on `app.state.tasks` and mount the task routes.

    Without a runner, starting and stopping sessions answers that the runner is out of reach.
    Without an outbox, steer and hand-back frames wait in memory until the channel gateway exists.
    """
    registry = TaskRegistry(
        app.state.engine, runner or NoRunner(), outbox or MemoryOutbox(), publisher
    )
    app.state.tasks = registry
    app.include_router(router)
    return registry
