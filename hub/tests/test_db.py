from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlmodel import SQLModel

from marcel_hub import db, models  # noqa: F401

TABLES = {
    'agent', 'device', 'conversation', 'message', 'task', 'task_event',
    'approval', 'artifact', 'schedule', 'usage_snapshot',
}  # fmt: skip


def test_upgrade_creates_every_table_on_an_empty_db(tmp_path: Path) -> None:
    engine = db.make_engine(tmp_path / 'new' / 'marcel.db')
    db.upgrade(engine)
    assert set(inspect(engine).get_table_names()) == TABLES | {'alembic_version'}


def test_downgrade_removes_every_table(tmp_path: Path) -> None:
    engine = db.make_engine(tmp_path / 'marcel.db')
    db.upgrade(engine)
    db.downgrade(engine)
    assert inspect(engine).get_table_names() == ['alembic_version']


def test_upgrade_is_repeatable_after_downgrade(tmp_path: Path) -> None:
    engine = db.make_engine(tmp_path / 'marcel.db')
    db.upgrade(engine)
    db.downgrade(engine)
    db.upgrade(engine)
    assert set(inspect(engine).get_table_names()) >= TABLES


def test_migrations_match_the_models(tmp_path: Path) -> None:
    """A model change without a revision fails here."""
    engine = db.make_engine(tmp_path / 'marcel.db')
    db.upgrade(engine)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), SQLModel.metadata)
    assert diff == []


def test_foreign_keys_are_enforced(engine, session) -> None:
    session.add(models.Conversation(agent_id='agt_missing', kind=models.ConversationKind.main))
    with pytest.raises(Exception, match='FOREIGN KEY'):
        session.commit()
