"""Buddies (S-17.3, S-22.27): `POST /api/agents`, `GET` and `PATCH /api/agents/{agent_id}` and
`POST /api/agents/{agent_id}/archive` in the real app, with the real `/channel` socket for the
brain. Nothing reaches a runner, `claude` or the network."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args

import pytest
import yaml
from sqlmodel import Session, select
from test_channel_fakes import ChannelHub, channel_hub
from test_tasks_fakes import validator

from marcel_hub.agents import routes
from marcel_hub.avatar.store import look_from_name
from marcel_hub.models import Agent, ChannelFrame, Conversation, ConversationKind, Project

AGENT = validator('app-api.yaml#/components/schemas/Agent')
APP_API = Path(__file__).resolve().parents[2] / 'contracts' / 'app-api.yaml'
FOX = {'animal': 'fox', 'color': 'tangerine', 'personality': 'cheerful'}


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[ChannelHub]:
    with channel_hub(tmp_path) as hub:
        yield hub


def url(hub: ChannelHub, agent_id: str | None = None) -> str:
    return f'/api/agents/{agent_id or hub.agent_id}'


def patch(hub: ChannelHub, body: Any, **kw: Any) -> Any:
    return hub.client.patch(url(hub), json=body, **kw)


def renames(hub: ChannelHub) -> list[dict[str, Any]]:
    with Session(hub.engine) as s:
        frames = s.exec(select(ChannelFrame)).all()
    return [f.frame_json for f in frames if f.frame_json.get('kind') == 'agent_renamed']


def test_get_answers_the_contracts_agent(hub: ChannelHub) -> None:
    res = hub.client.get(url(hub))
    assert res.status_code == 200
    AGENT.validate(res.json())
    assert res.json()['id'] == hub.agent_id and res.json()['name'] == 'Marcel'
    assert 'look' not in res.json() and 'onboarded_at' not in res.json()


def test_rename_and_look_round_trip(hub: ChannelHub) -> None:
    res = patch(hub, {'name': 'Pip', 'look': FOX})
    assert res.status_code == 200, res.json()
    AGENT.validate(res.json())
    got = hub.client.get(url(hub)).json()
    assert (got['name'], got['look'], got['animal']) == ('Pip', FOX, 'fox'), 'animal follows'
    me = hub.client.get('/api/me').json()
    assert me['agents'][0]['look'] == FOX
    [event] = hub.events('agent.updated')  # validated against events.schema.json
    assert event['data']['agent']['name'] == 'Pip' and event['data']['agent']['look'] == FOX


def test_onboarded_is_set_once(hub: ChannelHub) -> None:
    first = patch(hub, {'onboarded': True}).json()['onboarded_at']
    assert first
    again = patch(hub, {'onboarded': True}).json()
    assert again['onboarded_at'] == first, 'sending it again changes nothing'
    assert len(hub.events('agent.updated')) == 1, 'no event for a request that changed nothing'


def test_palette_counts_only_until_there_is_a_look(hub: ChannelHub) -> None:
    assert patch(hub, {'palette': 'mint'}).json()['palette'] == 'mint'
    patch(hub, {'look': FOX})
    assert patch(hub, {'palette': 'plum'}).json()['palette'] == 'mint', 'ignored once looked'


def test_an_unknown_agent_is_not_found(hub: ChannelHub) -> None:
    for res in (
        hub.client.get(url(hub, 'agt_nope')),
        hub.client.patch(url(hub, 'agt_nope'), json={'name': 'Mine now'}),
    ):
        assert res.status_code == 404 and res.json()['code'] == 'agent_not_found'
    assert hub.events('agent.updated') == []


def test_a_phone_showing_another_buddy_may_change_this_one(hub: ChannelHub) -> None:
    # One person, many buddies (ADR-013): every phone sees and changes every buddy.
    pip = add_buddy(hub)
    on_pip = {'Authorization': f'Bearer {hub.phone(agent_id=pip["id"])}'}
    res = hub.client.patch(url(hub), json={'name': 'Marcello'}, headers=on_pip)
    assert res.status_code == 200 and res.json()['name'] == 'Marcello'


@pytest.mark.parametrize(
    'body',
    [
        {},
        {'name': ''},
        {'name': 'x' * 41},
        {'look': {**FOX, 'color': 'mauve'}},
        {'look': {'animal': 'fox', 'color': 'teal'}},
        {'onboarded': False},
        {'nickname': 'Pip'},
        {'name': None},
        {'look': None},
        {'onboarded': None},
        {'palette': None},
    ],
)
def test_a_bad_body_is_400_and_changes_nothing(hub: ChannelHub, body: dict[str, Any]) -> None:
    res = patch(hub, body)
    assert res.status_code == 400 and res.json()['code'] == 'bad_request', res.json()
    assert hub.events('agent.updated') == []


@pytest.mark.parametrize(
    ('literal', 'schema'),
    [
        (routes.LookAnimal, 'LookAnimal'),
        (routes.LookColor, 'LookColor'),
        (routes.LookPersonality, 'LookPersonality'),
    ],
)
def test_the_looks_match_the_contract(literal: Any, schema: str) -> None:
    # The values are copied by hand; a contract change without the copy turns this red.
    contract = yaml.safe_load(APP_API.read_text())['components']['schemas'][schema]['enum']
    assert list(get_args(literal)) == contract


def test_a_40_character_name_is_fine(hub: ChannelHub) -> None:
    assert patch(hub, {'name': 'x' * 40}).status_code == 200


def test_the_brain_hears_a_rename_once(hub: ChannelHub) -> None:
    sid = hub.brain()
    with hub.connect() as channel:
        assert channel.hello(sid)['type'] == 'welcome'
        patch(hub, {'name': 'Pip'})
        frame = channel.next()
        assert (frame['type'], frame['kind'], frame['data']) == (
            'system_event', 'agent_renamed', {'name': 'Pip'},
        )  # fmt: skip
        channel.send(type='ack', seq=frame['seq'])
        patch(hub, {'name': 'Pip'})  # the same name: not a rename
        patch(hub, {'look': FOX})  # a look change: not a rename
        assert channel.until_pong() == []
    assert [f['data'] for f in renames(hub)] == [{'name': 'Pip'}]


def test_a_rename_waits_for_a_brain_that_is_not_connected(hub: ChannelHub) -> None:
    sid = hub.brain()
    patch(hub, {'name': 'Pip'})
    with hub.connect() as channel:
        channel.hello(sid)
        frame = channel.next()
        assert (frame['kind'], frame['data']) == ('agent_renamed', {'name': 'Pip'})


# Adding and archiving buddies (S-22.27, B-49) --------------------------------------------------


class Pokes:
    """Stands in for the brain keeper: counts the times it was asked to check now."""

    def __init__(self) -> None:
        self.count = 0

    def poke(self) -> None:
        self.count += 1


def add_buddy(hub: ChannelHub, **body: Any) -> dict[str, Any]:
    res = hub.client.post('/api/agents', json={'name': 'Pip', **body})
    assert res.status_code == 201, res.text
    AGENT.validate(res.json())
    return res.json()


def test_a_new_buddy_has_its_main_conversation_inbox_and_a_look_from_its_name(
    hub: ChannelHub,
) -> None:
    keeper = Pokes()
    hub.app.state.brain = keeper
    pip = add_buddy(hub)
    assert pip['look'] == look_from_name('Pip') and pip['animal'] == pip['look']['animal']
    with Session(hub.engine) as s:
        agent = s.get(Agent, pip['id'])
        assert agent is not None and agent.slug == 'pip', 'its own brain folder and memory'
        main = s.get(Conversation, pip['main_conversation_id'])
        assert main is not None and (main.agent_id, main.kind) == (pip['id'], ConversationKind.main)
        inbox = s.exec(select(Project).where(Project.agent_id == pip['id'])).all()
        assert [p.slug for p in inbox] == ['inbox']
    assert keeper.count == 1, 'the brain keeper checks now and starts its brain'
    assert pip['id'] in [a['id'] for a in hub.client.get('/api/me').json()['agents']]
    [event] = hub.events('agent.updated')  # validated against events.schema.json
    assert event['data']['agent']['id'] == pip['id']
    # A phone does not switch to it by itself.
    devices = hub.client.get('/api/devices').json()
    assert [d['agent_id'] for d in devices] == [hub.agent_id]


def test_a_new_buddy_keeps_the_look_it_was_given_and_a_free_slug(hub: ChannelHub) -> None:
    assert add_buddy(hub, look=FOX)['look'] == FOX
    with Session(hub.engine) as s:
        second = s.get(Agent, add_buddy(hub)['id'])
        assert second is not None and second.slug == 'pip-2'


@pytest.mark.parametrize(
    'body',
    [{}, {'name': ''}, {'name': 'x' * 41}, {'name': 'Pip', 'look': {**FOX, 'color': 'mauve'}},
     {'name': 'Pip', 'role': 'kid'}],
)  # fmt: skip
def test_a_bad_new_buddy_is_400_and_adds_nothing(hub: ChannelHub, body: dict[str, Any]) -> None:
    res = hub.client.post('/api/agents', json=body)
    assert res.status_code == 400 and res.json()['code'] == 'bad_request'
    with Session(hub.engine) as s:
        assert len(s.exec(select(Agent)).all()) == 1


def test_archiving_the_last_buddy_is_409_and_changes_nothing(hub: ChannelHub) -> None:
    # Control: delete the `last_buddy` check and this goes red.
    res = hub.client.post(f'/api/agents/{hub.agent_id}/archive')
    assert res.status_code == 409 and res.json()['code'] == 'last_buddy', res.text
    assert 'only buddy' in res.json()['message']
    with Session(hub.engine) as s:
        agent = s.get(Agent, hub.agent_id)
        assert agent is not None and agent.archived_at is None
    assert hub.events('agent.updated') == []


def test_archiving_a_buddy_moves_its_phones_and_keeps_it_readable(hub: ChannelHub) -> None:
    pip = add_buddy(hub)
    keeper = Pokes()
    hub.app.state.brain = keeper
    on_pip = {'Authorization': f'Bearer {hub.phone(agent_id=pip["id"])}'}
    res = hub.client.post(f'/api/agents/{pip["id"]}/archive')
    assert res.status_code == 200, res.text
    AGENT.validate(res.json())
    assert res.json()['archived_at']
    assert keeper.count == 1, 'the brain keeper stops its brain now'
    # It leaves /me but stays readable.
    assert [a['id'] for a in hub.client.get('/api/me').json()['agents']] == [hub.agent_id]
    assert hub.client.get(f'/api/agents/{pip["id"]}').json()['archived_at']
    # The phone that showed it now shows the first live buddy, and heard so.
    devices = hub.client.get('/api/devices', headers=on_pip).json()
    mine = next(d for d in devices if d['current'])
    assert mine['agent_id'] == hub.agent_id
    moved = [e['data'] for e in hub.events('device.updated')]
    assert [(d['id'], d['agent_id']) for d in moved] == [(mine['id'], hub.agent_id)]
    assert [e['data']['agent']['id'] for e in hub.events('agent.updated')][-1] == pip['id']
    # Archiving it again changes nothing.
    events = len(hub.events())
    again = hub.client.post(f'/api/agents/{pip["id"]}/archive')
    assert again.status_code == 200 and again.json()['archived_at'] == res.json()['archived_at']
    assert len(hub.events()) == events


def test_archiving_an_unknown_buddy_is_404(hub: ChannelHub) -> None:
    res = hub.client.post('/api/agents/agt_nope/archive')
    assert res.status_code == 404 and res.json()['code'] == 'agent_not_found'
