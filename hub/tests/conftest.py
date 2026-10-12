from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session

from marcel_hub import boards, db


@pytest.fixture(autouse=True)
def _quiet_boards(monkeypatch: pytest.MonkeyPatch) -> None:
    """The app's board keeper rebuilds boards in the background once a second. Tests count the
    app events they cause, so here it waits an hour; board tests call `flush` themselves."""
    monkeypatch.setattr(boards, 'TICK_S', 3600.0)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = db.make_engine(tmp_path / 'marcel.db')
    db.upgrade(engine)
    return engine


@pytest.fixture
def session(engine: Engine):
    with Session(engine) as s:
        yield s
