"""Mentions (S-18.2, B-42): the composer's search, the checks on a message's mentions, and the
brain frame that carries them. Marcel's things come from `test_isolation.World`; Emma is a phone
and a helper under another user id, which `mentions.py` still keeps apart."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from test_channel_fakes import MESSAGE, ChannelHub, channel_hub
from test_isolation import World as Buddies

from marcel_hub.app import Settings, create_app
from marcel_hub.auth import tokens
from marcel_hub.conversations import ensure_main_conversation
from marcel_hub.models import Agent, Task, TaskLocation, TaskOrigin, TaskState, inbox_id

EMMA = 'usr_emma'


class World(Buddies):
    """Marcel's world, and Emma: a phone and her helper Eve under another user id."""

    def __init__(self, client: TestClient) -> None:
        super().__init__(client)
        self.owner, self.agent_id = self.marcels_phone, self.marcel
        self.conversation_id = self.marcel_main
        with Session(self.engine) as s:
            eve = Agent(owner_user_id=EMMA, name='Eve', animal='cat', palette='marcel', seed='Eve',
                        slug='eve')  # fmt: skip
            s.add(eve)
            s.commit()
            self.emma_main = ensure_main_conversation(s, eve).id
            self.emma_inbox = inbox_id(s.connection(), eve.id)
            s.commit()
            code = tokens.create_pairing_code(s, user_id=EMMA)
        paired = client.post('/api/pair', json={'code': code, 'device_name': 'Emma'}).json()
        self.emma = {'Authorization': f'Bearer {paired["token"]}'}


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db', owner_name='Shaun')
    with TestClient(create_app(settings)) as client:
        yield World(client)


def mentions(world: World, who: dict[str, str], **params: Any) -> list[dict[str, Any]]:
    res = world.call('GET', '/api/mentions', who, params=params)
    assert res.status_code == 200, res.text
    return res.json()


def send(world: World, who: dict[str, str], conversation_id: str, **body: Any) -> Any:
    return world.call('POST', f'/api/conversations/{conversation_id}/messages', who, body)


def emmas_conversation(world: World) -> str:
    return world.emma_main


# Search ---------------------------------------------------------------------------------------


def test_search_finds_quests_projects_and_items_by_title_newest_first(world: World) -> None:
    found = mentions(world, world.owner, q='HEAT')
    assert [(m['kind'], m['id']) for m in found] == [('task', world.task_id)]
    assert {m['title'] for m in mentions(world, world.owner, q='offer')} == {'Offer'}
    assert mentions(world, world.owner, q='house', kind='project') == [
        {'kind': 'project', 'id': world.project_id, 'title': 'House'}
    ]
    everything = mentions(world, world.owner)
    assert {m['kind'] for m in everything} == {'task', 'project', 'artifact'}
    assert not any(m['title'].endswith('board') for m in everything)  # boards are not items
    newest = mentions(world, world.owner, kind='project')
    assert newest[0]['id'] == world.project_id  # made after the inbox


def test_search_is_at_most_twenty_and_treats_wildcards_as_text(world: World) -> None:
    with Session(world.engine) as s:
        for n in range(25):
            s.add(
                Task(
                    agent_id=world.agent_id,
                    title=f'Chore {n}',
                    location=TaskLocation.nuc,
                    state=TaskState.done,
                    model='sonnet',
                    origin=TaskOrigin.marcel,
                )
            )
        s.commit()
    assert len(mentions(world, world.owner, q='chore')) == 20
    assert mentions(world, world.owner, q='%') == []
    assert mentions(world, world.owner, q='_') == []


def test_search_never_returns_another_persons_things(world: World) -> None:
    assert all(
        m['id'] not in (world.task_id, world.project_id, world.artifact_id)
        for m in mentions(world, world.emma)
    )
    assert mentions(world, world.emma, q='heat') == []
    assert mentions(world, world.emma, q='house', kind='project') == []
    # Emma finds her own inbox.
    assert [m['id'] for m in mentions(world, world.emma, kind='project')] == [world.emma_inbox]


def test_a_bad_search_is_a_400(world: World) -> None:
    for params in ({'q': 'x' * 101}, {'kind': 'person'}):
        res = world.call('GET', '/api/mentions', world.owner, params=params)
        assert res.status_code == 400, params
        assert res.json()['code'] == 'bad_request'


# A message's mentions -------------------------------------------------------------------------


def test_mentions_are_stored_with_the_hubs_titles(world: World) -> None:
    res = send(
        world,
        world.owner,
        world.conversation_id,
        text='How is @Heat pumps going?',
        mentions=[
            {'kind': 'task', 'id': world.task_id, 'title': 'what the app had'},
            {'kind': 'artifact', 'id': world.artifact_id, 'title': 'Offer'},
        ],
    )
    assert res.status_code == 201, res.text
    message = res.json()
    MESSAGE.validate(message)
    assert message['mentions'] == [
        {'kind': 'task', 'id': world.task_id, 'title': 'Heat pumps'},
        {'kind': 'artifact', 'id': world.artifact_id, 'title': 'Offer'},
    ]
    assert 'project_id' not in message
    fetched = world.call('GET', f'/api/messages/{message["id"]}', world.owner).json()
    assert fetched['mentions'] == message['mentions']


def test_a_project_mention_files_the_message_in_that_project(world: World) -> None:
    house = {'kind': 'project', 'id': world.project_id, 'title': 'House'}
    res = send(world, world.owner, world.conversation_id, text='News on #House?', mentions=[house])
    assert res.status_code == 201 and res.json()['project_id'] == world.project_id
    # A project the message names itself wins over the mention.
    inbox = mentions(world, world.owner, kind='project')[-1]['id']
    res = send(
        world,
        world.owner,
        world.conversation_id,
        text='Inbox only',
        project_id=inbox,
        mentions=[house],
    )
    assert res.json()['project_id'] == inbox


def test_another_persons_mention_is_refused_and_nothing_is_stored(world: World) -> None:
    emma_chat = emmas_conversation(world)
    before = world.call('GET', f'/api/conversations/{emma_chat}/messages', world.emma).json()
    for mention in (
        {'kind': 'task', 'id': world.task_id, 'title': 'Heat pumps'},
        {'kind': 'project', 'id': world.project_id, 'title': 'House'},
        {'kind': 'artifact', 'id': world.artifact_id, 'title': 'Offer'},
        {'kind': 'task', 'id': 'tsk_nope', 'title': 'Gone'},
    ):
        res = send(world, world.emma, emma_chat, text='About @that', mentions=[mention])
        assert res.status_code == 404, mention
        assert res.json()['code'] == 'mention_not_found'
    after = world.call('GET', f'/api/conversations/{emma_chat}/messages', world.emma).json()
    assert after == before


def test_an_archived_project_can_no_longer_be_mentioned(world: World) -> None:
    house = {'kind': 'project', 'id': world.project_id, 'title': 'House'}
    with Session(world.engine) as s:
        task = s.get(Task, world.task_id)
        assert task is not None
        task.state = TaskState.done  # a project with live work cannot be archived
        s.add(task)
        s.commit()
    assert (
        world.call('POST', f'/api/projects/{world.project_id}/archive', world.owner).status_code
        == 200
    )
    assert mentions(world, world.owner, q='house', kind='project') == []
    res = send(world, world.owner, world.conversation_id, text='#House?', mentions=[house])
    assert res.status_code == 404 and res.json()['code'] == 'mention_not_found'


def test_more_than_ten_mentions_is_a_400(world: World) -> None:
    task = {'kind': 'task', 'id': world.task_id, 'title': 'Heat pumps'}
    res = send(world, world.owner, world.conversation_id, text='Lots', mentions=[task] * 11)
    assert res.status_code == 400


# The brain frame ------------------------------------------------------------------------------


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[ChannelHub]:
    with channel_hub(tmp_path) as hub:
        yield hub


def test_the_brain_frame_carries_the_mentions(hub: ChannelHub) -> None:
    task_id, _ = hub.task(title='Fix flaky login test')
    brain = hub.brain()
    mention = {'kind': 'task', 'id': task_id, 'title': 'Fix flaky login test'}
    res = hub.client.post(
        f'/api/conversations/{hub.main_id}/messages',
        json={'text': '@Fix flaky login test also check Safari', 'mentions': [mention]},
    )
    assert res.status_code == 201, res.text
    with hub.connect() as ch:
        ch.hello(brain)
        frame = ch.deliver()
    assert frame['type'] == 'user_message'
    assert frame['mentions'] == [mention]


def test_a_message_without_mentions_sends_none_to_the_brain(hub: ChannelHub) -> None:
    brain = hub.brain()
    hub.client.post(f'/api/conversations/{hub.main_id}/messages', json={'text': 'Hello'})
    with hub.connect() as ch:
        ch.hello(brain)
        assert 'mentions' not in ch.deliver()
