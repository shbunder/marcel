"""Agent settings (S-17.3): `GET` and `PATCH /api/agents/{agent_id}` in the real app, with the real
`/channel` socket for the brain. Nothing reaches a runner, `claude` or the network."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args

import pytest
import yaml
from sqlmodel import Session, select
from test_channel_fakes import ChannelHub, channel_hub
from test_tasks_fakes import validator

from marcel_hub.agents import routes
from marcel_hub.models import Agent, ChannelFrame

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


def test_another_persons_agent_is_not_found(hub: ChannelHub) -> None:
    # Control: delete the owner check and this goes red.
    other = {'Authorization': f'Bearer {hub.phone("usr_kid")}'}
    for res in (
        hub.client.get(url(hub), headers=other),
        hub.client.patch(url(hub), json={'name': 'Mine now'}, headers=other),
        hub.client.get(url(hub, 'agt_nope')),
    ):
        assert res.status_code == 404 and res.json()['code'] == 'agent_not_found'
    with Session(hub.engine) as s:
        agent = s.get(Agent, hub.agent_id)
        assert agent is not None and agent.name == 'Marcel'
    assert hub.events('agent.updated') == []


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
