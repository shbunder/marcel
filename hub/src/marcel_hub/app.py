"""The hub's FastAPI app factory and its settings.

Settings come from a TOML file (`/data/marcel.toml`, or `MARCEL_CONFIG`) and then from the
environment, which wins. A missing file is fine: defaults apply. A broken file stops the hub at
start with a message that names the file.
"""

import os
import sys
import tomllib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from sqlalchemy import text
from sqlalchemy.engine import Engine

from marcel_hub import __version__, auth, conversations, db, tasks, ws
from marcel_hub.runner_client import DEFAULT_SOCKET, OffsetStore, RunnerClient, RunnerWatch

DEFAULT_CONFIG_PATH = Path('/data/marcel.toml')


class SettingsError(Exception):
    """The settings cannot be read. The message says which file or variable and what to do."""


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path('/data')
    db_path: Path = Path('/data/marcel.db')
    host: str = '0.0.0.0'
    port: int = 7420
    owner_name: str = 'Owner'
    # None: no runner (tests). `load_settings` defaults it to the socket mounted into the hub.
    runner_sock: Path | None = None
    worker_cwd: str | None = None


def load_settings(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ) if env is None else env
    config_path = Path(env.get('MARCEL_CONFIG', DEFAULT_CONFIG_PATH))
    values: dict[str, Any] = {}
    if config_path.exists():
        try:
            values = tomllib.loads(config_path.read_text())
        except (tomllib.TOMLDecodeError, OSError) as exc:
            raise SettingsError(
                f'Cannot read {config_path}: {exc}. Fix the file or remove it.'
            ) from exc
    values = {
        **values,
        **{k[len('MARCEL_') :].lower(): v for k, v in env.items() if _is_setting(k)},
    }

    known = {'data_dir', 'db_path', 'host', 'port', 'owner_name', 'runner_sock', 'worker_cwd'}
    unknown = set(values) - known
    if unknown:
        raise SettingsError(f'Unknown setting(s) {sorted(unknown)} in {config_path} or MARCEL_*.')
    try:
        data_dir = Path(values.get('data_dir', '/data'))
        return Settings(
            data_dir=data_dir,
            db_path=Path(values.get('db_path', data_dir / 'marcel.db')),
            host=str(values.get('host', '0.0.0.0')),
            port=int(values.get('port', 7420)),
            owner_name=str(values.get('owner_name', 'Owner')),
            runner_sock=Path(values.get('runner_sock', DEFAULT_SOCKET)),
            worker_cwd=str(values['worker_cwd']) if values.get('worker_cwd') else None,
        )
    except ValueError as exc:
        raise SettingsError(f'The port must be a number: {exc}.') from exc
    except TypeError as exc:
        raise SettingsError(
            f'A setting in {config_path} has the wrong type: {exc}. Use text for paths.'
        ) from exc


def _is_setting(name: str) -> bool:
    return name in {
        'MARCEL_DATA_DIR',
        'MARCEL_DB_PATH',
        'MARCEL_HOST',
        'MARCEL_PORT',
        'MARCEL_OWNER_NAME',
        'MARCEL_RUNNER_SOCK',
        'MARCEL_WORKER_CWD',
    }


router = APIRouter(prefix='/api')


@router.get('/health')
def health(request: Request) -> dict[str, Any]:
    engine: Engine = request.app.state.engine
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        db_ok = True
    except Exception:  # health must answer, whatever is wrong
        db_ok = False
    watch: RunnerWatch | None = request.app.state.runner_watch
    if watch is None:
        runner = {
            'reachable': False,
            'reason': 'The hub has no runner set up, so it cannot see the NUC.',
        }
    elif watch.reachable:
        runner = {'reachable': True}
    else:
        runner = {'reachable': False, 'reason': watch.reason}
    return {'ok': db_ok, 'version': __version__, 'runner': runner}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    engine = db.make_engine(settings.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.upgrade(engine)
        await app.state.tasks.drain()  # tasks queued before a restart start if there is room
        if watch is not None:
            watch.start()
        yield
        if watch is not None:
            await watch.close()
            await watch.client.aclose()
        engine.dispose()

    # No /docs, /redoc or /openapi.json: every route but /health and /pair needs a token.
    app = FastAPI(
        title='Marcel hub',
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.include_router(router)
    auth.install(app)
    # The runner client (S-04.5) replaces the registry's NoRunner when a socket is configured.
    client = (
        RunnerClient(settings.runner_sock, worker_cwd=settings.worker_cwd)
        if settings.runner_sock
        else None
    )
    # One event log for the app's live stream (S-04.3); the task registry publishes into it.
    app.state.events = ws.EventLog(engine)
    registry = tasks.install(app, runner=client, publisher=ws.OwnerPublisher(app.state.events))
    watch = (
        RunnerWatch(
            client, registry, offsets=OffsetStore(settings.data_dir / 'runner-offsets.json')
        )
        if client
        else None
    )
    app.state.runner_watch = watch
    ws.install(app)
    app.include_router(conversations.router)
    return app


def main(argv: list[str] | None = None) -> None:
    """`marcel-hub`: start the hub on the configured host and port.

    `marcel-hub pair` prints a QR code that pairs a phone instead.
    """
    import uvicorn

    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ['pair']:
        from marcel_hub.auth.cli import pair

        raise SystemExit(pair(args[1:]))

    try:
        settings = load_settings()
    except SettingsError as exc:
        raise SystemExit(f'Marcel hub did not start. {exc}') from exc
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
