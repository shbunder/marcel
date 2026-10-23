"""Migration 0010 (S-04.15, app-api 0.7) on a database from before it.

A copy of a v1 hub's database (every revision up to 0009, with an agent, a conversation, messages,
a project and a task in it) goes up to 0010, down again and back up. Existing rows keep working at
each step, and the owner's agent gets the slug `marcel`."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from marcel_hub import db
from marcel_hub.app import Settings, create_app
from marcel_hub.auth import tokens
from marcel_hub.models import Agent, Device, Message, Project, TaskEvent, User

NOW = "'2026-10-12 00:00:00.000000'"
MENTION = {'kind': 'task', 'id': 'tsk_01J9ZQ5', 'title': 'Fix flaky login test'}  # app-api
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
        s.add(User(id='usr_owner', name='Shaun'))
        s.add(Message(id='msg_3', conversation_id='cnv_main', role='user', kind='text',
                      body_json={'text': '@Marcel yes'}, reply_to_message_id='msg_2',
                      mentions_json=[MENTION]))  # fmt: skip
        project = s.get(Project, 'prj_garden')
        assert project is not None
        project.tracker_url = 'https://github.com/shbunder/garden/issues'
        s.add(project)
        s.commit()
        reply = s.get(Message, 'msg_3')
        assert reply is not None and reply.reply_to_message_id == 'msg_2'
        assert reply.mentions_json == [MENTION]
        owner = s.get(User, 'usr_owner')
        assert owner is not None and owner.name == 'Shaun'


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
        s.add(User(id='usr_owner', name='Shaun'))
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


def test_0011_takes_workflow_events_and_its_downgrade_keeps_the_rest(tmp_path: Path) -> None:
    engine = v1(tmp_path / 'marcel.db')
    db.upgrade(engine, '0010')
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO task (id, agent_id, project_id, title, location, state, model, origin, "
            "created_at, updated_at) VALUES ('tsk_1', 'agt_marcel', 'prj_garden', 'T', 'nuc', "
            f"'working', 'sonnet', 'marcel', {NOW}, {NOW})"))  # fmt: skip
        conn.execute(text(
            "INSERT INTO task_event (task_id, seq, type, data_json, at) VALUES ('tsk_1', 1, "
            f"'text', '{{}}', {NOW})"))  # fmt: skip
    db.upgrade(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO task_event (task_id, seq, type, data_json, at) VALUES ('tsk_1', 2, "
            f"'workflow_agent_start', '{{}}', {NOW})"))  # fmt: skip
    with Session(engine) as s:
        assert [e.type.value for e in s.exec(select(TaskEvent))] == ['text', 'workflow_agent_start']
    db.downgrade(engine, '0010')
    with engine.connect() as conn:
        types = conn.execute(text('SELECT type FROM task_event')).scalars().all()
    assert types == ['text']


def test_0012_credits_every_agent_message_to_its_conversations_agent(tmp_path: Path) -> None:
    engine = v1(tmp_path / 'marcel.db')  # msg_1 from the owner, msg_2 from the agent
    db.upgrade(engine)
    with Session(engine) as s:
        written = {m.id: m.agent_id for m in s.exec(select(Message))}
    assert written == {'msg_1': None, 'msg_2': 'agt_marcel'}
    db.downgrade(engine, '0011')
    assert 'agent_id' not in columns(engine, 'message')
    db.upgrade(engine)
    assert 'agent_id' in columns(engine, 'message')


def test_0013_marks_no_existing_task_as_a_kickoff(tmp_path: Path) -> None:
    engine = v1(tmp_path / 'marcel.db')
    db.upgrade(engine, '0012')
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO task (id, agent_id, project_id, title, location, state, model, origin, "
            "created_at, updated_at) VALUES ('tsk_1', 'agt_marcel', 'prj_garden', 'T', 'nuc', "
            f"'working', 'sonnet', 'marcel', {NOW}, {NOW})"))  # fmt: skip
    db.upgrade(engine)
    with engine.connect() as conn:
        assert conn.execute(text('SELECT kickoff FROM task')).scalars().all() == [0]
    db.downgrade(engine, '0012')
    assert 'kickoff' not in columns(engine, 'task')
    with engine.connect() as conn:
        assert conn.execute(text('SELECT id FROM task')).scalars().all() == ['tsk_1']


def family_at_0013(path: Path) -> tuple[Engine, dict[str, str]]:
    """A 0.7 hub with people (S-17.1): the owner Shaun with Marcel and one phone, and Emma, a
    family member, with Pip and two phones. Returns the engine and each phone's token by name."""
    engine = v1(path)
    db.upgrade(engine, '0013')
    phones = {
        'shaun': ('usr_owner', tokens.new_token()),
        'emma-1': ('usr_emma', tokens.new_token()),
        'emma-2': ('usr_emma', tokens.new_token()),
    }
    with engine.begin() as conn:
        for sql in (
            'INSERT INTO user (id, name, role, created_at) VALUES '
            f"('usr_owner', 'Shaun', 'owner', {NOW}), ('usr_emma', 'Emma', 'member', {NOW})",
            "UPDATE agent SET main_conversation_id = 'cnv_main', slug = 'marcel'",
            'INSERT INTO agent (id, owner_user_id, name, animal, palette, seed, model_brain, '
            "model_worker, slug) VALUES ('agt_pip', 'usr_emma', 'Pip', 'fox', 'marcel', 'Pip', "
            "'opus', 'sonnet', 'pip')",
            "INSERT INTO conversation (id, agent_id, kind, created_at) VALUES ('cnv_pip', "
            f"'agt_pip', 'main', {NOW})",
            "UPDATE agent SET main_conversation_id = 'cnv_pip' WHERE id = 'agt_pip'",
            # Both people's event logs count from 1, and Emma has an unused pairing code.
            'INSERT INTO app_event (user_id, id, type, data_json, at) VALUES '
            f"('usr_owner', 1, 'message.created', '{{}}', {NOW}), "
            f"('usr_emma', 1, 'message.created', '{{}}', {NOW})",
            'INSERT INTO pairing_code (code_hash, user_id, created_at, expires_at) VALUES '
            f"('h_emma', 'usr_emma', {NOW}, {NOW})",
        ):
            conn.execute(text(sql))
        for name, (user, token) in phones.items():
            conn.execute(
                text(
                    'INSERT INTO device (id, user_id, name, token_hash, created_at) VALUES '
                    f'(:id, :user, :name, :hash, {NOW})'
                ),
                {
                    'id': f'dev_{name}',
                    'user': user,
                    'name': name,
                    'hash': tokens.hash_secret(token),
                },
            )
    return engine, {name: token for name, (_, token) in phones.items()}


def test_0014_folds_the_family_into_one_person_and_keeps_every_phone(tmp_path: Path) -> None:
    """S-22.11 (ADR-013): one user, both agents with their slugs, three phones whose tokens still
    work, each showing its old person's agent."""
    engine, phones = family_at_0013(tmp_path / 'marcel.db')
    db.upgrade(engine)
    assert 'role' not in columns(engine, 'user')
    with Session(engine) as s:
        assert [(u.id, u.name) for u in s.exec(select(User))] == [('usr_owner', 'Shaun')]
        agents = {a.id: (a.owner_user_id, a.slug) for a in s.exec(select(Agent))}
        assert agents == {'agt_marcel': ('usr_owner', 'marcel'), 'agt_pip': ('usr_owner', 'pip')}
        devices = {d.id: (d.user_id, d.agent_id) for d in s.exec(select(Device))}
    assert devices == {
        'dev_shaun': ('usr_owner', 'agt_marcel'),
        'dev_emma-1': ('usr_owner', 'agt_pip'),
        'dev_emma-2': ('usr_owner', 'agt_pip'),
    }
    with engine.connect() as conn:
        assert conn.execute(text('SELECT user_id, id FROM app_event')).all() == [('usr_owner', 1)]
        assert conn.execute(text('SELECT count(*) FROM pairing_code')).scalar_one() == 0
    engine.dispose()

    settings = Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db', owner_name='Shaun')
    with TestClient(create_app(settings)) as client:
        for name, token in phones.items():
            headers = {'Authorization': f'Bearer {token}'}
            res = client.get('/api/devices', headers=headers)
            assert res.status_code == 200, name
            mine = next(d for d in res.json() if d['current'])
            assert mine['agent_id'] == devices[f'dev_{name}'][1]
            me = client.get('/api/me', headers=headers).json()
            assert [a['id'] for a in me['agents']] == ['agt_marcel', 'agt_pip'], 'every buddy'


def test_0014_gives_a_phone_whose_person_has_no_live_agent_the_owners(tmp_path: Path) -> None:
    engine, _ = family_at_0013(tmp_path / 'marcel.db')
    with engine.begin() as conn:
        conn.execute(text(f"UPDATE agent SET archived_at = {NOW} WHERE id = 'agt_pip'"))
    db.upgrade(engine)
    with Session(engine) as s:
        assert {d.agent_id for d in s.exec(select(Device))} == {'agt_marcel'}


def test_0014_downgrade_gives_the_columns_back(tmp_path: Path) -> None:
    engine, _ = family_at_0013(tmp_path / 'marcel.db')
    db.upgrade(engine)
    db.downgrade(engine, '0013')
    assert 'agent_id' not in columns(engine, 'device')
    with engine.connect() as conn:
        assert conn.execute(text('SELECT id, role FROM user')).all() == [('usr_owner', 'owner')]
        assert conn.execute(text('SELECT count(*) FROM device')).scalar_one() == 3
    db.upgrade(engine)
    assert 'agent_id' in columns(engine, 'device')
