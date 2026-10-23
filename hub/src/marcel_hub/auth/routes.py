"""`POST /pair` (public), and the token-protected `/me`, `/devices…` and `/pairing-codes` routes.

One install is one person (ADR-013), so every paired phone sees and changes every phone: no route
here compares the caller with the phone it names. An unknown id answers 404.
"""

import base64
import re
from datetime import UTC, datetime
from typing import Any, Literal, Self

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel import Session, col, select

from marcel_hub.auth.deps import DbSession, current_device, require_device
from marcel_hub.auth.errors import ApiError
from marcel_hub.auth.tokens import (
    NO_PUBLIC_URL,
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
# A push relay target (app-api 0.8.0 `PushTarget`, S-22.15), with the contract's patterns. The
# relay's address must be https with no user, query or fragment: the send secret goes there.
RELAY_URL = r'^https://[^/?#@\s]+(/[^?#\s]*)?$'
RELAY_HANDLE = r'^[A-Za-z0-9_-]{22}$'
RELAY_SECRET = r'^[A-Za-z0-9_-]{43}$'
DEVICE_KEY = r'^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$'  # 32 bytes, base64url without padding
RELAY_FIELDS = ('relay_url', 'handle', 'send_secret', 'device_key')


class PushTarget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['apns', 'ntfy', 'relay']
    apns_token: str | None = Field(default=None, max_length=200)
    ntfy_topic: str | None = Field(default=None, max_length=200)
    relay_url: str | None = Field(default=None, max_length=500, pattern=RELAY_URL)
    handle: str | None = Field(default=None, pattern=RELAY_HANDLE)
    send_secret: str | None = Field(default=None, pattern=RELAY_SECRET, repr=False)
    device_key: str | None = Field(default=None, pattern=DEVICE_KEY)

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
        if self.kind == 'relay':
            missing = [f'`{name}`' for name in RELAY_FIELDS if not getattr(self, name)]
            if missing:
                raise ValueError(f'{", ".join(missing)} needed when kind is relay')
            if not usable_device_key(self.device_key or ''):
                raise ValueError('`device_key` is not a public key a push can be sealed for')
        return self


def usable_device_key(device_key: str) -> bool:
    """True when the phone's X25519 key can be sealed for: a low-order key (all zeros, say) gives
    no shared secret, so every push to it would fail."""
    raw = base64.urlsafe_b64decode(device_key + '=' * (-len(device_key) % 4))
    try:
        X25519PrivateKey.generate().exchange(X25519PublicKey.from_public_bytes(raw))
    except ValueError:
        return False
    return True


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
    from marcel_hub.notify.backends import target_kind  # notify/ builds on auth/

    push = target_kind(device) or 'none'
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
    _tell_every_phone_of(request, device)
    return {'device_id': device.id, 'user_id': device.user_id, 'token': token}


def _tell_every_phone_of(request: Request, device: Device) -> None:
    """Any paired phone can pair another (B-49), so every phone hears of a new one: a lost phone
    that pairs a stranger's is seen, and removed, in Settings."""
    from marcel_hub.notify.message import STATUS_LINK, PushNotification, clip  # notify/ uses auth/

    pusher = getattr(request.app.state, 'pusher', None)
    if pusher is None:
        return
    pusher.push(
        PushNotification(
            title='A new phone was paired',
            body=clip(f'{device.name} can now use Marcel. Not you? Remove it in Settings.', 300),
            category='note',
            level='normal',
            deep_link=STATUS_LINK,
            collapse_key=f'paired-{device.id}',
        ),
        None,  # every phone: it is about the install, not one buddy
    )


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
    relay = body.kind == 'relay'  # one target per phone: any other kind clears it
    device.relay_url = body.relay_url if relay else None
    device.relay_handle = body.handle if relay else None
    device.relay_secret = body.send_secret if relay else None
    device.relay_device_key = body.device_key if relay else None
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
    the hub's public https address; without one there is no code, as with `marcel-hub pair`: a
    phone must never pair over plain http, where the code and its token could be read."""
    agent_id = body.agent_id if body is not None else None
    if agent_id is not None:
        find_live_buddy(session, agent_id)
    public_url: str | None = request.app.state.settings.public_url
    if not public_url:
        raise ApiError(400, 'bad_request', f'No pairing code was made. {NO_PUBLIC_URL}')
    hub = hub_api(public_url)
    now = utcnow()
    code = create_pairing_code(session, agent_id=agent_id, now=now)
    return {
        'code': code,
        'link': pairing_url(hub, code, agent_id),
        'expires_at': _iso(now + PAIRING_CODE_TTL),
    }
