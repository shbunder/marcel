import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from marcel_core.config import settings

# Configure application-level logging so marcel_core.* loggers are visible.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
)

from marcel_core.agent.sessions import session_manager
from marcel_core.api.chat import router as chat_router
from marcel_core.api.chat_v2 import router as chat_v2_router
from marcel_core.api.conversations import router as conversations_router
from marcel_core.api.health import router as health_router
from marcel_core.api.sessions import router as sessions_router
from marcel_core.channels.telegram import router as telegram_router
from marcel_core.skills.integrations.banking.sync import start_sync_loop, stop_sync_loop
from marcel_core.watchdog.flags import read_restart_request, write_restart_result

log = logging.getLogger(__name__)

_RESTART_POLL_INTERVAL = 2.0  # seconds


def _is_docker() -> bool:
    """Return True if running inside a Docker container."""
    return Path('/.dockerenv').exists()


async def _restart_watcher() -> None:
    """Poll for a restart request flag and trigger a restart when found.

    In Docker: the flag file is left in place for the host-side systemd path
    unit (marcel-redeploy.path) to detect and trigger redeploy.sh. The
    watchdog (PID 1) handles the process lifecycle within the container.

    Outside Docker (dev mode): exec-replaces the process in-place so the PID
    stays the same and the Python interpreter reloads fresh from disk.
    """
    while True:
        await asyncio.sleep(_RESTART_POLL_INTERVAL)
        sha = read_restart_request()
        if sha:
            log.info('Restart requested (pre-change SHA: %s)', sha)

            if _is_docker():
                # Leave the flag file in place — the host-side systemd path
                # unit watches it and triggers redeploy.sh on the host.
                log.info('Docker detected — restart flag written, waiting for host-side redeploy')
            else:
                write_restart_result('ok')
                os.execv(sys.executable, [sys.executable] + sys.argv)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Clear Telegram sessions so every user starts fresh after a restart
    from marcel_core.channels.telegram.sessions import clear_all_sessions

    clear_all_sessions()

    # Migrate any legacy history.jsonl files to per-session files
    from marcel_core.memory.history import migrate_legacy_history
    from marcel_core.storage._root import data_root

    users_dir = data_root() / 'users'
    if users_dir.exists():
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('_'):
                legacy = user_dir / 'history.jsonl'
                if legacy.exists():
                    count = migrate_legacy_history(user_dir.name, default_channel='telegram')
                    if count:
                        log.info('Migrated %d sessions for user %s', count, user_dir.name)

    task = asyncio.create_task(_restart_watcher())
    session_manager.start_cleanup_loop()
    start_sync_loop()
    yield
    stop_sync_loop()
    session_manager.stop_cleanup_loop()
    await session_manager.disconnect_all()
    task.cancel()


app = FastAPI(title='Marcel', lifespan=lifespan)

# CORS — needed for Vite dev server (different port) during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(health_router)
app.include_router(chat_router)
app.include_router(chat_v2_router)  # v2 harness endpoint (pydantic-ai)
app.include_router(conversations_router)
app.include_router(sessions_router)  # v2 session management
app.include_router(telegram_router)

# Serve the built web frontend (SPA) if it exists
_WEB_DIST = Path(__file__).resolve().parent.parent / 'web' / 'dist'
_API_PREFIXES = ('ws', 'v2', 'health', 'conversations', 'telegram', 'api')

if _WEB_DIST.is_dir():
    _assets_dir = _WEB_DIST / 'assets'
    if _assets_dir.is_dir():
        app.mount('/assets', StaticFiles(directory=str(_assets_dir)), name='web-assets')

    @app.get('/{path:path}')
    async def _spa_fallback(path: str) -> FileResponse:
        """Serve index.html for all non-API routes (SPA client-side routing)."""
        if path and path.split('/')[0] in _API_PREFIXES:
            from fastapi import HTTPException

            raise HTTPException(status_code=404)
        return FileResponse(str(_WEB_DIST / 'index.html'))
