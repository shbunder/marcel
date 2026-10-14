"""The avatar sheet and the look picker's previews (contract 0.7: B-44, S-19.4), drawn by the Pixel
Zoo as the real hub draws them."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import AUTH
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

SHEET_SCHEMA = json.loads(
    (Path(__file__).parents[2] / 'contracts' / 'avatar-sheet.schema.json').read_text()
)
PNG = b'\x89PNG\r\n\x1a\n'
LOOK = {'animal': 'fox', 'color': 'teal', 'personality': 'cool'}


def test_the_sheet_follows_the_look_as_json_or_png(client: TestClient):
    client.patch('/api/agents/agt_marcel', json={'look': LOOK}, headers=AUTH)
    sheet = client.get('/api/agents/agt_marcel/avatar', headers=AUTH)
    assert sheet.status_code == 200
    Draft202012Validator(SHEET_SCHEMA).validate(sheet.json())
    assert sheet.json()['name'] == 'fox'
    etag = sheet.headers['etag']
    assert etag == '"lk-fox-teal-cool"'
    image = client.get('/api/agents/agt_marcel/avatar', headers={**AUTH, 'Accept': 'image/png'})
    assert image.headers['content-type'] == 'image/png'
    assert image.content.startswith(PNG)
    assert image.headers['etag'] == etag


def test_an_unchanged_sheet_answers_304(client: TestClient):
    client.patch('/api/agents/agt_marcel', json={'look': LOOK}, headers=AUTH)
    etag = client.get('/api/agents/agt_marcel/avatar', headers=AUTH).headers['etag']
    same = client.get('/api/agents/agt_marcel/avatar', headers={**AUTH, 'If-None-Match': etag})
    assert same.status_code == 304
    client.patch('/api/agents/agt_marcel', json={'look': {**LOOK, 'animal': 'panda'}}, headers=AUTH)
    changed = client.get('/api/agents/agt_marcel/avatar', headers={**AUTH, 'If-None-Match': etag})
    assert changed.status_code == 200
    assert changed.json()['name'] == 'panda'


def test_a_preview_is_one_png_of_any_look(client: TestClient):
    query = {**LOOK, 'mood': 'happy'}
    first = client.get('/api/avatar-previews', params=query, headers=AUTH)
    assert first.status_code == 200
    assert first.headers['content-type'] == 'image/png'
    assert first.content.startswith(PNG)
    again = client.get('/api/avatar-previews', params=query, headers=AUTH)
    assert again.content == first.content, 'the same query gives the same image'
    other = client.get('/api/avatar-previews', params={**LOOK, 'animal': 'frog'}, headers=AUTH)
    assert other.content != first.content


def test_a_preview_of_an_unknown_value_is_a_400(client: TestClient):
    for wrong in (
        {'animal': 'dragon'},
        {'color': 'gold'},
        {'personality': 'loud'},
        {'mood': 'meh'},
    ):
        resp = client.get('/api/avatar-previews', params={**LOOK, **wrong}, headers=AUTH)
        assert resp.status_code == 400, wrong
        assert resp.json()['code'] == 'bad_request'
    unpaired = client.get('/api/avatar-previews', params=LOOK, headers={'Authorization': ''})
    assert unpaired.status_code == 401
