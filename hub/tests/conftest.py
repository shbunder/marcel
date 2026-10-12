from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session

from marcel_hub import db


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = db.make_engine(tmp_path / 'marcel.db')
    db.upgrade(engine)
    return engine


@pytest.fixture
def session(engine: Engine):
    with Session(engine) as s:
        yield s
