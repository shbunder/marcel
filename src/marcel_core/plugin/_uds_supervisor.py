"""UDS habitat supervisor — spawn, health-monitor, respawn, teardown.

Phase 1 of ISSUE-f60b09. Kernel-side counterpart to :mod:`_uds_bridge`.

The loader calls :func:`spawn_habitat` once per habitat declaring
``isolation: uds``; the returned :class:`HabitatHandle` carries the
pid + socket path the proxy coroutines connect to. The module-level
:func:`start_supervisor` launches an asyncio task that polls
``Popen.poll()`` on every tracked habitat and restarts any that exited
uncleanly, with exponential backoff (1 s, 2 s, 4 s, …, capped at 60 s).

Teardown (:func:`stop_supervisor`) sends SIGTERM to each child, waits
up to ``_GRACE_SECONDS``, then escalates to SIGKILL on anything still
running. Called from ``lifespan()`` shutdown.

All public API is async-safe. The supervisor is intentionally minimal —
no restart-count caps, no circuit breaker, no dead-habitat quarantine.
Phase 1 is "dumb retry with backoff"; smarter policy lands only if real
operations prove the simple version insufficient.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_POLL_INTERVAL = 2.0  # seconds between health checks
_GRACE_SECONDS = 5.0  # SIGTERM → SIGKILL window on teardown
_BACKOFF_START = 1.0
_BACKOFF_CAP = 60.0
_SOCKET_READY_TIMEOUT = 5.0  # seconds to wait for a spawned habitat's socket to appear


@dataclass
class HabitatHandle:
    """Bookkeeping for one spawned habitat.

    Mutated in place by the supervisor on respawn (``proc`` and
    ``pid`` change; ``name``/``socket_path``/``command`` are stable).
    """

    name: str
    socket_path: Path
    command: list[str]
    proc: subprocess.Popen[bytes] | None = None
    last_exit_code: int | None = None
    last_restart_at: float = 0.0
    backoff_next: float = _BACKOFF_START
    # Paused=True means we're in a backoff window; the supervisor won't
    # try to restart until the next poll cycle after ``last_restart_at + backoff_next``.
    paused: bool = False

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None else None


@dataclass
class _SupervisorState:
    handles: dict[str, HabitatHandle] = field(default_factory=dict)
    task: asyncio.Task[None] | None = None
    stop_event: asyncio.Event | None = None


_state = _SupervisorState()


def spawn_habitat(name: str, command: list[str], socket_path: Path) -> HabitatHandle:
    """Spawn one habitat subprocess and wait for its socket to be ready.

    *command* is the argv list (first element is the interpreter — the
    habitat's ``.venv/bin/python`` in Phase 2+; for Phase 1 testing
    with fixture habitats, ``sys.executable`` is the sensible default).

    Raises :class:`RuntimeError` if the socket does not appear within
    ``_SOCKET_READY_TIMEOUT`` — the caller treats that as a habitat
    load failure.
    """
    if name in _state.handles:
        raise ValueError(f'habitat {name!r} already spawned')

    socket_path.unlink(missing_ok=True)  # paranoia: bridge also does this
    proc = _spawn(command)
    _wait_for_socket(socket_path, proc, timeout=_SOCKET_READY_TIMEOUT)

    handle = HabitatHandle(name=name, socket_path=socket_path, command=command, proc=proc)
    _state.handles[name] = handle
    log.info('uds-supervisor: spawned habitat %r (pid=%d) on %s', name, proc.pid, socket_path)
    return handle


def _spawn(command: list[str]) -> subprocess.Popen[bytes]:
    """Start the subprocess. Stdout/stderr inherit so habitat logs surface in kernel logs."""
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=None,  # inherit
        stderr=None,  # inherit
        start_new_session=True,  # lets us signal the whole group on teardown
    )


def _wait_for_socket(path: Path, proc: subprocess.Popen[bytes], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f'habitat exited before socket appeared (code={proc.returncode})')
        if path.exists():
            return
        time.sleep(0.05)
    # Timed out — kill the stuck child so we don't leak zombies.
    try:
        proc.terminate()
    except OSError:
        pass
    raise RuntimeError(f'habitat socket {path} did not appear within {timeout}s')


def start_supervisor() -> None:
    """Launch the background poll loop.  No-op if already running."""
    if _state.task is not None and not _state.task.done():
        return
    _state.stop_event = asyncio.Event()
    _state.task = asyncio.create_task(_poll_loop(), name='uds-supervisor')


async def _poll_loop() -> None:
    assert _state.stop_event is not None
    try:
        while not _state.stop_event.is_set():
            try:
                await asyncio.wait_for(_state.stop_event.wait(), timeout=_POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass
            if _state.stop_event.is_set():
                return
            _check_and_respawn()
    except asyncio.CancelledError:
        return


def _check_and_respawn() -> None:
    now = time.monotonic()
    for handle in list(_state.handles.values()):
        proc = handle.proc
        if proc is None:
            _attempt_restart(handle, now)
            continue
        rc = proc.poll()
        if rc is None:
            # Running — reset the backoff so the next crash starts from 1 s.
            handle.backoff_next = _BACKOFF_START
            handle.paused = False
            continue
        # Process exited.
        handle.last_exit_code = rc
        handle.proc = None
        log.warning('uds-supervisor: habitat %r exited (code=%s)', handle.name, rc)
        _attempt_restart(handle, now)


def _attempt_restart(handle: HabitatHandle, now: float) -> None:
    if handle.paused and now < handle.last_restart_at + handle.backoff_next:
        return
    try:
        handle.proc = _spawn(handle.command)
        handle.last_restart_at = now
        handle.paused = True  # enter backoff window; cleared when poll sees it still running
        log.info(
            'uds-supervisor: restarted habitat %r (pid=%d) after %.1fs backoff',
            handle.name,
            handle.proc.pid,
            handle.backoff_next,
        )
        handle.backoff_next = min(handle.backoff_next * 2, _BACKOFF_CAP)
    except Exception:
        log.exception('uds-supervisor: failed to restart habitat %r', handle.name)
        handle.last_restart_at = now
        handle.paused = True
        handle.backoff_next = min(handle.backoff_next * 2, _BACKOFF_CAP)


async def stop_supervisor() -> None:
    """Signal the poll loop to stop, then terminate every tracked habitat.

    Blocks until all children exit or the SIGTERM grace window elapses,
    at which point stragglers are SIGKILLed. Called from ``lifespan()``
    teardown.
    """
    if _state.stop_event is not None:
        _state.stop_event.set()
    if _state.task is not None:
        try:
            await _state.task
        except asyncio.CancelledError:
            pass
        _state.task = None

    # Terminate all tracked habitats.
    for handle in list(_state.handles.values()):
        proc = handle.proc
        if proc is None or proc.poll() is not None:
            continue
        try:
            # start_new_session=True made each child its own process group;
            # SIGTERM the group so any grandchildren go too.
            os.killpg(proc.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

    deadline = time.monotonic() + _GRACE_SECONDS
    for handle in list(_state.handles.values()):
        proc = handle.proc
        if proc is None:
            continue
        remaining = max(0.0, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            log.warning('uds-supervisor: habitat %r did not exit within grace — sending SIGKILL', handle.name)
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=1.0)
            except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
                pass
        handle.proc = None

    _state.handles.clear()


def list_habitats() -> dict[str, HabitatHandle]:
    """Return a shallow copy of the supervisor's handle table (for tests + ops).

    The returned dict is a snapshot; mutating it does not affect the
    supervisor's internal state.
    """
    return dict(_state.handles)


def _reset_for_tests() -> None:
    """Clear all supervisor state. Test-only — do not call from production code."""
    _state.handles.clear()
    _state.task = None
    _state.stop_event = None


# Convenience: Phase 2+ will want to pick the habitat's own .venv python
# if available. Phase 1 tests pass an explicit command, so this helper is
# future-proofing rather than load-bearing.
def habitat_python(habitat_dir: Path) -> str:
    """Return the python interpreter to use for *habitat_dir*.

    Prefers ``<habitat_dir>/.venv/bin/python`` if it exists, else falls
    back to the kernel's ``sys.executable``.
    """
    venv_py = habitat_dir / '.venv' / 'bin' / 'python'
    if venv_py.exists():
        return str(venv_py)
    return sys.executable
