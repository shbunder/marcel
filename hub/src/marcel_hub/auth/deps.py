"""The device-token check every protected router depends on."""

import hmac
from collections.abc import Iterator
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session, select

from marcel_hub.auth.errors import ApiError
from marcel_hub.auth.tokens import hash_secret
from marcel_hub.models import Device, utcnow

bearer = HTTPBearer(auto_error=False, description='The device token from `POST /pair`.')

# Writing `last_seen` on every request would turn every read into a write.
LAST_SEEN_EVERY = timedelta(minutes=1)


def unauthorized() -> ApiError:
    return ApiError(
        401,
        'unauthorized',
        'This phone is not paired, or it was removed. Pair it again with `marcel-hub pair`.',
        headers={'WWW-Authenticate': 'Bearer'},
    )


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session


DbSession = Annotated[Session, Depends(get_session)]


def require_device(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: DbSession,
) -> Device:
    """The paired device making this request, or 401. Put it on every non-public router."""
    if credentials is None:
        raise unauthorized()
    token_hash = hash_secret(credentials.credentials)
    device = session.exec(select(Device).where(Device.token_hash == token_hash)).first()
    if device is None or not hmac.compare_digest(device.token_hash, token_hash):
        raise unauthorized()
    now = utcnow()
    if device.last_seen is None or now - device.last_seen >= LAST_SEEN_EVERY:
        device.last_seen = now
        session.add(device)
        session.commit()
        session.refresh(device)
    request.state.device = device
    return device


def current_device(request: Request) -> Device:
    """The device `require_device` let in. Raises if the router forgot the dependency."""
    device = getattr(request.state, 'device', None)
    if device is None:
        raise RuntimeError(f'{request.url.path} has no device: its router needs require_device.')
    return device
