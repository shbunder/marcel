import asyncio
import logging
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
from marcel_core.api.components import router as components_router
from marcel_core.api.conversations import router as conversations_router
from marcel_core.api.health import router as health_router
from marcel_core.jobs.scheduler import scheduler
from marcel_core.plugin import get_channel, list_channels
from marcel_core.plugin.channels import discover as discover_channels

log = logging.getLogger(__name__)


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


def _log_zoo_summary() -> None:
    """Log the resolved MARCEL_ZOO_DIR and on-disk habitat counts.

    Counts are on-disk directory counts, not post-discovery registrations, so
    the line tells the truth about what's available even if a specific habitat
    failed to load. First-boot operators who forget ``make zoo-setup`` get a
    WARNING pointing at the fix — the difference between "Marcel is running"
    and "Marcel is running but has zero habitats" is easy to miss otherwise.
    """
    zoo_dir = settings.zoo_dir
    if zoo_dir is None:
        log.warning(
            'main: MARCEL_ZOO_DIR is unset — Marcel has zero habitats. '
            'Run `make zoo-setup` (host) + `make zoo-docker-deps` (container) to install.'
        )
        return
    if not zoo_dir.is_dir():
        log.warning(
            'main: MARCEL_ZOO_DIR=%s does not exist — Marcel has zero habitats. '
            'Run `make zoo-setup` (host) + `make zoo-docker-deps` (container) to install.',
            zoo_dir,
        )
        return

    counts: dict[str, int] = {}
    for kind in ('channels', 'integrations', 'skills', 'jobs', 'agents'):
        subdir = zoo_dir / kind
        if not subdir.is_dir():
            counts[kind] = 0
            continue
        counts[kind] = sum(1 for entry in subdir.iterdir() if entry.is_dir() and not entry.name.startswith(('_', '.')))

    summary = ' '.join(f'{k}={v}' for k, v in counts.items())
    if sum(counts.values()) == 0:
        log.warning(
            'main: zoo at %s is empty (%s). Run `make zoo-setup` (host) + `make zoo-docker-deps` (container).',
            zoo_dir,
            summary,
        )
    else:
        log.info('main: zoo at %s — %s', zoo_dir, summary)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info('main: starting Marcel v%s', __version__)

    from marcel_core.plugin import _uds_supervisor
    from marcel_core.skills.integrations import discover as discover_integrations

    # Populate integration handlers and habitat metadata before the scheduler
    # starts — rebuild_schedule() → _ensure_habitat_jobs() reads _metadata to
    # decide which habitat:* jobs to materialize and which to treat as orphan.
    # Skipping this means every habitat-scheduled job is deleted on cold start.
    # Discovery also spawns any UDS-isolated habitats (ISSUE-f60b09).
    discover_integrations()
    _uds_supervisor.start_supervisor()
    _log_zoo_summary()

    summarize_task = asyncio.create_task(_background_summarization_loop())
    scheduler.start()
    log.info('main: all background tasks started')
    yield
    scheduler.stop()
    summarize_task.cancel()
    await _uds_supervisor.stop_supervisor()
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
app.include_router(components_router)
app.include_router(conversations_router)

# Discover external channel habitats from <MARCEL_ZOO_DIR>/channels/
# before mounting — each habitat's __init__.py calls register_channel()
# at import time. The kernel ships no channel habitats; Telegram and any
# future Signal/Discord channels live exclusively in the zoo. No-op when
# MARCEL_ZOO_DIR is unset — server still boots with WebSocket + REST.
discover_channels()

# Mount every registered channel plugin's router. This replaces the
# previous hard-coded `app.include_router(telegram_router)` — any channel
# habitat (kernel-bundled today, zoo-hosted tomorrow) that exposes a
# router is discovered here. Plugins without a router (e.g. a future
# signal-only channel) are silently skipped.
for _channel_name in list_channels():
    _plugin = get_channel(_channel_name)
    if _plugin is not None and _plugin.router is not None:
        app.include_router(_plugin.router)
        log.info('main: mounted channel plugin %r', _channel_name)

# Serve the built web frontend (SPA) if it exists
_WEB_DIST = Path(__file__).resolve().parent.parent / 'web' / 'dist'
_API_PREFIXES = ('ws', 'health', 'conversations', 'telegram', 'api')
# File extensions that are never valid SPA routes — reject with 404 to avoid
# making the server look interesting to automated vulnerability scanners.
_PROBE_EXTENSIONS = frozenset(
    (
        '.php',
        '.asp',
        '.aspx',
        '.jsp',
        '.cgi',
        '.env',
        '.xml',
        '.sql',
        '.bak',
        '.old',
        '.orig',
        '.swp',
        '.config',
        '.ini',
        '.log',
    )
)

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
        # Reject paths with file extensions commonly targeted by scanners
        if path:
            dot = path.rfind('.')
            if dot != -1 and path[dot:].lower() in _PROBE_EXTENSIONS:
                from fastapi import HTTPException

                raise HTTPException(status_code=404)
        return FileResponse(str(_WEB_DIST / 'index.html'))
