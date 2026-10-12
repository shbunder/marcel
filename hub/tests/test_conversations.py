"""Main and side conversations, messages by cursor, threads and send-to-main (B-01, B-02, B-08).

The helpers at the top are shared with `test_ws.py`.
"""

import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from sqlmodel import Session, select

from marcel_hub import conversations
from marcel_hub.app import Settings, create_app
from marcel_hub.auth import tokens
from marcel_hub.models import (
    Agent,
    AppEvent,
    Device,
    Message,
    MessageKind,
    MessageRole,
)

CONTRACT = Path(__file__).resolve().parents[2] / 'contracts' / 'app-api.yaml'
REGISTRY = Registry().with_resource(
    'urn:contract',
    Resource.from_contents(
        yaml.safe_load(CONTRACT.read_text())
        | {'$schema': 'https://json-schema.org/draft/2020-12/schema'}
    ),
)


def validate(schema: str, body: Any) -> None:
    Draft202012Validator(
        {'$ref': f'urn:contract#/components/schemas/{schema}'}, registry=REGISTRY
    ).validate(body)


@dataclass
class Phone:
    device_id: str
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {'Authorization': f'Bearer {self.token}'}


def add_phone(client: TestClient, user_id: str = tokens.OWNER_USER_ID) -> Phone:
    token = tokens.new_token()
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        device = Device(user_id=user_id, name='iPhone', token_hash=tokens.hash_secret(token))
        s.add(device)
        s.commit()
        return Phone(device.id, token)


def add_agent(client: TestClient, user_id: str = tokens.OWNER_USER_ID) -> tuple[str, str]:
    """An agent with its main conversation: (agent id, main conversation id)."""
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        agent = Agent(
            owner_user_id=user_id, name='Marcel', animal='giraffe', palette='pink', seed='1'
        )
        s.add(agent)
        s.commit()
        main = conversations.ensure_main_conversation(s, agent)
        return agent.id, main.id


def add_message(client: TestClient, conversation_id: str, **body: Any) -> str:
    """An agent message stored directly, as later stories (brain, milestones) will."""
    kind = MessageKind(body.pop('kind', 'text'))
    task_id = body.pop('task_id', None)
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        msg = Message(
            conversation_id=conversation_id,
            role=MessageRole.agent,
            kind=kind,
            body_json=body,
            task_id=task_id,
        )
        s.add(msg)
        s.commit()
        return msg.id


def events(client: TestClient) -> list[AppEvent]:
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        return list(s.exec(select(AppEvent).order_by(AppEvent.user_id, AppEvent.id)).all())  # pyright: ignore[reportArgumentType]


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db')
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def phone(client: TestClient) -> Phone:
    return add_phone(client)


@pytest.fixture
def agent(client: TestClient) -> tuple[str, str]:
    return add_agent(client)


@pytest.fixture
def main(agent: tuple[str, str]) -> str:
    return agent[1]


def send(client: TestClient, phone: Phone, conversation_id: str, text: str, **kw: Any) -> Any:
    res = client.post(
        f'/api/conversations/{conversation_id}/messages',
        json={'text': text, **kw},
        headers=phone.headers,
    )
    assert res.status_code == 201, res.text
    validate('Message', res.json())
    return res.json()


# ---- main conversation ----------------------------------------------------------------------


def test_the_main_conversation_is_made_once_and_linked_to_the_agent(client: TestClient) -> None:
    agent_id, main = add_agent(client)
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        agent = s.get(Agent, agent_id)
        assert agent is not None and agent.main_conversation_id == main
        assert conversations.ensure_main_conversation(s, agent).id == main


def test_list_conversations_main_first_then_side_threads_newest_first(
    client: TestClient, phone: Phone, agent: tuple[str, str]
) -> None:
    agent_id, main = agent
    first = send(client, phone, main, 'first')
    second = send(client, phone, main, 'second')
    older = client.post(f'/api/messages/{first["id"]}/thread', headers=phone.headers).json()
    newer = client.post(f'/api/messages/{second["id"]}/thread', headers=phone.headers).json()
    send(client, phone, older['id'], 'a reply')

    res = client.get('/api/conversations', params={'agent_id': agent_id}, headers=phone.headers)

    assert res.status_code == 200
    body = res.json()
    for conv in body:
        validate('Conversation', conv)
    assert [c['id'] for c in body] == [main, newer['id'], older['id']]
    assert body[0]['kind'] == 'main'
    assert body[2]['reply_count'] == 1 and 'last_message_at' in body[2]
    assert body[1]['reply_count'] == 0 and 'last_message_at' not in body[1]


def test_another_users_agent_lists_no_conversations(client: TestClient, phone: Phone) -> None:
    other_agent, _ = add_agent(client, user_id='usr_other')
    res = client.get('/api/conversations', params={'agent_id': other_agent}, headers=phone.headers)
    assert res.json() == []


# ---- sending ----------------------------------------------------------------------------------


def test_send_stores_the_message_and_returns_it(
    client: TestClient, phone: Phone, main: str
) -> None:
    msg = send(client, phone, main, 'Fix the login test', client_id='c-1')
    assert msg | {'id': 'x', 'created_at': 'x'} == {
        'id': 'x',
        'conversation_id': main,
        'role': 'user',
        'kind': 'text',
        'text': 'Fix the login test',
        'client_id': 'c-1',
        'created_at': 'x',
    }
    listed = client.get(f'/api/conversations/{main}/messages', headers=phone.headers).json()
    assert listed == {'items': [msg]}


def test_the_same_client_id_returns_the_same_message_and_no_duplicate(
    client: TestClient, phone: Phone, main: str
) -> None:
    first = send(client, phone, main, 'hello', client_id='retry-1')
    again = send(client, phone, main, 'hello', client_id='retry-1')
    other = send(client, phone, main, 'hello', client_id='retry-2')

    assert again == first
    assert other['id'] != first['id']
    items = client.get(f'/api/conversations/{main}/messages', headers=phone.headers).json()
    assert len(items['items']) == 2
    assert [e.type for e in events(client)] == ['message.created', 'message.created']


def test_concurrent_retries_with_one_client_id_store_one_message(
    client: TestClient, phone: Phone, main: str
) -> None:
    results: list[str] = []

    def retry() -> None:
        results.append(send(client, phone, main, 'hello', client_id='same')['id'])

    threads = [threading.Thread(target=retry) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(results)) == 1
    assert len(events(client)) == 1


def test_a_project_id_is_accepted(client: TestClient, phone: Phone, main: str) -> None:
    # Stored from S-04.10; until then the message still lands in the main conversation.
    msg = send(client, phone, main, 'about energy', project_id='prj_energy')
    assert msg['conversation_id'] == main


@pytest.mark.parametrize(
    'body', [{}, {'text': ''}, {'text': 'x' * 20001}, {'text': 'hi', 'colour': 'red'}]
)
def test_a_bad_message_is_a_400(
    client: TestClient, phone: Phone, main: str, body: dict[str, Any]
) -> None:
    res = client.post(f'/api/conversations/{main}/messages', json=body, headers=phone.headers)
    assert res.status_code == 400
    assert res.json()['code'] == 'bad_request'


def test_another_users_conversation_is_not_found(client: TestClient, phone: Phone) -> None:
    _, theirs = add_agent(client, user_id='usr_other')
    for res in (
        client.post(
            f'/api/conversations/{theirs}/messages', json={'text': 'hi'}, headers=phone.headers
        ),
        client.get(f'/api/conversations/{theirs}/messages', headers=phone.headers),
        client.get('/api/conversations/cnv_nope/messages', headers=phone.headers),
    ):
        assert res.status_code == 404
        assert res.json()['code'] == 'conversation_not_found'
    assert events(client) == []


# ---- paging -----------------------------------------------------------------------------------


def test_messages_are_newest_first_and_page_backwards_by_cursor(
    client: TestClient, phone: Phone, main: str
) -> None:
    sent = [send(client, phone, main, f'm{i}')['id'] for i in range(7)]
    pages: list[list[str]] = []
    params: dict[str, Any] = {'limit': 3}
    while True:
        res = client.get(
            f'/api/conversations/{main}/messages', params=params, headers=phone.headers
        )
        assert res.status_code == 200
        validate('MessagePage', res.json())
        pages.append([m['id'] for m in res.json()['items']])
        if 'next_cursor' not in res.json():
            break
        params['cursor'] = res.json()['next_cursor']

    assert pages == [sent[6:3:-1], sent[3:0:-1], sent[0:1]]


def test_a_full_last_page_has_no_cursor(client: TestClient, phone: Phone, main: str) -> None:
    for i in range(2):
        send(client, phone, main, f'm{i}')
    res = client.get(f'/api/conversations/{main}/messages?limit=2', headers=phone.headers)
    assert 'next_cursor' not in res.json()


def test_messages_of_another_conversation_are_not_listed(
    client: TestClient, phone: Phone, main: str
) -> None:
    _, other_main = add_agent(client)
    send(client, phone, other_main, 'elsewhere')
    assert client.get(f'/api/conversations/{main}/messages', headers=phone.headers).json() == {
        'items': []
    }


@pytest.mark.parametrize('cursor', ['nonsense', 'c_msg_missing'])
def test_a_bad_cursor_is_a_400(client: TestClient, phone: Phone, main: str, cursor: str) -> None:
    res = client.get(f'/api/conversations/{main}/messages?cursor={cursor}', headers=phone.headers)
    assert res.status_code == 400
    assert res.json()['code'] == 'bad_cursor'


def test_a_cursor_from_another_conversation_is_a_400(
    client: TestClient, phone: Phone, main: str
) -> None:
    _, other_main = add_agent(client)
    for i in range(2):
        send(client, phone, other_main, f'm{i}')
    cursor = client.get(
        f'/api/conversations/{other_main}/messages?limit=1', headers=phone.headers
    ).json()['next_cursor']
    res = client.get(f'/api/conversations/{main}/messages?cursor={cursor}', headers=phone.headers)
    assert res.status_code == 400


@pytest.mark.parametrize('limit', [0, 201, 'x'])
def test_a_bad_limit_is_a_400(client: TestClient, phone: Phone, main: str, limit: Any) -> None:
    res = client.get(f'/api/conversations/{main}/messages?limit={limit}', headers=phone.headers)
    assert res.status_code == 400


# ---- side threads -----------------------------------------------------------------------------


def test_reply_in_thread_opens_a_side_conversation_once(
    client: TestClient, phone: Phone, agent: tuple[str, str]
) -> None:
    agent_id, main = agent
    parent = send(client, phone, main, 'Which ORM should we move to? Think it over.')

    first = client.post(f'/api/messages/{parent["id"]}/thread', headers=phone.headers)
    again = client.post(f'/api/messages/{parent["id"]}/thread', headers=phone.headers)

    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()['id'] == first.json()['id']
    side = first.json()
    validate('Conversation', side)
    assert side['kind'] == 'side'
    assert side['agent_id'] == agent_id
    assert side['parent_message_id'] == parent['id']
    assert side['title'] == 'Which ORM should we move to? Think it over.'
    assert 'task_id' not in side
    assert [e.type for e in events(client)] == ['message.created', 'conversation.created']


def test_the_parent_message_shows_its_thread_and_reply_count(
    client: TestClient, phone: Phone, main: str
) -> None:
    parent = send(client, phone, main, 'question')
    side = client.post(f'/api/messages/{parent["id"]}/thread', headers=phone.headers).json()
    send(client, phone, side['id'], 'reply 1')
    send(client, phone, side['id'], 'reply 2')

    [listed] = client.get(f'/api/conversations/{main}/messages', headers=phone.headers).json()[
        'items'
    ]
    assert listed['thread'] == {'conversation_id': side['id'], 'reply_count': 2}
    replies = client.get(f'/api/conversations/{side["id"]}/messages', headers=phone.headers)
    assert [m['text'] for m in replies.json()['items']] == ['reply 2', 'reply 1']


def test_a_thread_on_a_milestone_belongs_to_its_task(
    client: TestClient, phone: Phone, main: str
) -> None:
    milestone = {
        'task_id': 'tsk_1',
        'task_title': 'Fix the flaky login test',
        'event': 'done',
        'location': 'cloud',
    }
    msg_id = add_message(client, main, kind='milestone', task_id='tsk_1', milestone=milestone)
    [listed] = client.get(f'/api/conversations/{main}/messages', headers=phone.headers).json()[
        'items'
    ]
    validate('Message', listed)
    assert listed['milestone'] == milestone

    side = client.post(f'/api/messages/{msg_id}/thread', headers=phone.headers).json()
    assert side['task_id'] == 'tsk_1'
    assert side['title'] == 'Fix the flaky login test'


def test_a_thread_on_a_plain_agent_message_has_no_task(
    client: TestClient, phone: Phone, main: str
) -> None:
    msg_id = add_message(client, main, text='Here is a long answer.\nWith a second line.')
    side = client.post(f'/api/messages/{msg_id}/thread', headers=phone.headers).json()
    assert 'task_id' not in side
    assert side['title'] == 'Here is a long answer.'


def test_a_thread_on_a_missing_or_foreign_message_is_not_found(
    client: TestClient, phone: Phone
) -> None:
    _, theirs = add_agent(client, user_id='usr_other')
    their_msg = add_message(client, theirs, text='private')
    for msg_id in ('msg_nope', their_msg):
        res = client.post(f'/api/messages/{msg_id}/thread', headers=phone.headers)
        assert res.status_code == 404
        assert res.json()['code'] == 'message_not_found'


# ---- send to main -----------------------------------------------------------------------------


@pytest.fixture
def side(client: TestClient, phone: Phone, main: str) -> str:
    parent = send(client, phone, main, 'question')
    return client.post(f'/api/messages/{parent["id"]}/thread', headers=phone.headers).json()['id']


def test_send_to_main_quotes_a_side_thread_message(
    client: TestClient, phone: Phone, main: str, side: str
) -> None:
    answer = add_message(client, side, text='Use SQLModel.')
    res = client.post(
        f'/api/conversations/{side}/send-to-main',
        json={'message_id': answer},
        headers=phone.headers,
    )
    assert res.status_code == 201
    quote = res.json()
    validate('Message', quote)
    assert quote['conversation_id'] == main
    assert quote['kind'] == 'quote'
    assert quote['role'] == 'user'
    assert quote['text'] == 'Use SQLModel.'
    assert quote['quoted_message_id'] == answer
    latest = client.get(f'/api/conversations/{main}/messages?limit=1', headers=phone.headers)
    assert latest.json()['items'] == [quote]
    assert events(client)[-1].type == 'message.created'


def test_send_to_main_quotes_a_milestone_by_its_summary(
    client: TestClient, phone: Phone, side: str
) -> None:
    milestone = {
        'task_id': 'tsk_1',
        'task_title': 'Fix it',
        'event': 'done',
        'location': 'nuc',
        'summary': 'Fixed the race.',
    }
    msg_id = add_message(client, side, kind='milestone', task_id='tsk_1', milestone=milestone)
    res = client.post(
        f'/api/conversations/{side}/send-to-main',
        json={'message_id': msg_id},
        headers=phone.headers,
    )
    assert res.json()['text'] == 'Fixed the race.'


def test_send_to_main_with_own_text(client: TestClient, phone: Phone, main: str, side: str) -> None:
    res = client.post(
        f'/api/conversations/{side}/send-to-main',
        json={'text': 'We go with SQLModel.'},
        headers=phone.headers,
    )
    assert res.status_code == 201
    validate('Message', res.json())
    assert res.json()['kind'] == 'text'
    assert res.json()['conversation_id'] == main


@pytest.mark.parametrize('body', [{}, {'message_id': 'm', 'text': 't'}])
def test_send_to_main_needs_exactly_one_of_message_or_text(
    client: TestClient, phone: Phone, side: str, body: dict[str, Any]
) -> None:
    res = client.post(f'/api/conversations/{side}/send-to-main', json=body, headers=phone.headers)
    assert res.status_code == 400


def test_send_to_main_from_the_main_conversation_is_a_400(
    client: TestClient, phone: Phone, main: str
) -> None:
    res = client.post(
        f'/api/conversations/{main}/send-to-main', json={'text': 'x'}, headers=phone.headers
    )
    assert res.status_code == 400
    assert res.json()['code'] == 'already_main'


def test_send_to_main_of_a_message_outside_the_thread_is_not_found(
    client: TestClient, phone: Phone, main: str, side: str
) -> None:
    in_main = add_message(client, main, text='not in the thread')
    res = client.post(
        f'/api/conversations/{side}/send-to-main',
        json={'message_id': in_main},
        headers=phone.headers,
    )
    assert res.status_code == 404
    assert res.json()['code'] == 'message_not_found'


def test_send_to_main_without_a_main_conversation_is_not_found(
    client: TestClient, phone: Phone, main: str, side: str, agent: tuple[str, str]
) -> None:
    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        row = s.get(Agent, agent[0])
        assert row is not None
        row.main_conversation_id = None
        s.add(row)
        s.commit()
    res = client.post(
        f'/api/conversations/{side}/send-to-main', json={'text': 'x'}, headers=phone.headers
    )
    assert res.status_code == 404
    assert res.json()['code'] == 'conversation_not_found'
