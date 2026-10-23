"""`POST /pair` (public), and the token-protected `/me`, `/devices…` and `/pairing-codes` routes.

One install is one person (ADR-013), so every paired phone sees and changes every phone: no route
here compares the caller with the phone it names. An unknown id answers 404.
"""

import re
from datetime import UTC, datetime
from typing import Any, Literal, Self

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel import Session, col, select

from marcel_hub.auth.deps import DbSession, current_device, require_device
from marcel_hub.auth.errors import ApiError
from marcel_hub.auth.tokens import (
    PAIRING_CODE_TTL,
    PairGuessLimit,
    create_pairing_code,
    first_buddy,
    hub_api,
    live_buddy,
    pairing_url,
    redeem_pairing_code,
)
from marcel_hub.models import Agent, Device, User, utcnow

pair_router = APIRouter(prefix='/api', tags=['access'])
router = APIRouter(prefix='/api', tags=['access'], dependencies=[Depends(require_device)])


class PairRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(min_length=6, max_length=12)
    device_name: str = Field(min_length=1, max_length=80)


# An APNs device token as iOS hands it over, hex-encoded (32 bytes today; Apple allows longer).
APNS_TOKEN = re.compile(r'(?:[0-9a-fA-F]{2}){32,100}')


class PushTarget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['apns', 'ntfy']
    apns_token: str | None = Field(default=None, max_length=200)
    ntfy_topic: str | None = Field(default=None, max_length=200)

    @model_validator(mode='after')
    def _token_for_kind(self) -> Self:
        if self.kind == 'apns' and not self.apns_token:
            raise ValueError('`apns_token` is needed when kind is apns')
        if self.kind == 'apns' and not APNS_TOKEN.fullmatch(self.apns_token or ''):
            raise ValueError(
                '`apns_token` must be the device token from iOS as hex: 64 to 200 characters, '
                '0-9 and a-f'
            )
        if self.kind == 'ntfy' and not self.ntfy_topic:
            raise ValueError('`ntfy_topic` is needed when kind is ntfy')
        return self


def buddy_of(session: Session, device: Device) -> str | None:
    """The buddy the phone shows (`Device.agent_id`, always a live one): its own while that one
    is live, else the first buddy (`first_buddy`); None only while there is no buddy at all."""
    if device.agent_id is not None:
        agent = session.get(Agent, device.agent_id)
        if agent is not None and agent.archived_at is None:
            return agent.id
    return first_buddy(session)


class DeviceUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    agent_id: str = Field(min_length=1)


class NewPairingCode(BaseModel):
    model_config = ConfigDict(extra='forbid')
    agent_id: str | None = Field(default=None, min_length=1)


def device_json(session: Session, device: Device, current: Device | None) -> dict[str, Any]:
    """The contract's `Device`. `current` is the calling phone; None in an event, which every
    phone reads, so it carries no `current`."""
    push = 'apns' if device.apns_token else 'ntfy' if device.ntfy_topic else 'none'
    body: dict[str, Any] = {
        'id': device.id,
        'name': device.name,
        'created_at': device.created_at.isoformat(),
        'push': push,
    }
    if current is not None:
        body['current'] = device.id == current.id
    if (agent_id := buddy_of(session, device)) is not None:
        body['agent_id'] = agent_id
    if device.last_seen is not None:
        body['last_seen_at'] = device.last_seen.isoformat()
    return body


def agent_json(agent: Agent, state: dict[str, Any]) -> dict[str, Any]:
    """The contract's `Agent`; `state` is its live `AgentState` (`agent_state.AgentStates`)."""
    body: dict[str, Any] = {
        'id': agent.id,
        'name': agent.name,
        'animal': agent.animal,
        'palette': agent.palette,
        'seed': agent.seed,
        'main_conversation_id': agent.main_conversation_id,
        'state': state,
        'model_brain': agent.model_brain,
        'model_worker': agent.model_worker,
    }
    if agent.look_animal and agent.look_color and agent.look_personality:  # 0.7 (S-17.3)
        body['look'] = {
            'animal': agent.look_animal,
            'color': agent.look_color,
            'personality': agent.look_personality,
        }
    if agent.onboarded_at is not None:
        body['onboarded_at'] = _iso(agent.onboarded_at)
    if agent.archived_at is not None:  # 0.8.0 (S-22.27)
        body['archived_at'] = _iso(agent.archived_at)
    return body


def _iso(at: datetime) -> str:
    return (at if at.tzinfo else at.replace(tzinfo=UTC)).isoformat()


def find_device(session: Session, device_id: str) -> Device:
    """The paired phone `device_id`, or 404 `device_not_found`."""
    device = session.get(Device, device_id)
    if device is None:
        raise ApiError(404, 'device_not_found', 'That device is not paired with this hub.')
    return device


def find_live_buddy(session: Session, agent_id: str) -> Agent:
    """The live buddy `agent_id`, or 404 `agent_not_found` (also for an archived one)."""
    agent = live_buddy(session, agent_id)
    if agent is None:
        raise ApiError(404, 'agent_not_found', 'That buddy is not on this hub, or it was archived.')
    return agent


def show_buddy(request: Request, session: Session, device: Device, agent_id: str) -> bool:
    """Set the buddy `device` shows; sends `device.updated` when it changed (committed with the
    change). False when it already showed it."""
    if device.agent_id == agent_id:
        return False
    device.agent_id = agent_id
    session.add(device)
    request.app.state.events.publish(
        session, device.user_id, 'device.updated', device_json(session, device, None)
    )
    session.refresh(device)
    return True


@pair_router.post('/pair', status_code=201)
def pair(body: PairRequest, request: Request, session: DbSession) -> dict[str, str]:
    limit: PairGuessLimit = request.app.state.pair_guess_limit
    limit.check()
    try:
        device, token = redeem_pairing_code(session, body.code, body.device_name)
    except ApiError as exc:
        if exc.code == 'pairing_code_invalid':
            limit.failed()
        raise
    return {'device_id': device.id, 'user_id': device.user_id, 'token': token}


@router.get('/me')
def me(request: Request, session: DbSession) -> dict[str, Any]:
    device = current_device(request)
    agents = session.exec(
        select(Agent)
        .where(col(Agent.archived_at).is_(None), col(Agent.main_conversation_id).is_not(None))
        .order_by(col(Agent.id))
    ).all()
    person = session.get(User, device.user_id)
    fallback = request.app.state.settings.owner_name  # the owner's name before their row exists
    return {
        'user_id': device.user_id,
        'name': person.name if person is not None else fallback,
        'device_id': device.id,
        'agents': [agent_json(a, request.app.state.agent_states.current(a.id)) for a in agents],
        'push': request.app.state.pusher.capabilities(),  # notify/ (S-08.1)
    }


@router.get('/devices')
def list_devices(request: Request, session: DbSession) -> list[dict]:
    me = current_device(request)
    devices = session.exec(
        select(Device).order_by(col(Device.created_at).desc(), col(Device.id).desc())
    ).all()
    return [device_json(session, d, me) for d in devices]


@router.delete('/devices/{device_id}', status_code=204)
def revoke_device(device_id: str, request: Request, session: DbSession) -> Response:
    device = find_device(session, device_id)
    session.delete(device)
    session.commit()
    return Response(status_code=204)


@router.put('/devices/{device_id}/push')
def set_device_push(
    device_id: str, body: PushTarget, request: Request, session: DbSession
) -> dict[str, Any]:
    me = current_device(request)
    device = find_device(session, device_id)
    device.apns_token = body.apns_token if body.kind == 'apns' else None
    device.ntfy_topic = body.ntfy_topic if body.kind == 'ntfy' else None
    session.add(device)
    session.commit()
    session.refresh(device)
    return device_json(session, device, me)


@router.patch('/devices/{device_id}')
def update_device(
    device_id: str, body: DeviceUpdate, request: Request, session: DbSession
) -> dict[str, Any]:
    """Choose the buddy a phone shows (B-49); any phone may change any phone's, its own too."""
    device = find_device(session, device_id)
    show_buddy(request, session, device, find_live_buddy(session, body.agent_id).id)
    return device_json(session, device, current_device(request))


@router.post('/pairing-codes', status_code=201)
def new_pairing_code(
    request: Request, session: DbSession, body: NewPairingCode | None = None
) -> dict[str, Any]:
    """A code that pairs another phone, as `marcel-hub pair` prints (B-49). The link's hub is
    the hub's public address; without one, the address this phone reached the hub at."""
    agent_id = body.agent_id if body is not None else None
    if agent_id is not None:
        find_live_buddy(session, agent_id)
    public_url: str | None = request.app.state.settings.public_url
    hub = hub_api(public_url) if public_url else str(request.base_url).rstrip('/') + '/api'
    now = utcnow()
    code = create_pairing_code(session, agent_id=agent_id, now=now)
    return {
        'code': code,
        'link': pairing_url(hub, code, agent_id),
        'expires_at': _iso(now + PAIRING_CODE_TTL),
    }
