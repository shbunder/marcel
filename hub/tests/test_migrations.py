"""Migration 0010 (S-04.15, app-api 0.7) on a database from before it.

A copy of a v1 hub's database (every revision up to 0009, with an agent, a conversation, messages,
a project and a task in it) goes up to 0010, down again and back up. Existing rows keep working at
each step, and the owner's agent gets the slug `marcel`."""

from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from marcel_hub import db
from marcel_hub.models import Agent, Message, Project, User, UserRole

NOW = "'2026-10-12 00:00:00.000000'"
NEW_AGENT = {'look_animal', 'look_color', 'look_personality', 'onboarded_at', 'archived_at', 'slug'}


def v1(path: Path) -> Engine:
    """A hub database as 0.6 left it, with rows in it."""
    engine = db.make_engine(path)
    db.upgrade(engine, '0009')
    with engine.begin() as conn:
        for sql in (
            'INSERT INTO agent (id, owner_user_id, name, animal, palette, seed, model_brain, '
            "model_worker) VALUES ('agt_marcel', 'usr_owner', 'Marcel', 'giraffe', 'pink', '1', "
            "'opus', 'sonnet')",
            "INSERT INTO conversation (id, agent_id, kind, created_at) VALUES ('cnv_main', "
            f"'agt_marcel', 'main', {NOW})",
            'INSERT INTO project (id, agent_id, slug, name, board_artifact_id, archived, '
            "created_at, updated_at) VALUES ('prj_garden', 'agt_marcel', 'garden', 'Garden', "
            f"'art_b', 0, {NOW}, {NOW})",
            'INSERT INTO message (id, conversation_id, role, kind, body_json, created_at) VALUES '
            f"('msg_1', 'cnv_main', 'user', 'text', '{{\"text\": \"Hi\"}}', {NOW})",
            'INSERT INTO message (id, conversation_id, role, kind, body_json, created_at) VALUES '
            f"('msg_2', 'cnv_main', 'agent', 'text', '{{\"text\": \"Hello\"}}', {NOW})",
        ):
            conn.execute(text(sql))
    return engine


def columns(engine: Engine, table: str) -> set[str]:
    return {c['name'] for c in inspect(engine).get_columns(table)}


def test_upgrade_keeps_the_rows_and_names_the_owners_agent(tmp_path: Path) -> None:
    engine = v1(tmp_path / 'marcel.db')
    db.upgrade(engine)
    assert 'user' in inspect(engine).get_table_names()
    assert columns(engine, 'agent') >= NEW_AGENT
    with Session(engine) as s:
        agent = s.get(Agent, 'agt_marcel')
        assert agent is not None and agent.name == 'Marcel'
        assert agent.slug == 'marcel'
        assert (agent.look_animal, agent.onboarded_at, agent.archived_at) == (None, None, None)
        old = s.get(Message, 'msg_1')
        assert old is not None and old.body_json == {'text': 'Hi'}
        assert (old.reply_to_message_id, old.mentions_json) == (None, [])
        project = s.get(Project, 'prj_garden')
        assert project is not None and project.tracker_url is None
        assert s.exec(select(User)).all() == [], 'no rows made by the migration (S-17.1 does)'


def test_the_new_columns_work_after_the_upgrade(tmp_path: Path) -> None:
    engine = v1(tmp_path / 'marcel.db')
    db.upgrade(engine)
    with Session(engine) as s:
        s.add(User(id='usr_owner', name='Shaun', role=UserRole.owner))
        s.add(Message(id='msg_3', conversation_id='cnv_main', role='user', kind='text',
                      body_json={'text': '@Marcel yes'}, reply_to_message_id='msg_2',
                      mentions_json=[{'agent_id': 'agt_marcel'}]))  # fmt: skip
        project = s.get(Project, 'prj_garden')
        assert project is not None
        project.tracker_url = 'https://github.com/shbunder/garden/issues'
        s.add(project)
        s.commit()
        reply = s.get(Message, 'msg_3')
        assert reply is not None and reply.reply_to_message_id == 'msg_2'
        assert reply.mentions_json == [{'agent_id': 'agt_marcel'}]
        owner = s.get(User, 'usr_owner')
        assert owner is not None and owner.role is UserRole.owner


def test_a_reply_must_point_at_a_real_message(tmp_path: Path) -> None:
    engine = v1(tmp_path / 'marcel.db')
    db.upgrade(engine)
    with Session(engine) as s:
        s.add(Message(conversation_id='cnv_main', role='user', kind='text', body_json={},
                      reply_to_message_id='msg_missing'))  # fmt: skip
        with pytest.raises(IntegrityError, match='FOREIGN KEY'):
            s.commit()


def test_an_agent_slug_is_unique(tmp_path: Path) -> None:
    engine = v1(tmp_path / 'marcel.db')
    db.upgrade(engine)
    with Session(engine) as s:
        s.add(Agent(owner_user_id='usr_kid', name='Pip', animal='frog', palette='green', seed='2',
                    slug='marcel'))  # fmt: skip
        with pytest.raises(IntegrityError, match='UNIQUE'):
            s.commit()


def test_only_the_owners_first_agent_is_marcel(tmp_path: Path) -> None:
    engine = db.make_engine(tmp_path / 'marcel.db')
    db.upgrade(engine, '0009')
    with engine.begin() as conn:
        for agent_id, owner in (('agt_a', 'usr_owner'), ('agt_b', 'usr_owner'), ('agt_c', 'usr_x')):
            conn.execute(text(
                "INSERT INTO agent (id, owner_user_id, name, animal, palette, seed, model_brain, "
                f"model_worker) VALUES ('{agent_id}', '{owner}', 'A', 'cat', 'red', '1', 'opus', "
                "'sonnet')"))  # fmt: skip
    db.upgrade(engine)
    with Session(engine) as s:
        slugs = {a.id: a.slug for a in s.exec(select(Agent)).all()}
    assert slugs == {'agt_a': 'marcel', 'agt_b': None, 'agt_c': None}


def test_downgrade_and_upgrade_again_keep_the_rows(tmp_path: Path) -> None:
    engine = v1(tmp_path / 'marcel.db')
    db.upgrade(engine)
    with Session(engine) as s:
        s.add(Message(id='msg_3', conversation_id='cnv_main', role='user', kind='text',
                      body_json={'text': 'Yes'}, reply_to_message_id='msg_2'))  # fmt: skip
        s.add(User(id='usr_owner', name='Shaun', role=UserRole.owner))
        s.commit()
    db.downgrade(engine, '0009')
    assert 'user' not in inspect(engine).get_table_names()
    assert not columns(engine, 'agent') & NEW_AGENT
    assert not columns(engine, 'message') & {'reply_to_message_id', 'mentions_json'}
    assert 'tracker_url' not in columns(engine, 'project')
    with engine.connect() as conn:
        assert conn.execute(text('PRAGMA foreign_key_check')).all() == []
        assert conn.execute(text('PRAGMA foreign_keys')).scalar_one() == 1
        ids = conn.execute(text('SELECT id FROM message ORDER BY id')).scalars().all()
        assert ids == ['msg_1', 'msg_2', 'msg_3']
        assert conn.execute(text('SELECT name FROM agent')).scalar_one() == 'Marcel'
    db.upgrade(engine)
    with Session(engine) as s:
        agent = s.get(Agent, 'agt_marcel')
        assert agent is not None and agent.slug == 'marcel'
        again = s.get(Message, 'msg_3')
        assert again is not None and again.reply_to_message_id is None  # dropped with the column
        assert again.body_json == {'text': 'Yes'}
