"""`GET /api/agents/{agent_id}/avatar` and `GET /api/avatar-previews` (app-api.yaml 0.7, S-19.2).

- **The sheet**: the caller's own agent only (another person's is 404 `agent_not_found`). Two
  bodies at one URL, picked by `Accept`: the sheet's JSON by default, its PNG for `image/png`.
  Both carry the same `ETag`, which changes with the look and is `avatar_etag` in `agent.updated`;
  `If-None-Match` with it answers 304.
- **A preview**: any look, for the picker: one 32 x 32 PNG in `mood` (default the personality's
  resting face). The same query always gives the same image, so the phone may keep it.

The query is checked against the contract's lists (the hub stays strict on what it accepts, even
once clients learn to fall back on a look they do not know).
"""

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response

from marcel_hub.agents.routes import LookAnimal, LookColor, LookPersonality, owned_agent
from marcel_hub.auth.deps import DbSession, require_device
from marcel_hub.avatar.store import AvatarStore, etag, look_of

Mood = Literal[
    'neutral', 'happy', 'laughing', 'love', 'excited', 'wink', 'proud', 'calm', 'thinking', 'idea',
    'focused', 'working', 'surprised', 'confused', 'worried', 'sad', 'crying', 'angry', 'grumpy',
    'sleepy', 'dizzy', 'embarrassed', 'singing', 'shocked', 'kiss',
]  # fmt: skip
PNG = 'image/png'
# The sheet may change with the look: the phone keeps it but asks again with If-None-Match.
REVALIDATE = 'private, no-cache'
# A preview for a query never changes (until a new zoo release): keep it a day.
KEEP = 'private, max-age=86400'

router = APIRouter(prefix='/api', tags=['agents'], dependencies=[Depends(require_device)])


def install(app: FastAPI, data_dir: Path) -> AvatarStore:
    store = AvatarStore(data_dir / 'avatars')
    app.state.avatars = store
    app.include_router(router)
    return store


def _matches(if_none_match: str, tag: str) -> bool:
    """`If-None-Match` names this ETag: `*`, the tag itself, or its weak form `W/"…"`."""
    given = [t.strip() for t in if_none_match.split(',') if t.strip()]
    return '*' in given or tag in [t.removeprefix('W/') for t in given]


def _wants_png(accept: str) -> bool:
    """`image/png` asked for ahead of JSON (JSON is the default)."""
    kinds = [part.split(';')[0].strip().lower() for part in accept.split(',')]
    if PNG not in kinds:
        return False
    return 'application/json' not in kinds or kinds.index(PNG) < kinds.index('application/json')


@router.get('/agents/{agent_id}/avatar', response_model=None)
def agent_avatar(agent_id: str, request: Request, session: DbSession) -> Response | dict[str, Any]:
    look = look_of(owned_agent(session, request, agent_id))
    tag = etag(look)
    headers = {'ETag': tag, 'Cache-Control': REVALIDATE, 'Vary': 'Accept'}
    if _matches(request.headers.get('if-none-match', ''), tag):
        return Response(status_code=304, headers=headers)
    store: AvatarStore = request.app.state.avatars
    image, meta = store.sheet(look)
    if _wants_png(request.headers.get('accept', '')):
        return Response(image, media_type=PNG, headers=headers)
    return Response(json.dumps(meta), media_type='application/json', headers=headers)


@router.get('/avatar-previews')
def avatar_preview(
    request: Request,
    animal: Annotated[LookAnimal, Query()],
    color: Annotated[LookColor, Query()],
    personality: Annotated[LookPersonality, Query()],
    mood: Annotated[Mood | None, Query()] = None,
) -> Response:
    store: AvatarStore = request.app.state.avatars
    look = {'species': animal, 'color': color, 'personality': personality}
    return Response(store.preview(look, mood), media_type=PNG, headers={'Cache-Control': KEEP})
