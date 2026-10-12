"""Projects (B-30…B-33a): the store, `/api/projects` and moving a task between projects."""

from fastapi import FastAPI

from marcel_hub.memory import MemoryRepo
from marcel_hub.projects.routes import router
from marcel_hub.projects.store import ProjectEcho, ProjectStore, notes_path, slugify
from marcel_hub.tasks.ports import Publisher
from marcel_hub.tasks.registry import TaskRegistry

__all__ = ['ProjectEcho', 'ProjectStore', 'install', 'notes_path', 'slugify']


def install(
    app: FastAPI,
    publisher: Publisher | None = None,
    tasks: TaskRegistry | None = None,
    memory: MemoryRepo | None = None,
) -> ProjectStore:
    """Put the store on `app.state.projects` and mount its routes. With a task registry, workers
    start with their project's briefing, and task changes send `project.updated`."""
    store = ProjectStore(app.state.engine, memory=memory, publisher=publisher, tasks=tasks)
    if tasks is not None:
        tasks.briefing = store.briefing
        tasks.publisher = ProjectEcho(tasks.publisher, store)
    app.state.projects = store
    app.include_router(router)
    return store
