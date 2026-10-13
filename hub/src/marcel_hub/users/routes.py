"""`/users…`: the owner's People screen (B-37, B-40). Every operation here is `x-owner-only`."""

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from marcel_hub.auth.deps import DbSession, require_owner
from marcel_hub.auth.errors import ApiError
from marcel_hub.auth.tokens import (
    DEFAULT_HUB,
    PAIRING_CODE_TTL,
    create_pairing_code,
    pairing_url,
)
from marcel_hub.models import utcnow
from marcel_hub.users import store

log = logging.getLogger('marcel_hub')

router = APIRouter(prefix='/api', tags=['people'], dependencies=[Depends(require_owner)])

Animal = Literal[
    'giraffe', 'crocodile', 'frog', 'cat', 'fox', 'bear', 'panda', 'rabbit', 'dog', 'pig', 'mouse'
]
Color = Literal[
    'natural',
    'rose',
    'coral',
    'tangerine',
    'honey',
    'lime',
    'green',
    'teal',
    'sky',
    'blue',
    'lavender',
    'plum',
    'cocoa',
    'sand',
    'slate',
    'snow',
    'ink',
]
Personality = Literal[
    'professional', 'cheerful', 'curious', 'cozy', 'bold', 'cool', 'sporty', 'shy', 'grumpy'
]


class Look(BaseModel):
    model_config = ConfigDict(extra='forbid')
    animal: Animal
    color: Color
    personality: Personality


class NewAgent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=40)
    look: Look | None = None


class NewUser(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=40)
    agent: NewAgent


@router.get('/users')
def list_users(session: DbSession) -> list[dict[str, Any]]:
    return [store.user_json(session, u) for u in store.all_users(session)]


@router.post('/users', status_code=201)
def add_user(body: NewUser, session: DbSession) -> dict[str, Any]:
    name, agent_name = body.name.strip(), body.agent.name.strip()
    if not name or not agent_name:
        field = 'name' if not name else 'agent.name'
        raise ApiError(400, 'bad_request', f'Check `{field}`: it needs at least one letter.')
    look = body.agent.look.model_dump() if body.agent.look else None
    return store.user_json(session, store.add_member(session, name, agent_name, look))


@router.delete('/users/{user_id}', status_code=204)
async def remove_user(user_id: str, request: Request, session: DbSession) -> Response:
    """Phones out first, so the person is signed out even when their quests cannot be stopped
    (the runner is down): those quests are logged and left to the watchdogs."""
    user = store.get_user(session, user_id)
    store.refuse_owner(user)
    store.sign_out(session, user)
    by = store.owner_name(session, request.app.state.settings.owner_name)
    for task_id in store.unfinished_tasks(session, user.id):
        try:
            await request.app.state.tasks.stop(task_id, by=by)
        except Exception:
            log.warning('Could not stop task %s of a removed person', task_id, exc_info=True)
    session.expire_all()
    store.forget(session, store.get_user(session, user_id))
    return Response(status_code=204)


@router.post('/users/{user_id}/pairing-codes', status_code=201)
def new_pairing_code(user_id: str, session: DbSession) -> dict[str, str]:
    user = store.get_user(session, user_id)
    now = utcnow()
    code = create_pairing_code(session, user_id=user.id, now=now)
    return {
        'code': code,
        'link': pairing_url(DEFAULT_HUB, code),
        'expires_at': (now + PAIRING_CODE_TTL).isoformat(),
    }
