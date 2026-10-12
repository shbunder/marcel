"""Engine, sessions and migrations. The schema changes only through Alembic revisions."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import create_engine

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / 'migrations'


def make_engine(db_path: Path | str) -> Engine:
    """A SQLite engine with foreign keys on. `:memory:` is not supported: migrations need a file."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f'sqlite:///{path}')

    @event.listens_for(engine, 'connect')
    def _pragmas(dbapi_connection, _record) -> None:  # pyright: ignore[reportUnusedFunction]
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    return engine


def alembic_config(engine: Engine) -> Config:
    cfg = Config()
    cfg.set_main_option('script_location', str(MIGRATIONS_DIR))
    cfg.attributes['engine'] = engine
    return cfg


def upgrade(engine: Engine, revision: str = 'head') -> None:
    command.upgrade(alembic_config(engine), revision)


def downgrade(engine: Engine, revision: str = 'base') -> None:
    command.downgrade(alembic_config(engine), revision)
