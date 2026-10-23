"""Each agent's avatar (S-19.2): `GET /api/agents/{agent_id}/avatar` and `GET /api/avatar-previews`
in the real app, drawn by the real Pixel Zoo (`shared/avatar/zoo`). Nothing reaches a network."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args

import pytest
import yaml
import zoo
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlmodel import Session, select
from test_channel_fakes import ChannelHub, channel_hub
from zoo import png

from marcel_hub import db
from marcel_hub.app import Settings, create_app
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


def test_every_answer_varies_on_accept(hub: ChannelHub) -> None:
    # JSON and PNG share one URL and one ETag: a cache must key them by Accept, 304 included.
    json_body, image = avatar(hub), avatar(hub, accept='image/png')
    not_modified = avatar(hub, accept='image/png', **{'if-none-match': json_body.headers['etag']})
    assert not_modified.status_code == 304
    for res in (json_body, image, not_modified):
        assert res.headers['vary'] == 'Accept'
        assert res.headers['etag'] == json_body.headers['etag']


def test_an_encoded_stranger_never_matches(hub: ChannelHub) -> None:
    assert avatar(hub, **{'if-none-match': '%22lk-fox%22'}).status_code == 200
    assert avatar(hub, **{'if-none-match': '%2A'}).status_code == 304, '%2A is *'


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


def test_an_unknown_agents_avatar_is_not_found(hub: ChannelHub) -> None:
    res = hub.client.get('/api/agents/agt_nope/avatar')
    assert res.status_code == 404 and res.json()['code'] == 'agent_not_found'


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


def test_the_key_follows_the_zoo_drawings(monkeypatch: pytest.MonkeyPatch) -> None:
    look = {'species': 'fox', 'color': 'teal', 'personality': 'shy'}
    before = store.etag(look)
    monkeypatch.setattr(store, 'ZOO_DRAWING', 'changed')
    assert store.etag(look) != before, 'a changed drawing draws new sheets'


def test_the_fingerprint_changes_with_any_drawing_source(tmp_path: Path) -> None:
    (tmp_path / 'core.py').write_text('EYES = 2\n')
    first = store.drawing_fingerprint(tmp_path)
    assert store.drawing_fingerprint(tmp_path) == first, 'stable'
    (tmp_path / 'core.py').write_text('EYES = 3\n')  # a drawing changed, no version bump
    assert store.drawing_fingerprint(tmp_path) != first


@pytest.mark.parametrize(
    'form', ['*', 'W/{tag}', '"other", {tag}', '{encoded}', 'W/{encoded}', '"other", {encoded}']
)
def test_if_none_match_in_its_other_forms(hub: ChannelHub, form: str) -> None:
    tag = avatar(hub).headers['etag']
    encoded = tag.replace('"', '%22')  # an iOS client once sent it percent-encoded
    sent = form.format(tag=tag, encoded=encoded)
    assert avatar(hub, **{'if-none-match': sent}).status_code == 304


def test_a_folder_that_cannot_be_written_only_costs_the_cache(tmp_path: Path) -> None:
    blocked = tmp_path / 'avatars'
    blocked.write_text('not a folder')  # nothing can be written under it
    keeper = store.AvatarStore(blocked)
    look = {'species': 'frog', 'color': 'lime', 'personality': 'bold'}
    image, meta = keeper.sheet(look)
    assert image.startswith(PNG_SIGNATURE) and meta['name'] == 'frog'
    assert keeper.preview(look, None).startswith(PNG_SIGNATURE)
    assert blocked.read_text() == 'not a folder'


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


# Looks from names (S-19.2, for agents made without a look) -----------------------------------


def test_a_look_picked_from_any_name_is_one_the_contract_offers() -> None:
    for name in ('Pip', 'Emma', 'Marcel', 'Zoë', '', '   ', 'x' * 40, '🦒', 'Lou-Ann'):
        look = store.look_from_name(name)
        assert look['animal'] in SCHEMAS['LookAnimal']['enum'], name
        assert look['color'] in SCHEMAS['LookColor']['enum'], name
        assert look['personality'] in SCHEMAS['LookPersonality']['enum'], name


def test_at_start_up_every_agent_without_a_look_gets_one(tmp_path: Path) -> None:
    engine = db.make_engine(tmp_path / 'marcel.db')
    db.upgrade(engine)
    with Session(engine) as s:
        for agent_id, owner, name in (
            ('agt_a', 'usr_owner', 'Marcel'),  # the oldest buddy: Marcel's look
            ('agt_b', 'usr_owner', 'Pip'),  # any other: from its name
            ('agt_c', 'usr_owner', 'Fox'),
        ):
            s.add(Agent(id=agent_id, owner_user_id=owner, name=name, animal='giraffe',
                        palette='marcel', seed=name))  # fmt: skip
        s.commit()
        fox = s.get(Agent, 'agt_c')
        assert fox is not None
        fox.look_animal, fox.look_color, fox.look_personality = 'fox', 'teal', 'shy'
        s.add(fox)
        s.commit()
    app = create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db'))
    with TestClient(app), Session(engine) as s:
        looks = {
            a.id: {
                'animal': a.look_animal,
                'color': a.look_color,
                'personality': a.look_personality,
            }  # fmt: skip
            for a in s.exec(select(Agent)).all()
        }
    assert looks['agt_a'] == {'animal': 'giraffe', 'color': 'rose', 'personality': 'professional'}
    assert looks['agt_b'] == store.look_from_name('Pip')
    assert looks['agt_c'] == {'animal': 'fox', 'color': 'teal', 'personality': 'shy'}, 'kept'
