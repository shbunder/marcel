"""People (B-37…B-40): the owner, who runs Marcel at home, and the family members they add.

`install(app)` mounts the owner-only `/users…` routes; `ensure_owner` makes the owner's row at
start-up; `marcel-hub setup` (`users.cli`) makes the owner and their agent on a fresh NUC.
"""

from fastapi import FastAPI

from marcel_hub.users.routes import router
from marcel_hub.users.store import ensure_owner, ensure_owner_agent, give_missing_looks

__all__ = ['ensure_owner', 'ensure_owner_agent', 'give_missing_looks', 'install']


def install(app: FastAPI) -> None:
    app.include_router(router)
