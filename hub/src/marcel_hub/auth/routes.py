"""`POST /pair` (public), and the token-protected `/me` and `/devices…` routes."""

import re
from typing import Any, Literal, Self

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel import Session, col, select

from marcel_hub.auth.deps import DbSession, current_device, require_device
from marcel_hub.auth.errors import ApiError
from marcel_hub.auth.tokens import PairGuessLimit, redeem_pairing_code
from marcel_hub.models import Agent, Device

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


def device_json(device: Device, current: Device) -> dict[str, Any]:
    push = 'apns' if device.apns_token else 'ntfy' if device.ntfy_topic else 'none'
    body: dict[str, Any] = {
        'id': device.id,
        'name': device.name,
        'created_at': device.created_at.isoformat(),
        'push': push,
        'current': device.id == current.id,
    }
    if device.last_seen is not None:
        body['last_seen_at'] = device.last_seen.isoformat()
    return body


def agent_json(agent: Agent, state: dict[str, Any]) -> dict[str, Any]:
    """The contract's `Agent`; `state` is its live `AgentState` (`agent_state.AgentStates`)."""
    return {
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


def own_device(session: Session, device_id: str, me: Device) -> Device:
    device = session.get(Device, device_id)
    if device is None or device.user_id != me.user_id:
        raise ApiError(404, 'device_not_found', 'That device is not paired with this hub.')
    return device


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
        .where(Agent.owner_user_id == device.user_id, col(Agent.main_conversation_id).is_not(None))
        .order_by(col(Agent.id))
    ).all()
    return {
        'user_id': device.user_id,
        'name': request.app.state.settings.owner_name,
        'device_id': device.id,
        'agents': [agent_json(a, request.app.state.agent_states.current(a.id)) for a in agents],
        'push': request.app.state.pusher.capabilities(),  # notify/ (S-08.1)
    }


@router.get('/devices')
def list_devices(request: Request, session: DbSession) -> list[dict]:
    me = current_device(request)
    devices = session.exec(
        select(Device)
        .where(Device.user_id == me.user_id)
        .order_by(col(Device.created_at).desc(), col(Device.id).desc())
    ).all()
    return [device_json(d, me) for d in devices]


@router.delete('/devices/{device_id}', status_code=204)
def revoke_device(device_id: str, request: Request, session: DbSession) -> Response:
    device = own_device(session, device_id, current_device(request))
    session.delete(device)
    session.commit()
    return Response(status_code=204)


@router.put('/devices/{device_id}/push')
def set_device_push(
    device_id: str, body: PushTarget, request: Request, session: DbSession
) -> dict[str, Any]:
    me = current_device(request)
    device = own_device(session, device_id, me)
    device.apns_token = body.apns_token if body.kind == 'apns' else None
    device.ntfy_topic = body.ntfy_topic if body.kind == 'ntfy' else None
    session.add(device)
    session.commit()
    session.refresh(device)
    return device_json(device, me)
