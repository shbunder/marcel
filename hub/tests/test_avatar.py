"""Each agent's avatar (S-19.2): `GET /api/agents/{agent_id}/avatar` and `GET /api/avatar-previews`
in the real app, drawn by the real Pixel Zoo (`shared/avatar/zoo`). Nothing reaches a network."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args

import pytest
import yaml
import zoo
from jsonschema import Draft202012Validator
from sqlmodel import Session
from test_channel_fakes import ChannelHub, channel_hub
from zoo import png

from marcel_hub.avatar import routes, store
from marcel_hub.models import Agent

CONTRACTS = Path(__file__).resolve().parents[2] / 'contracts'
SHEET = Draft202012Validator(json.loads((CONTRACTS / 'avatar-sheet.schema.json').read_text()))
SCHEMAS = yaml.safe_load((CONTRACTS / 'app-api.yaml').read_text())['components']['schemas']
FOX = {'animal': 'fox', 'color': 'tangerine', 'personality': 'cheerful'}
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[ChannelHub]:
    with channel_hub(tmp_path) as hub:
        yield hub


def avatar(hub: ChannelHub, **headers: str) -> Any:
    return hub.client.get(f'/api/agents/{hub.agent_id}/avatar', headers=headers)


def preview(hub: ChannelHub, **query: str) -> Any:
    return hub.client.get('/api/avatar-previews', params=query)


def size(image: bytes) -> tuple[int, int]:
    width, height, _ = png.decode(image)
    return width, height


# The sheet ----------------------------------------------------------------------------------


def test_the_sheet_is_the_contracts_json_and_its_png(hub: ChannelHub) -> None:
    res = avatar(hub)
    assert res.status_code == 200 and res.headers['content-type'] == 'application/json'
    meta = res.json()
    SHEET.validate(meta)
    image = avatar(hub, accept='image/png')
    assert image.status_code == 200 and image.headers['content-type'] == 'image/png'
    assert image.content.startswith(PNG_SIGNATURE)
    rows = -(-meta['frame_count'] // meta['columns'])
    assert size(image.content) == (meta['columns'] * 32, rows * 32)
    assert res.headers['etag'] == image.headers['etag'], 'both bodies carry the same ETag'
    assert res.headers['vary'] == 'Accept'


@pytest.mark.parametrize(
    ('accept', 'kind'),
    [
        ('', 'application/json'),
        ('*/*', 'application/json'),
        ('application/json, image/png', 'application/json'),
        ('image/png, application/json', 'image/png'),
        ('image/png;q=0.9', 'image/png'),
    ],
)
def test_accept_picks_the_body(hub: ChannelHub, accept: str, kind: str) -> None:
    assert avatar(hub, accept=accept).headers['content-type'] == kind


def test_same_look_same_etag_and_304(hub: ChannelHub) -> None:
    first = avatar(hub).headers['etag']
    assert avatar(hub).headers['etag'] == first
    again = avatar(hub, **{'if-none-match': first})
    assert again.status_code == 304 and again.content == b'' and again.headers['etag'] == first
    assert avatar(hub, **{'if-none-match': '"stale"'}).status_code == 200


def test_a_new_look_is_a_new_sheet_and_event(hub: ChannelHub) -> None:
    before = avatar(hub)
    res = hub.client.patch(f'/api/agents/{hub.agent_id}', json={'look': FOX})
    assert res.status_code == 200
    after = avatar(hub)
    assert after.headers['etag'] != before.headers['etag']
    assert after.json()['name'] == 'fox' and after.json()['palette'] == 'tangerine'
    assert avatar(hub, accept='image/png').content != before.content
    [event] = hub.events('agent.updated')  # validated against events.schema.json
    assert event['data']['avatar_etag'] == after.headers['etag']
    assert avatar(hub, **{'if-none-match': before.headers['etag']}).status_code == 200


def test_a_rename_keeps_the_avatar(hub: ChannelHub) -> None:
    tag = avatar(hub).headers['etag']
    hub.client.patch(f'/api/agents/{hub.agent_id}', json={'name': 'Pip'})
    [event] = hub.events('agent.updated')
    assert event['data']['avatar_etag'] == tag == avatar(hub).headers['etag']


def test_an_agent_without_a_look_draws_from_its_animal(hub: ChannelHub) -> None:
    with Session(hub.engine) as s:
        agent = s.get(Agent, hub.agent_id)
        assert agent is not None and agent.look_animal is None
        assert store.look_of(agent) == {
            'species': 'giraffe', 'color': 'natural', 'personality': 'professional'
        }  # fmt: skip
        agent.animal, agent.palette = 'frog', 'teal'
        assert store.look_of(agent)['species'] == 'frog'
        assert store.look_of(agent)['color'] == 'teal'
        agent.animal = 'dragon'  # an animal the zoo does not draw
        assert store.look_of(agent)['species'] == 'giraffe'


def test_another_persons_agent_is_not_found(hub: ChannelHub) -> None:
    other = {'Authorization': f'Bearer {hub.phone("usr_kid")}'}
    res = hub.client.get(f'/api/agents/{hub.agent_id}/avatar', headers=other)
    assert res.status_code == 404 and res.json()['code'] == 'agent_not_found'
    assert hub.client.get('/api/agents/agt_nope/avatar').status_code == 404


def test_a_sheet_is_drawn_once_and_kept_on_disk(
    hub: ChannelHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawn: list[dict[str, Any]] = []
    real = zoo.sheet

    def counting(look: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
        drawn.append(look)
        return real(look)

    monkeypatch.setattr(store.zoo, 'sheet', counting)
    for accept in ('application/json', 'image/png', 'application/json'):
        avatar(hub, accept=accept)
    assert len(drawn) == 1
    folder = hub.tmp_path / 'avatars'
    names = sorted(p.name for p in folder.iterdir())
    assert len(names) == 2 and not [n for n in names if n.endswith('.tmp')]


def test_the_key_follows_the_zoo_version(monkeypatch: pytest.MonkeyPatch) -> None:
    look = {'species': 'fox', 'color': 'teal', 'personality': 'shy'}
    before = store.etag(look)
    monkeypatch.setattr(store, 'ZOO_VERSION', '9.9.9')
    assert store.etag(look) != before, 'a new zoo release draws new sheets'


# Previews -----------------------------------------------------------------------------------


def test_a_preview_is_one_32_pixel_png(hub: ChannelHub) -> None:
    res = preview(hub, **FOX)
    assert res.status_code == 200 and res.headers['content-type'] == 'image/png'
    assert size(res.content) == (32, 32)
    assert res.headers['cache-control'] == routes.KEEP
    assert preview(hub, **FOX).content == res.content, 'same query, same image'
    happy = preview(hub, **FOX, mood='laughing')
    assert happy.status_code == 200 and happy.content != res.content


@pytest.mark.parametrize(
    'query',
    [
        {**FOX, 'animal': 'dragon'},
        {**FOX, 'color': '#ff0000'},  # the zoo draws any #rrggbb; the contract does not offer it
        {**FOX, 'personality': 'sleepy'},
        {**FOX, 'mood': 'bored'},
        {'animal': 'fox', 'color': 'teal'},
    ],
)
def test_a_preview_outside_the_contract_is_400(hub: ChannelHub, query: dict[str, str]) -> None:
    res = preview(hub, **query)
    assert res.status_code == 400 and res.json()['code'] == 'bad_request'


# The contract and the zoo agree ---------------------------------------------------------------


def test_the_moods_match_the_contract() -> None:
    assert list(get_args(routes.Mood)) == SCHEMAS['Mood']['enum']


def test_the_zoo_draws_every_look_the_contract_offers() -> None:
    assert set(SCHEMAS['LookAnimal']['enum']) <= set(zoo.SPECIES)
    assert set(SCHEMAS['LookPersonality']['enum']) <= set(zoo.PERSONALITIES)
    assert set(SCHEMAS['LookColor']['enum']) <= {'natural', *zoo.COLORS}
    assert set(SCHEMAS['Mood']['enum']) <= set(zoo.MOODS)
