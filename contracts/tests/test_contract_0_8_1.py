"""app-api 0.8.1: the memory routes take an optional `agent_id`, so the Notebook follows the buddy
a phone shows (ADR-013 keeps memory per buddy). Answers S-22.12's contract request."""

from __future__ import annotations

from typing import Any

from conftest import load_yaml

MEMORY_ROUTES = [
    ('get', '/memory'),
    ('get', '/memory/files/{path}'),
    ('put', '/memory/files/{path}'),
]
REF = '#/components/parameters/MemoryAgentId'


def api() -> dict[str, Any]:
    return load_yaml('app-api.yaml')


def flat(text: str) -> str:
    return ' '.join(text.split())


def test_the_version_is_0_8_1() -> None:
    assert api()['info']['version'] == '0.8.2'  # 0.8.2 in S-02.16
    assert '**0.8.1** (additive)' in api()['info']['description']


def test_the_parameter_is_an_optional_query_string_defaulting_to_the_phones_buddy() -> None:
    param = api()['components']['parameters']['MemoryAgentId']
    assert param['name'] == 'agent_id' and param['in'] == 'query'
    assert param['required'] is False, 'required would break every app built before 0.8.1'
    assert param['schema'] == {'type': 'string'}
    text = flat(param['description'])
    assert "the calling phone's buddy (`Device.agent_id`)" in text
    assert '404 `agent_not_found`' in text and 'stays readable' in text


def test_every_memory_route_takes_it() -> None:
    paths = api()['paths']
    for method, path in MEMORY_ROUTES:
        params = paths[path][method].get('parameters', []) + paths[path].get('parameters', [])
        assert {'$ref': REF} in params, f'{method} {path}'
    memory_ops = {
        (m, p)
        for p, item in paths.items()
        for m, op in item.items()
        if isinstance(op, dict) and 'memory' in op.get('tags', [])
    }
    assert memory_ops == set(MEMORY_ROUTES), 'a new memory route must take agent_id too'


def test_an_unknown_buddy_is_a_404_agent_not_found() -> None:
    paths = api()['paths']
    for method, path in MEMORY_ROUTES:
        resp = paths[path][method]['responses']['404']
        assert '$ref' not in resp, f'{method} {path}: name the codes, not the generic NotFound'
        text = flat(resp['description'])
        assert '`agent_not_found`' in text, f'{method} {path}'
        assert resp['content']['application/json']['schema'] == {
            '$ref': '#/components/schemas/Error'
        }


def test_only_a_save_refuses_an_archived_buddy() -> None:
    """Archiving keeps a buddy's memory readable (`archiveAgent`), so only the PUT refuses it."""
    paths = api()['paths']
    put = flat(paths['/memory/files/{path}']['put']['responses']['404']['description'])
    assert 'archived' in put and '`memory_file_not_found`' in put
    for path in ('/memory', '/memory/files/{path}'):
        assert 'archived' not in paths[path]['get']['responses']['404']['description'], path
    archive = flat(paths['/agents/{agent_id}/archive']['post']['description'])
    assert 'memory stay readable' in archive


def test_the_error_codes_list_it_for_memory() -> None:
    code = api()['components']['schemas']['Error']['properties']['code']['description']
    assert '`agent_not_found` (0.8.1)' in flat(code)
