"""REST endpoint for the A2UI component catalog.

Clients fetch the full catalog at startup to know which components are
available and their prop schemas.  The catalog is not user-specific —
it reflects all skills loaded at the system level.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from marcel_core.auth import verify_api_token, verify_telegram_init_data
from marcel_core.channels.telegram.sessions import get_user_slug as get_telegram_user_slug
from marcel_core.skills.component_registry import build_registry

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth helper (same pattern as artifacts.py)
# ---------------------------------------------------------------------------


def _authenticate(init_data: str, authorization: str) -> str:
    """Return the authenticated user_slug, or raise 401."""
    if init_data:
        tg_user = verify_telegram_init_data(init_data)
        if tg_user is None:
            raise HTTPException(status_code=401, detail='Invalid Telegram credentials')
        user_slug = get_telegram_user_slug(tg_user['id'])
        if user_slug is None:
            raise HTTPException(status_code=401, detail='Telegram user not linked')
        return user_slug

    token = authorization.removeprefix('Bearer ').strip()
    if not verify_api_token(token):
        raise HTTPException(status_code=401, detail='Unauthorized')
    raise HTTPException(status_code=400, detail='initData required for this endpoint')


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ComponentSchemaResponse(BaseModel):
    name: str
    description: str
    skill: str
    props: dict[str, Any]


class ComponentCatalogResponse(BaseModel):
    components: list[ComponentSchemaResponse]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get('/api/components', response_model=ComponentCatalogResponse)
async def list_components(
    initData: str = Query(''),
    authorization: str = Header(''),
) -> ComponentCatalogResponse:
    """Return the full A2UI component catalog."""
    user_slug = _authenticate(initData, authorization)
    registry = build_registry(user_slug)
    return ComponentCatalogResponse(
        components=[
            ComponentSchemaResponse(
                name=c.name,
                description=c.description,
                skill=c.skill,
                props=c.props,
            )
            for c in registry.list_all()
        ]
    )


@router.get('/api/components/{name}', response_model=ComponentSchemaResponse)
async def get_component(
    name: str,
    initData: str = Query(''),
    authorization: str = Header(''),
) -> ComponentSchemaResponse:
    """Return a single component schema by name."""
    user_slug = _authenticate(initData, authorization)
    registry = build_registry(user_slug)
    component = registry.get(name)
    if component is None:
        raise HTTPException(status_code=404, detail=f'Component {name!r} not found')
    return ComponentSchemaResponse(
        name=component.name,
        description=component.description,
        skill=component.skill,
        props=component.props,
    )
