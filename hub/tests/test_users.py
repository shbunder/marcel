"""People (S-17.1, B-37, B-39): the owner, the members they add, and `marcel-hub setup`."""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from sqlmodel import Session, select
from test_contract import served_contract
from test_contract_routes import RETIRED_OPERATIONS

from marcel_hub import db
from marcel_hub.app import Settings, create_app, main
from marcel_hub.auth import tokens
from marcel_hub.models import Agent, Task, TaskLocation, TaskOrigin, TaskState, User, UserRole

CONTRACT = Path(__file__).resolve().parents[2] / 'contracts' / 'app-api.yaml'
SPEC = served_contract(yaml.safe_load(CONTRACT.read_text()))  # S-02.13: as the hub serves it
REGISTRY = Registry().with_resource(
    'urn:contract',
    Resource.from_contents(SPEC | {'$schema': 'https://json-schema.org/draft/2020-12/schema'}),
)
EMMA = {
    'name': 'Emma',
    'agent': {
        'name': 'Pip',
        'look': {'animal': 'fox', 'color': 'tangerine', 'personality': 'cheerful'},
    },
}


def validate(schema: str, body: Any) -> None:
    Draft202012Validator(
        {'$ref': f'urn:contract#/components/schemas/{schema}'}, registry=REGISTRY
    ).validate(body)


def owner_only_operations() -> list[tuple[str, str]]:
    """The people operations the hub still guards with `require_owner`. app-api 0.8.0 (S-02.13)
    removed them and their owner-only mark from the contract; until S-22.11 removes them here they
    are `test_contract_routes.RETIRED_OPERATIONS`, and the guard keeps its test."""
    return [
        (method, re.sub(r'\{\}', '{user_id}', path.removeprefix('/api')))
        for method, path in RETIRED_OPERATIONS
    ]


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db', owner_name='Shaun')
    with TestClient(create_app(settings)) as c:
        yield c


def engine(client: TestClient) -> Any:
    return client.app.state.engine  # type: ignore[attr-defined]


def pair_with(client: TestClient, code: str, name: str = 'iPhone') -> dict[str, Any]:
    res = client.post('/api/pair', json={'code': code, 'device_name': name})
    assert res.status_code == 201, res.text
    return res.json()


def owner_phone(client: TestClient) -> dict[str, str]:
    with Session(engine(client)) as s:
        code = tokens.create_pairing_code(s)
    return {'Authorization': f'Bearer {pair_with(client, code)["token"]}'}


def add_emma(client: TestClient, owner: dict[str, str]) -> dict[str, Any]:
    res = client.post('/api/users', json=EMMA, headers=owner)
    assert res.status_code == 201, res.text
    return res.json()


def member_phone(client: TestClient, owner: dict[str, str], user_id: str) -> dict[str, str]:
    issued = client.post(f'/api/users/{user_id}/pairing-codes', headers=owner)
    assert issued.status_code == 201, issued.text
    paired = pair_with(client, issued.json()['code'], "Emma's iPhone")
    return {'Authorization': f'Bearer {paired["token"]}'}


# --- the owner --------------------------------------------------------------------------------


def test_the_owner_row_is_made_at_start_up(client: TestClient) -> None:
    with Session(engine(client)) as s:
        owner = s.get(User, tokens.OWNER_USER_ID)
    assert owner is not None and (owner.name, owner.role) == ('Shaun', UserRole.owner)
    me = client.get('/api/me', headers=owner_phone(client)).json()
    assert (me['user_id'], me['name'], me['role']) == (tokens.OWNER_USER_ID, 'Shaun', 'owner')
    validate('Me', me)


def test_the_owner_lists_people_owner_first(client: TestClient) -> None:
    owner = owner_phone(client)
    add_emma(client, owner)
    people = client.get('/api/users', headers=owner).json()
    assert [(p['name'], p['role']) for p in people] == [('Shaun', 'owner'), ('Emma', 'member')]
    emma = people[1]
    assert emma['agent']['name'] == 'Pip'
    assert emma['agent']['look'] == EMMA['agent']['look']
    assert (emma['device_count'], emma['quests_this_week']) == (0, 0)
    assert people[0]['device_count'] == 1


def test_adding_a_person_makes_their_own_agent_and_conversation(client: TestClient) -> None:
    emma = add_emma(client, owner_phone(client))
    with Session(engine(client)) as s:
        [pip] = s.exec(select(Agent).where(Agent.owner_user_id == emma['id'])).all()
    assert (pip.name, pip.slug, pip.look_animal, pip.animal) == ('Pip', 'pip', 'fox', 'fox')
    assert pip.main_conversation_id is not None
    assert emma['agent']['id'] == pip.id


def test_a_new_person_is_checked_against_the_contract(client: TestClient) -> None:
    owner = owner_phone(client)
    bad = {
        'name': 'Emma',
        'agent': {
            'name': 'Pip',
            'look': {'animal': 'dragon', 'color': 'rose', 'personality': 'shy'},
        },
    }
    res = client.post('/api/users', json=bad, headers=owner)
    assert res.status_code == 400 and res.json()['code'] == 'bad_request'


# --- members ----------------------------------------------------------------------------------


@pytest.mark.parametrize(('method', 'path'), owner_only_operations())
def test_a_member_gets_403_on_every_owner_only_operation(
    client: TestClient, method: str, path: str
) -> None:
    owner = owner_phone(client)
    emma = add_emma(client, owner)
    member = member_phone(client, owner, emma['id'])
    url = '/api' + re.sub(r'\{[^}]+\}', emma['id'], path)
    res = client.request(method, url, headers=member, json=EMMA if method == 'POST' else None)
    assert res.status_code == 403, f'{method} {path} let a member through: {res.text}'
    body = res.json()
    assert body['code'] == 'owner_only'
    assert body['message'] == 'Only Shaun can do this. Ask Shaun to do it.'


def test_the_four_people_operations_are_owner_only() -> None:
    assert sorted(owner_only_operations()) == [
        ('DELETE', '/users/{user_id}'),
        ('GET', '/users'),
        ('POST', '/users'),
        ('POST', '/users/{user_id}/pairing-codes'),
    ]


def test_a_phone_paired_with_a_members_code_belongs_to_that_member(client: TestClient) -> None:
    owner = owner_phone(client)
    emma = add_emma(client, owner)
    issued = client.post(f'/api/users/{emma["id"]}/pairing-codes', headers=owner).json()
    validate('IssuedPairingCode', issued)
    assert issued['link'].startswith('marcel://pair?hub=')
    paired = pair_with(client, issued['code'])
    assert paired['user_id'] == emma['id']
    me = client.get('/api/me', headers={'Authorization': f'Bearer {paired["token"]}'}).json()
    validate('Me', me)
    assert (me['user_id'], me['name'], me['role']) == (emma['id'], 'Emma', 'member')
    assert [a['name'] for a in me['agents']] == ['Pip']


def test_a_pairing_code_for_nobody_is_404(client: TestClient) -> None:
    res = client.post('/api/users/usr_nobody/pairing-codes', headers=owner_phone(client))
    assert res.status_code == 404 and res.json()['code'] == 'user_not_found'


# --- removing ---------------------------------------------------------------------------------


def test_removing_a_person_signs_out_their_phones_at_once(client: TestClient) -> None:
    owner = owner_phone(client)
    emma = add_emma(client, owner)
    member = member_phone(client, owner, emma['id'])
    assert client.get('/api/me', headers=member).status_code == 200
    assert client.delete(f'/api/users/{emma["id"]}', headers=owner).status_code == 204
    assert client.get('/api/me', headers=member).status_code == 401
    assert [p['name'] for p in client.get('/api/users', headers=owner).json()] == ['Shaun']
    with Session(engine(client)) as s:
        [pip] = s.exec(select(Agent).where(Agent.owner_user_id == emma['id'])).all()
    assert pip.archived_at is not None  # their chats and notes stay; the helper is archived


def test_removing_a_person_stops_their_quests(client: TestClient) -> None:
    owner = owner_phone(client)
    emma = add_emma(client, owner)
    with Session(engine(client)) as s:
        task = Task(
            agent_id=emma['agent']['id'],
            title='Homework plan',
            location=TaskLocation.nuc,
            state=TaskState.queued,
            model='sonnet',
            origin=TaskOrigin.marcel,
        )
        s.add(task)
        s.commit()
        task_id = task.id
    assert client.delete(f'/api/users/{emma["id"]}', headers=owner).status_code == 204
    with Session(engine(client)) as s:
        stopped = s.get(Task, task_id)
    assert stopped is not None and stopped.state == TaskState.stopped


def test_removing_a_person_signs_them_out_even_when_a_quest_cannot_be_stopped(
    client: TestClient,
) -> None:
    owner = owner_phone(client)
    emma = add_emma(client, owner)
    member = member_phone(client, owner, emma['id'])
    with Session(engine(client)) as s:
        s.add(
            Task(
                agent_id=emma['agent']['id'],
                title='Homework plan',
                location=TaskLocation.nuc,
                state=TaskState.working,
                model='sonnet',
                origin=TaskOrigin.marcel,
                session_id='ses_1',
            )
        )
        s.commit()

    async def runner_down(task_id: str, by: str = 'you') -> None:
        raise ConnectionError('the runner is not answering')

    client.app.state.tasks.stop = runner_down  # type: ignore[attr-defined]
    assert client.delete(f'/api/users/{emma["id"]}', headers=owner).status_code == 204
    assert client.get('/api/me', headers=member).status_code == 401
    assert [p['name'] for p in client.get('/api/users', headers=owner).json()] == ['Shaun']


@pytest.mark.parametrize('blank', [{'name': '   '}, {'agent': {'name': ' '}}])
def test_a_blank_name_is_refused(client: TestClient, blank: dict[str, Any]) -> None:
    body = EMMA | blank
    res = client.post('/api/users', json=body, headers=owner_phone(client))
    assert res.status_code == 400 and res.json()['code'] == 'bad_request'
    assert 'at least one letter' in res.json()['message']


def test_the_owner_cannot_be_removed(client: TestClient) -> None:
    owner = owner_phone(client)
    res = client.delete(f'/api/users/{tokens.OWNER_USER_ID}', headers=owner)
    assert res.status_code == 409 and res.json()['code'] == 'cannot_remove_owner'
    assert client.get('/api/me', headers=owner).status_code == 200


def test_removing_nobody_is_404(client: TestClient) -> None:
    res = client.delete('/api/users/usr_nobody', headers=owner_phone(client))
    assert res.status_code == 404 and res.json()['code'] == 'user_not_found'


# --- marcel-hub setup -------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv('MARCEL_CONFIG', str(tmp_path / 'none.toml'))
    monkeypatch.setenv('MARCEL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('MARCEL_OWNER_NAME', 'Shaun')
    monkeypatch.setenv('MARCEL_BRAIN_CWD', '/home/owner/marcel/brain')
    return tmp_path


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    assert exit_info.value.code == 0
    return capsys.readouterr().out


def rows(env: Path) -> tuple[list[User], list[Agent]]:
    eng = db.make_engine(env / 'marcel.db')
    try:
        with Session(eng) as s:
            return list(s.exec(select(User)).all()), list(s.exec(select(Agent)).all())
    finally:
        eng.dispose()


def test_setup_twice_leaves_one_owner_and_one_agent(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = run(['setup'], capsys)
    assert 'Made Marcel' in first and 'Scan this with the Marcel app' in first
    second = run(['setup'], capsys)
    assert 'already here' in second
    users, agents = rows(env)
    assert [(u.id, u.name, u.role) for u in users] == [
        (tokens.OWNER_USER_ID, 'Shaun', UserRole.owner)
    ]
    [marcel] = agents
    assert (marcel.slug, marcel.look_animal, marcel.look_color, marcel.look_personality) == (
        'marcel',
        'giraffe',
        'rose',
        'professional',
    )
    assert marcel.main_conversation_id is not None
    assert marcel.persona_path == '/home/owner/marcel/brain/CLAUDE.md'


def test_setup_reuses_the_agent_that_agent_create_made(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(['agent', 'create', '--name', 'Marcel'], capsys)
    [made] = rows(env)[1]
    assert made.slug == 'marcel'  # agent create names the owner's first agent `marcel` (S-17.7)
    assert 'already here' in run(['setup'], capsys)
    [after] = rows(env)[1]
    assert after.id == made.id
    assert (after.slug, after.look_animal) == ('marcel', 'giraffe')


def test_setup_pairs_only_the_first_phone(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(['setup'], capsys)
    eng = db.make_engine(env / 'marcel.db')
    with TestClient(create_app(Settings(data_dir=env, db_path=env / 'marcel.db'))) as c:
        owner_phone(c)
    eng.dispose()
    out = run(['setup'], capsys)
    assert 'Scan this' not in out
    assert '1 paired phone(s). Pair another with: marcel-hub pair' in out
