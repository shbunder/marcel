"""Lifecycle for spawned connector servers (FEAT-260718-230bf8).

``http`` connectors are a shared upstream with a per-request header, so they
need no lifecycle. ``stdio`` and ``inprocess`` connectors are different: the
credential is delivered **once at spawn**, so each ``(connector, user)`` pair
gets its **own** instance.

What that pairing buys is **correct credential attribution**, not isolation.
Reusing Alice's instance for Bob's turn would send Bob's tool calls upstream
carrying *Alice's* token — Marcel silently acting as the wrong family member
against a real account. Keying by pair is what prevents that confused-deputy
bug, and it holds regardless of how Marcel is deployed.

It is **not** a security boundary, and should never be described as one.
Marcel runs as a single container under one uid: every stdio instance is a
child of that process, and ``inprocess`` instances share Marcel's own memory.
A connector spawned for one user can read every user's ``tokens.enc`` (0600
guards against *other OS users*, not against sibling connectors) and, for
``inprocess``, the credential vault itself. Connector code is therefore
**trusted code**, on the same footing as in-process toolkit habitats
(ADR-260628-6101c5, "lean isolation"). Running a connector you would not run
as yourself needs real containment — bubblewrap for stdio, or the ``http``
transport, where the server never touches the host — not this registry.

:class:`ConnectorRegistry` owns those instances:

* **lazy start** — nothing is built until the pair is first asked for, and the
  transport itself defers the subprocess to its first connect;
* **reuse** — the same pair gets the same instance across turns, so a
  conversation does not pay a subprocess spawn per message;
* **idle stop** — :meth:`reap_idle` closes instances untouched for longer than
  the idle window, so a connector used once at breakfast is not still running
  at dinner;
* **bounded-backoff restart** — a pair whose spawn failed is not retried until
  its backoff elapses, and the delay doubles per consecutive failure up to a
  ceiling, so a broken habitat cannot spin the host.

The registry is deliberately transport-agnostic: it takes a factory, so tests
drive the full lifecycle without spawning a process.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from marcel_core.connectors.models import ConnectorConfig, Transport

log = logging.getLogger(__name__)

# Close an instance after this long without use.
DEFAULT_IDLE_SECONDS = 600.0
# Restart backoff: first retry after this, doubling per consecutive failure…
BACKOFF_BASE_SECONDS = 2.0
# …never waiting longer than this between attempts.
BACKOFF_MAX_SECONDS = 300.0

InstanceFactory = Callable[[ConnectorConfig, str], object]
"""Builds the transport instance for one (connector, user). Injected for tests.

Synchronous on purpose: constructing a client/toolset is cheap and does **not**
spawn anything — fastmcp defers the actual subprocess to the first connect. So
"lazy start" is already the transport's behaviour, and this registry's job is
the part fastmcp does not do: keeping one instance per pair alive across turns,
reaping it when idle, and refusing to rebuild a broken one in a tight loop.
"""


class ConnectorStartError(Exception):
    """A spawned connector could not be started (and is in backoff)."""


@dataclass
class _Entry:
    instance: object | None = None
    last_used: float = field(default_factory=time.monotonic)
    failures: int = 0
    retry_after: float = 0.0  # monotonic deadline; 0 ⇒ no backoff pending

    def backoff_remaining(self, now: float) -> float:
        return max(0.0, self.retry_after - now)


class ConnectorRegistry:
    """Per-(connector, user) instances for spawned connector transports."""

    def __init__(
        self,
        factory: InstanceFactory,
        *,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        closer: Callable[[object], Awaitable[None]] | None = None,
    ) -> None:
        self._factory = factory
        self._idle_seconds = idle_seconds
        self._closer = closer
        self._entries: dict[tuple[str, str], _Entry] = {}

    @staticmethod
    def handles(config: ConnectorConfig) -> bool:
        """Whether this connector's transport needs a managed instance."""
        return config.server.transport in (Transport.STDIO, Transport.INPROCESS)

    def acquire(self, config: ConnectorConfig, slug: str, *, now: float | None = None) -> object:
        """The live instance for ``(config, slug)``, spawning it on first use.

        Raises :class:`ConnectorStartError` while the pair is inside its restart
        backoff, or when the factory fails (which starts/extends the backoff).
        """
        clock = time.monotonic() if now is None else now
        key = (config.name, slug)
        entry = self._entries.setdefault(key, _Entry(last_used=clock))

        if entry.instance is not None:
            entry.last_used = clock
            return entry.instance

        remaining = entry.backoff_remaining(clock)
        if remaining > 0:
            raise ConnectorStartError(
                f'{config.name!r} could not be started and is retrying shortly — try again in a moment.'
            )

        try:
            instance = self._factory(config, slug)
        except Exception as exc:
            entry.failures += 1
            delay = min(BACKOFF_BASE_SECONDS * (2 ** (entry.failures - 1)), BACKOFF_MAX_SECONDS)
            entry.retry_after = clock + delay
            # Never log the spawn env — it carries the user's credential.
            log.warning(
                'connectors: %s failed to start for %s (attempt %d, backing off %.0fs): %s',
                config.name,
                slug,
                entry.failures,
                delay,
                type(exc).__name__,
            )
            raise ConnectorStartError(
                f'{config.name!r} could not be started right now — please try again in a moment.'
            ) from exc

        entry.instance = instance
        entry.failures = 0
        entry.retry_after = 0.0
        entry.last_used = clock
        log.info('connectors: started %s for %s', config.name, slug)
        return instance

    async def reap_idle(self, *, now: float | None = None) -> int:
        """Close instances untouched for longer than the idle window.

        Returns how many were closed. Safe to call on a timer.
        """
        clock = time.monotonic() if now is None else now
        closed = 0
        for key, entry in list(self._entries.items()):
            if entry.instance is None or clock - entry.last_used <= self._idle_seconds:
                continue
            await self._close(entry.instance)
            entry.instance = None
            self._entries.pop(key, None)
            closed += 1
            log.info('connectors: stopped idle %s for %s', key[0], key[1])
        return closed

    async def shutdown(self) -> None:
        """Close every live instance (server stop)."""
        for key, entry in list(self._entries.items()):
            if entry.instance is not None:
                await self._close(entry.instance)
            self._entries.pop(key, None)

    async def _close(self, instance: object) -> None:
        if self._closer is None:
            return
        try:
            await self._closer(instance)
        except Exception:
            log.warning('connectors: error closing instance', exc_info=True)

    # -- introspection (tests / diagnostics) ---------------------------------

    def live_keys(self) -> set[tuple[str, str]]:
        return {k for k, e in self._entries.items() if e.instance is not None}
