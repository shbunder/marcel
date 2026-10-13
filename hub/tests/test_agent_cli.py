"""`marcel-hub agent create|list`: the owner's agent, with its conversation, as the app sees it."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from marcel_hub import agent_cli, db
from marcel_hub.app import Settings, create_app, main
from marcel_hub.auth.tokens import OWNER_USER_ID, create_pairing_code
from marcel_hub.models import Agent, Conversation, Project


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv('MARCEL_CONFIG', str(tmp_path / 'none.toml'))
    monkeypatch.setenv('MARCEL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('MARCEL_BRAIN_CWD', '/home/owner/marcel/brain')
    return tmp_path


def run(argv: list[str], capsys: pytest.CaptureFixture[str], code: int = 0) -> str:
    with pytest.raises(SystemExit) as exit_info:
        main(['agent', *argv])
    assert exit_info.value.code == code
    return capsys.readouterr().out


def agents(env: Path) -> list[Agent]:
    engine = db.make_engine(env / 'marcel.db')
    try:
        with Session(engine) as s:
            return list(s.exec(select(Agent)).all())
    finally:
        engine.dispose()


def test_create_makes_the_owners_agent_that_the_app_sees(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = run(['create', '--name', 'Marcel'], capsys)
    assert 'Made Marcel' in out
    assert 'starts its brain in /home/owner/marcel/brain' in out
    [made] = agents(env)
    assert (made.owner_user_id, made.animal, made.palette, made.seed) == (
        OWNER_USER_ID,
        'giraffe',
        'marcel',
        'Marcel',
    )
    assert made.persona_path == '/home/owner/marcel/brain/CLAUDE.md'

    engine = db.make_engine(env / 'marcel.db')
    with Session(engine) as s:
        conv = s.get(Conversation, made.main_conversation_id)
        assert conv is not None and conv.agent_id == made.id
        assert s.exec(select(Project).where(Project.agent_id == made.id)).first() is not None
        code = create_pairing_code(s)
    engine.dispose()

    settings = Settings(data_dir=env, db_path=env / 'marcel.db')
    with TestClient(create_app(settings)) as client:
        token = client.post('/api/pair', json={'code': code, 'device_name': 'iPhone'}).json()
        me = client.get('/api/me', headers={'Authorization': f'Bearer {token["token"]}'}).json()
    assert [(a['id'], a['name'], a['main_conversation_id']) for a in me['agents']] == [
        (made.id, 'Marcel', made.main_conversation_id)
    ]


def test_create_twice_leaves_one_agent(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(['create'], capsys)
    out = run(['create', '--name', 'Marcel', '--animal', 'owl'], capsys)
    assert 'already exists' in out
    [only] = agents(env)
    assert only.animal == 'giraffe'


def test_a_second_agent_needs_another(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Each agent gets its own brain in the same workspace: a mistyped name must not make one."""
    run(['create'], capsys)
    out = run(['create', '--name', 'marcel'], capsys, code=1)
    assert 'already has Marcel' in out and '--another' in out
    assert [a.name for a in agents(env)] == ['Marcel']
    run(['create', '--name', 'Juno', '--another'], capsys)
    assert sorted(a.name for a in agents(env)) == ['Juno', 'Marcel']


def test_the_name_defaults_to_the_agent_name_the_brain_was_rendered_with(
    env: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('MARCEL_AGENT_NAME', 'Juno')
    run(['create'], capsys)
    assert [a.name for a in agents(env)] == ['Juno']


def test_the_brain_keeper_starts_a_brain_for_the_agent_create_made(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The path production takes: the CLI writes the database, the hub's keeper (another process,
    here another engine) finds the agent on its next check and has the runner start its brain."""
    from test_brain import FakeBrains
    from test_tasks_fakes import run as run_async

    from marcel_hub.brain import BrainKeeper, brain_name

    engine = db.make_engine(env / 'marcel.db')
    db.upgrade(engine)
    fake = FakeBrains()
    keeper = BrainKeeper(engine, fake, cwd='/home/owner/marcel/brain')
    run_async(keeper.check())
    assert fake.spawned == []  # no agent, no brain

    run(['create'], capsys)
    run_async(keeper.check())
    [made] = agents(env)
    assert [(b['name'], b['cwd']) for b in fake.spawned] == [
        (brain_name(made.id), '/home/owner/marcel/brain')
    ]
    with Session(engine) as s:
        conv = s.get(Conversation, made.main_conversation_id)
        assert conv is not None and conv.brain_session_id is not None
        assert len(s.exec(select(Conversation)).all()) == 1  # the keeper made no second one
    engine.dispose()


def test_list_shows_the_agents(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert 'No agents yet' in run(['list'], capsys)
    run(['create', '--name', 'Juno', '--seed', 'j1'], capsys)
    out = run(['list'], capsys)
    assert 'Juno' in out and 'giraffe/marcel' in out


def test_create_says_when_no_brain_will_start(
    env: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv('MARCEL_BRAIN_CWD')
    assert 'will not start its brain' in run(['create'], capsys)
    assert agents(env)[0].persona_path is None


def test_refuses_on_broken_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / 'marcel.toml'
    cfg.write_text('port = = 1')
    monkeypatch.setenv('MARCEL_CONFIG', str(cfg))
    with pytest.raises(SystemExit, match=r'Nothing was changed.*marcel\.toml'):
        agent_cli.agent(['create'])
