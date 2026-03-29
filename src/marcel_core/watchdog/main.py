"""
Marcel watchdog — manages the uvicorn subprocess and handles git rollback on failed restarts.

This file must NEVER be modified by Marcel's self-modification system.
It is the safety net. Treat it as read-only infrastructure.
"""

from __future__ import annotations

import logging
import os
import pathlib
import signal
import subprocess
import sys
import time

from marcel_core.watchdog import flags, health, rollback

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s watchdog %(levelname)s: %(message)s',
)
log = logging.getLogger(__name__)

PORT = int(os.environ.get('MARCEL_PORT', '8000'))
HEALTH_TIMEOUT = float(os.environ.get('MARCEL_HEALTH_TIMEOUT', '30'))
POLL_INTERVAL = float(os.environ.get('MARCEL_POLL_INTERVAL', '2'))


def _repo_root() -> pathlib.Path:
    """Walk up from this file until a directory containing ``.git`` is found."""
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        if (parent / '.git').exists():
            return parent
    raise RuntimeError('Could not find repo root')


def _start_uvicorn() -> subprocess.Popen:  # type: ignore[type-arg]
    return subprocess.Popen(
        [
            sys.executable,
            '-m',
            'uvicorn',
            'marcel_core.main:app',
            '--host',
            '0.0.0.0',
            '--port',
            str(PORT),
        ],
        cwd=_repo_root(),
    )


def _stop(proc: subprocess.Popen, timeout: float = 10.0) -> None:  # type: ignore[type-arg]
    """Send SIGTERM to *proc* and wait up to *timeout* seconds; SIGKILL if needed."""
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def run() -> None:
    """Main watchdog loop.  Blocks until a fatal error or explicit exit."""
    log.info('Starting Marcel watchdog')
    repo = _repo_root()
    proc = _start_uvicorn()

    def _handle_signal(signum: int, frame: object) -> None:
        log.info('Watchdog received signal %d — stopping uvicorn and exiting', signum)
        _stop(proc)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Initial startup health check — no rollback on first boot.
    if not health.poll_health(PORT, HEALTH_TIMEOUT, POLL_INTERVAL):
        log.error('Marcel failed to start. Exiting.')
        _stop(proc)
        sys.exit(1)

    log.info('Marcel is up on port %d', PORT)

    # Monitor loop
    while True:
        time.sleep(POLL_INTERVAL)

        # Check for restart request from agent.
        pre_change_sha = flags.read_restart_request()
        if pre_change_sha:
            log.info('Restart requested (pre-change SHA: %s)', pre_change_sha)
            flags.clear_restart_request()
            _stop(proc)
            proc = _start_uvicorn()

            if health.poll_health(PORT, HEALTH_TIMEOUT, POLL_INTERVAL):
                log.info('Restart successful')
                flags.write_restart_result('ok')
            else:
                log.warning('Health check failed after restart — rolling back')
                _stop(proc)
                try:
                    rollback.do_rollback(repo)
                    log.info('Rollback committed. Restarting from previous version.')
                except Exception as exc:
                    log.error('Rollback failed: %s', exc)
                    flags.write_restart_result('rollback_failed')
                    sys.exit(1)

                proc = _start_uvicorn()
                if health.poll_health(PORT, HEALTH_TIMEOUT, POLL_INTERVAL):
                    log.info('Marcel is up on rolled-back code')
                    flags.write_restart_result('rolled_back')
                else:
                    log.error('Marcel failed to start even after rollback. Manual intervention required.')
                    flags.write_restart_result('rollback_failed')
                    sys.exit(1)

        # Check if subprocess died unexpectedly.
        if proc.poll() is not None:
            log.warning(
                'uvicorn exited unexpectedly (code %d). Restarting.',
                proc.returncode,
            )
            proc = _start_uvicorn()
            if not health.poll_health(PORT, HEALTH_TIMEOUT, POLL_INTERVAL):
                log.error('Marcel failed to restart after unexpected exit.')
                sys.exit(1)


if __name__ == '__main__':
    run()
