"""Device tokens and one-time pairing codes. Only their sha256 is ever stored."""

import hashlib
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote

from sqlalchemy import delete, or_, update
from sqlmodel import Session, col

from marcel_hub.auth.errors import ApiError
from marcel_hub.models import Device, PairingCode, utcnow

# Marcel has one user, the owner. Every device and agent belongs to them for now.
OWNER_USER_ID = 'usr_owner'

TOKEN_PREFIX = 'mrc_'
PAIRING_CODE_TTL = timedelta(minutes=10)
# No 0/O or 1/I, so a code read off a screen is typed right. 8 of 32 symbols = 40 bits.
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
CODE_LENGTH = 8

PAIR_AGAIN = 'Run `marcel-hub pair` again for a new one.'


def new_token() -> str:
    """32 random bytes, hex-encoded, with a prefix so a leaked token is recognisable."""
    return TOKEN_PREFIX + secrets.token_hex(32)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def new_pairing_code() -> str:
    return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def pairing_url(hub: str, code: str) -> str:
    """The `PairingCode` URL the QR code encodes."""
    return f'marcel://pair?hub={quote(hub, safe="")}&code={code}'


def create_pairing_code(
    session: Session, *, user_id: str = OWNER_USER_ID, now: datetime | None = None
) -> str:
    """Store a new code (hashed) that works once, for 10 minutes from `now`. Old codes go."""
    session.exec(
        delete(PairingCode).where(
            or_(col(PairingCode.used_at).is_not(None), col(PairingCode.expires_at) <= utcnow())
        )
    )
    code = new_pairing_code()
    now = now or utcnow()
    session.add(
        PairingCode(
            code_hash=hash_secret(code),
            user_id=user_id,
            created_at=now,
            expires_at=now + PAIRING_CODE_TTL,
        )
    )
    session.commit()
    return code


def redeem_pairing_code(session: Session, code: str, device_name: str) -> tuple[Device, str]:
    """Use up `code` and create a device. Returns the device and its token (shown once)."""
    code_hash = hash_secret(code.strip().upper())
    row = session.get(PairingCode, code_hash)
    if row is None:
        raise ApiError(400, 'pairing_code_invalid', f'That pairing code is not right. {PAIR_AGAIN}')
    now = utcnow()
    if row.expires_at <= now:
        raise ApiError(400, 'pairing_code_expired', f'That pairing code has expired. {PAIR_AGAIN}')
    claimed = session.exec(
        update(PairingCode)
        .where(col(PairingCode.code_hash) == code_hash, col(PairingCode.used_at).is_(None))
        .values(used_at=now)
    )
    if claimed.rowcount != 1:  # used before, or by a request racing this one
        session.rollback()
        raise ApiError(
            400, 'pairing_code_used', f'That pairing code was already used. {PAIR_AGAIN}'
        )
    token = new_token()
    device = Device(user_id=row.user_id, name=device_name, token_hash=hash_secret(token))
    session.add(device)
    session.commit()
    session.refresh(device)
    return device, token
