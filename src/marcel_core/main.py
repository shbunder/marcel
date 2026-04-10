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

from marcel_core import __version__
from marcel_core.config import settings

# Configure application-level logging.
# Format: [LOG-TYPE] [timestamp] module: message
_LOG_FORMAT = '[%(levelname)-8s] [%(asctime)s] %(name)s: %(message)s'
_LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

logging.basicConfig(
    level=logging.INFO,
    format=_LOG_FORMAT,
    datefmt=_LOG_DATE_FORMAT,
)


# Suppress noisy health-check spam from uvicorn access logs
class _HealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return '/health' not in msg


logging.getLogger('uvicorn.access').addFilter(_HealthCheckFilter())

# Quieten noisy third-party loggers
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)

from marcel_core.api.artifacts import router as artifacts_router
from marcel_core.api.chat import router as chat_router
from marcel_core.api.conversations import router as conversations_router
from marcel_core.api.health import router as health_router
from marcel_core.channels.telegram import router as telegram_router
from marcel_core.jobs.scheduler import scheduler
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


async def _background_summarization_loop() -> None:
    """Periodically check all channels for idle conversations and summarize them.

    Runs every 15 minutes. This ensures summaries are ready before the user
    returns, rather than waiting for the next message.
    """
    from marcel_core.config import settings as cfg
    from marcel_core.memory.conversation import (
        has_active_content,
        is_idle,
    )
    from marcel_core.memory.summarizer import summarize_active_segment
    from marcel_core.storage._root import data_root

    while True:
        await asyncio.sleep(15 * 60)  # 15 minutes
        try:
            users_dir = data_root() / 'users'
            if not users_dir.exists():
                continue
            for user_dir in users_dir.iterdir():
                if not user_dir.is_dir() or user_dir.name.startswith(('_', '.')):
                    continue
                conv_dir = user_dir / 'conversation'
                if not conv_dir.exists():
                    continue
                for channel_dir in conv_dir.iterdir():
                    if not channel_dir.is_dir():
                        continue
                    user_slug = user_dir.name
                    channel = channel_dir.name
                    idle_minutes = cfg.marcel_idle_summarize_minutes
                    if is_idle(user_slug, channel, idle_minutes) and has_active_content(user_slug, channel):
                        log.info('%s-%s: background idle summarization triggered', user_slug, channel)
                        await summarize_active_segment(user_slug, channel, trigger='idle')
        except Exception:
            log.exception('background summarization loop error')


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info('main: starting Marcel v%s', __version__)

    # Seed default MARCEL.md and skills if not present
    from marcel_core.defaults import seed_defaults
    from marcel_core.storage._root import data_root

    seed_defaults(data_root())

    restart_task = asyncio.create_task(_restart_watcher())
    summarize_task = asyncio.create_task(_background_summarization_loop())
    start_sync_loop()
    scheduler.start()
    log.info('main: all background tasks started')
    yield
    scheduler.stop()
    stop_sync_loop()
    summarize_task.cancel()
    restart_task.cancel()
    log.info('main: shutdown complete')


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
app.include_router(artifacts_router)
app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(telegram_router)

# Serve the built web frontend (SPA) if it exists
_WEB_DIST = Path(__file__).resolve().parent.parent / 'web' / 'dist'
_API_PREFIXES = ('ws', 'health', 'conversations', 'telegram', 'api')

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
