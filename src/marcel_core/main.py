import asyncio
import logging
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()  # loads ANTHROPIC_API_KEY and other vars from .env into the process env

from marcel_core.agent.sessions import session_manager
from marcel_core.api.chat import router as chat_router
from marcel_core.api.conversations import router as conversations_router
from marcel_core.api.health import router as health_router
from marcel_core.telegram import router as telegram_router
from marcel_core.watchdog.flags import clear_restart_request, read_restart_request, write_restart_result

log = logging.getLogger(__name__)

_RESTART_POLL_INTERVAL = 2.0  # seconds


def _is_docker() -> bool:
    """Return True if running inside a Docker container."""
    return Path('/.dockerenv').exists()


async def _restart_watcher() -> None:
    """Poll for a restart request flag and trigger a restart when found.

    In Docker: delegates to redeploy.sh which rebuilds/restarts the container
    with rollback on failure. The watchdog (PID 1) handles the process lifecycle.

    Outside Docker (dev mode): exec-replaces the process in-place so the PID
    stays the same and the Python interpreter reloads fresh from disk.
    """
    while True:
        await asyncio.sleep(_RESTART_POLL_INTERVAL)
        sha = read_restart_request()
        if sha:
            log.info('Restart requested (pre-change SHA: %s)', sha)
            clear_restart_request()

            if _is_docker():
                log.info('Docker detected — running redeploy.sh')
                subprocess.Popen(['/app/redeploy.sh', '--no-build'], cwd='/app')
            else:
                write_restart_result('ok')
                os.execv(sys.executable, [sys.executable] + sys.argv)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(_restart_watcher())
    session_manager.start_cleanup_loop()
    yield
    session_manager.stop_cleanup_loop()
    await session_manager.disconnect_all()
    task.cancel()


app = FastAPI(title='Marcel', lifespan=lifespan)

app.include_router(health_router)
app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(telegram_router)
