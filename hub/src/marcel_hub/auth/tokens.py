"""Device tokens and one-time pairing codes. Only their sha256 is ever stored."""

import hashlib
import secrets
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from urllib.parse import quote

from sqlalchemy import delete, or_, update
from sqlmodel import Session, col, select

from marcel_hub.auth.errors import ApiError
from marcel_hub.models import Agent, Device, PairingCode, utcnow

# An install belongs to one person, the owner (ADR-013). Every device and buddy is theirs.
OWNER_USER_ID = 'usr_owner'

TOKEN_PREFIX = 'mrc_'
PAIRING_CODE_TTL = timedelta(minutes=10)
# No 0/O or 1/I, so a code read off a screen is typed right. 8 of 32 symbols = 40 bits.
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
CODE_LENGTH = 8

PAIR_AGAIN = 'Run `marcel-hub pair` again for a new one.'
# Without the hub setting `public_url` there is no address to put in a pairing link (S-22.2).
NO_PUBLIC_URL = 'No public address yet: run `marcel connect`.'


def hub_api(public_url: str) -> str:
    """The address a phone reaches the hub's API at: `<public_url>/api`."""
    return f'{public_url}/api'


# `POST /pair` is public. After this many unknown codes in a window it refuses every code, the
# right one too, until the window ends: guessing a live 40-bit code then takes centuries.
MAX_WRONG_CODES = 10
WRONG_CODE_WINDOW = 60.0  # seconds


class PairGuessLimit:
    """Counts unknown pairing codes across all callers (behind the tunnel they share an address)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.failures: list[float] = []

    def _recent(self) -> list[float]:
        now = self.clock()
        self.failures = [t for t in self.failures if now - t < WRONG_CODE_WINDOW]
        return self.failures

    def check(self) -> None:
        if len(self._recent()) >= MAX_WRONG_CODES:
            raise ApiError(
                429,
                'too_many_tries',
                'Too many wrong pairing codes. Wait a minute, then try again.',
                headers={'Retry-After': str(int(WRONG_CODE_WINDOW))},
            )

    def failed(self) -> None:
        self._recent().append(self.clock())


def new_token() -> str:
    """32 random bytes, hex-encoded, with a prefix so a leaked token is recognisable."""
    return TOKEN_PREFIX + secrets.token_hex(32)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def new_pairing_code() -> str:
    return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def pairing_url(hub: str, code: str, buddy: str | None = None) -> str:
    """The `PairingCode` URL the QR code encodes; with `buddy` (an agent id), `&buddy=` names the
    buddy the new phone starts on (app-api 0.8.0)."""
    url = f'marcel://pair?hub={quote(hub, safe="")}&code={code}'
    return f'{url}&buddy={quote(buddy, safe="")}' if buddy else url


def live_buddy(session: Session, agent_id: str) -> Agent | None:
    """The buddy `agent_id` while it is live (not archived), else None."""
    agent = session.get(Agent, agent_id)
    return agent if agent is not None and agent.archived_at is None else None


def create_pairing_code(
    session: Session,
    *,
    user_id: str = OWNER_USER_ID,
    agent_id: str | None = None,
    now: datetime | None = None,
) -> str:
    """Store a new code (hashed) that works once, for 10 minutes from `now`. Old codes go. With
    `agent_id`, the phone it pairs starts on that buddy (`redeem_pairing_code`)."""
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
            agent_id=agent_id,
        )
    )
    session.commit()
    return code


def first_buddy(session: Session) -> str | None:
    """The first buddy `/me` lists (live, with its main conversation; the oldest id first),
    which a phone shows when it has no buddy of its own; None when there is none yet."""
    return session.exec(
        select(Agent.id)
        .where(col(Agent.archived_at).is_(None), col(Agent.main_conversation_id).is_not(None))
        .order_by(col(Agent.id))
        .limit(1)
    ).first()


def redeem_pairing_code(session: Session, code: str, device_name: str) -> tuple[Device, str]:
    """Use up `code` and create a device that shows the code's buddy while it is live, else the
    first buddy. Returns the device and its token (shown once)."""
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
    named = live_buddy(session, row.agent_id) if row.agent_id is not None else None
    device = Device(
        user_id=row.user_id,
        name=device_name,
        token_hash=hash_secret(token),
        agent_id=named.id if named is not None else first_buddy(session),
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    return device, token
