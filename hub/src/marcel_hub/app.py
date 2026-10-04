"""The hub's FastAPI app factory and its settings.

Settings come from a TOML file (`/data/marcel.toml`, or `MARCEL_CONFIG`) and then from the
environment, which wins. A missing file is fine: defaults apply. A broken file stops the hub at
start with a message that names the file.
"""

import os
import tomllib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from sqlalchemy import text
from sqlalchemy.engine import Engine

from marcel_hub import __version__, db

DEFAULT_CONFIG_PATH = Path('/data/marcel.toml')


class SettingsError(Exception):
    """The settings cannot be read. The message says which file or variable and what to do."""


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path('/data')
    db_path: Path = Path('/data/marcel.db')
    host: str = '0.0.0.0'
    port: int = 7420


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

    unknown = set(values) - {'data_dir', 'db_path', 'host', 'port'}
    if unknown:
        raise SettingsError(f'Unknown setting(s) {sorted(unknown)} in {config_path} or MARCEL_*.')
    try:
        data_dir = Path(values.get('data_dir', '/data'))
        return Settings(
            data_dir=data_dir,
            db_path=Path(values.get('db_path', data_dir / 'marcel.db')),
            host=str(values.get('host', '0.0.0.0')),
            port=int(values.get('port', 7420)),
        )
    except ValueError as exc:
        raise SettingsError(f'The port must be a number: {exc}.') from exc


def _is_setting(name: str) -> bool:
    return name in {'MARCEL_DATA_DIR', 'MARCEL_DB_PATH', 'MARCEL_HOST', 'MARCEL_PORT'}


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
    return {
        'ok': db_ok,
        'version': __version__,
        'runner': {
            'reachable': False,
            'reason': 'The hub has no runner connection yet, so it cannot see the NUC.',
        },
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    engine = db.make_engine(settings.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.upgrade(engine)
        yield
        engine.dispose()

    app = FastAPI(title='Marcel hub', version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.include_router(router)
    return app
