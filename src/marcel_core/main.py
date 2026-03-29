import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()  # loads ANTHROPIC_API_KEY and other vars from .env into the process env

from marcel_core.api.chat import router as chat_router
from marcel_core.api.health import router as health_router
from marcel_core.telegram import router as telegram_router
from marcel_core.watchdog.flags import clear_restart_request, read_restart_request, write_restart_result

log = logging.getLogger(__name__)

_RESTART_POLL_INTERVAL = 2.0  # seconds


async def _restart_watcher() -> None:
    """Poll for a restart request flag and exec-replace the process when found.

    This allows Marcel to restart itself after a self-modification without requiring
    sudo or an external watchdog process. os.execv replaces the running process image
    in-place — the PID stays the same (systemd keeps tracking it), but the Python
    interpreter and all imported modules are reloaded fresh from disk.
    """
    while True:
        await asyncio.sleep(_RESTART_POLL_INTERVAL)
        sha = read_restart_request()
        if sha:
            log.info('Restart requested (pre-change SHA: %s) — exec-replacing process', sha)
            clear_restart_request()
            write_restart_result('ok')
            # Replace the current process image with a fresh uvicorn instance.
            # sys.argv is ['uvicorn', 'marcel_core.main:app', '--host', ...]
            os.execv(sys.executable, [sys.executable] + sys.argv)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(_restart_watcher())
    yield
    task.cancel()


app = FastAPI(title='Marcel', lifespan=lifespan)

app.include_router(health_router)
app.include_router(chat_router)
app.include_router(telegram_router)
