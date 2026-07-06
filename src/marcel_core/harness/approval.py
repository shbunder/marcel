"""In-turn command-approval registry — pause a tool call for a human.

When the command policy classifies an action ``ask``, the ``tool_call``
handler creates an :class:`ApprovalRequest`, forwards a plain-language prompt
to the user's channel (Telegram), and ``await``\\s the registry for a verdict.
The channel's callback (a button press) calls :meth:`ApprovalRegistry.resolve`
in the *same process*, waking the waiter (ADR-260628-ca8f39).

If no verdict arrives within the window the request **expires → deny**, and
the request is persisted to an auditable pending queue
(:mod:`marcel_core.storage.approvals`) so the user can approve it later and
re-trigger the action as a fresh run — never auto-allow.

A single process-wide registry is shared by the turn (which waits) and the
channel webhook (which resolves): use :func:`approval_registry`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from marcel_core.storage import approvals as approval_store

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 300.0

# A command string can carry an inline secret (an auth header, a token, a
# ``-p<password>``). We persist approval records to disk (audit log + queue)
# outside the encrypted credential store, so redact obvious secret shapes
# before writing — never log or persist them in the clear (data-boundaries rule).
# Each pattern captures at most ONE group — the *keep* prefix; the secret is
# the un-captured tail, which is replaced. Patterns with no group are masked
# whole.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'(?i)(authorization:\s*(?:bearer|basic)\s+)\S+'),
    re.compile(r'(?i)\b(bearer\s+)[A-Za-z0-9._\-]{8,}'),
    re.compile(r'(?i)\b((?:api[_-]?key|token|secret|password|passwd|pwd)["\']?\s*[=:]\s*["\']?)\S+'),
    re.compile(r'(-p)\S{3,}'),  # mysql-style -p<password>
    re.compile(r'\b[A-Za-z0-9]{4}[A-Za-z0-9._\-]{28,}\b'),  # long high-entropy tokens (no group)
)


def _redact(text: str) -> str:
    """Mask obvious secret shapes in a command/summary string."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.lastindex else '') + '«redacted»', text)
    return text


def _redacted_record(record: dict) -> dict:
    """Return a copy of an approval record with secrets masked in its text."""
    safe = dict(record)
    if isinstance(safe.get('summary'), str):
        safe['summary'] = _redact(safe['summary'])
    args = safe.get('args')
    if isinstance(args, dict):
        safe['args'] = {k: _redact(v) if isinstance(v, str) else v for k, v in args.items()}
    return safe


class ApprovalOutcome(str, Enum):
    """How an approval request resolved."""

    ALLOW_ONCE = 'allow_once'
    ALLOW_ALWAYS = 'allow_always'
    DENY = 'deny'
    EXPIRED = 'expired'


ALLOWING_OUTCOMES = frozenset({ApprovalOutcome.ALLOW_ONCE, ApprovalOutcome.ALLOW_ALWAYS})


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass(frozen=True)
class ApprovalRequest:
    """One pending approval — the action a human is being asked to allow."""

    id: str
    user_slug: str
    channel: str
    tool_name: str
    summary: str
    args: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'user_slug': self.user_slug,
            'channel': self.channel,
            'tool_name': self.tool_name,
            'summary': self.summary,
            'args': self.args,
            'created_at': self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ApprovalRequest:
        return cls(
            id=data['id'],
            user_slug=data['user_slug'],
            channel=data.get('channel', ''),
            tool_name=data.get('tool_name', ''),
            summary=data.get('summary', ''),
            args=data.get('args', {}),
            created_at=data.get('created_at', _now_iso()),
        )


class ApprovalRegistry:
    """Tracks in-flight approval requests as awaitable futures.

    ``wait`` blocks the turn until the channel callback ``resolve``\\s the
    request or the window expires. Stateless across restarts by design —
    a pending in-memory request that outlives a restart simply expires; its
    durable form is the queued record written on expiry.
    """

    def __init__(self, default_timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> None:
        self._pending: dict[str, asyncio.Future[ApprovalOutcome]] = {}
        self._requests: dict[str, ApprovalRequest] = {}
        self._default_timeout = default_timeout

    def new_request(
        self,
        *,
        user_slug: str,
        channel: str,
        tool_name: str,
        summary: str,
        args: dict | None = None,
    ) -> ApprovalRequest:
        """Build a fresh :class:`ApprovalRequest` with a unique id."""
        return ApprovalRequest(
            id=uuid.uuid4().hex[:12],
            user_slug=user_slug,
            channel=channel,
            tool_name=tool_name,
            summary=summary,
            args=args or {},
        )

    async def wait(self, request: ApprovalRequest, *, timeout: float | None = None) -> ApprovalOutcome:
        """Await a verdict for ``request``; expire → DENY (queued + audited).

        Registers the request, blocks up to ``timeout`` seconds (the
        configured default when ``None``), and returns the resolved outcome.
        On timeout the request is queued for later approval and the outcome
        is :attr:`ApprovalOutcome.EXPIRED` (which the caller treats as deny).
        Every outcome is written to the audit log.
        """
        window = self._default_timeout if timeout is None else timeout
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalOutcome] = loop.create_future()
        self._pending[request.id] = future
        self._requests[request.id] = request
        try:
            outcome = await asyncio.wait_for(future, timeout=window)
        except asyncio.TimeoutError:
            outcome = ApprovalOutcome.EXPIRED
            approval_store.queue_pending(_redacted_record(request.to_dict()))
            log.info('approval %s expired after %.0fs — queued for later', request.id, window)
        finally:
            self._pending.pop(request.id, None)
            self._requests.pop(request.id, None)
        self._audit(request, outcome)
        return outcome

    def resolve(self, approval_id: str, outcome: ApprovalOutcome) -> bool:
        """Deliver a verdict from the channel callback. Returns True if it landed.

        A no-op (returns False) for an unknown or already-resolved id, so a
        duplicate button press is harmless.
        """
        future = self._pending.get(approval_id)
        if future is None or future.done():
            return False
        future.set_result(outcome)
        return True

    def pending_ids(self) -> list[str]:
        """Ids currently awaiting a verdict (introspection/tests)."""
        return list(self._pending)

    def owner_of(self, approval_id: str) -> str | None:
        """Return the ``user_slug`` a pending request was forwarded to, or ``None``.

        The channel callback uses this to verify the button-presser is the
        request's owner before resolving it — a resolve must not be honoured
        for someone else's approval.
        """
        request = self._requests.get(approval_id)
        return request.user_slug if request is not None else None

    def _audit(self, request: ApprovalRequest, outcome: ApprovalOutcome) -> None:
        record = _redacted_record(request.to_dict())
        record['outcome'] = outcome.value
        record['resolved_at'] = _now_iso()
        try:
            approval_store.append_audit(record)
        except Exception:
            log.exception('failed to write approval audit record for %s', request.id)


_REGISTRY = ApprovalRegistry()


def approval_registry() -> ApprovalRegistry:
    """Return the process-wide approval registry (shared turn ↔ channel callback)."""
    return _REGISTRY
