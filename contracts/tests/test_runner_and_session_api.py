"""runner-api.yaml and session-api.yaml: valid OpenAPI, every schema exemplified and consistent."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import load_yaml
from examples import schemas_with_examples, validator_for
from jsonschema.exceptions import ValidationError
from openapi_spec_validator import validate
from referencing import Registry

DOCS = ('runner-api.yaml', 'session-api.yaml')
CASES = [(doc, p, ex) for doc in DOCS for p, ex in schemas_with_examples(load_yaml(doc))]


@pytest.mark.parametrize('doc', DOCS)
def test_spec_is_valid_openapi_3_1(doc: str):
    validate(load_yaml(doc))


@pytest.mark.parametrize('doc', DOCS)
def test_every_component_schema_has_an_example(doc: str):
    schemas = load_yaml(doc)['components']['schemas']
    assert [name for name, s in schemas.items() if 'example' not in s] == []


@pytest.mark.parametrize(('doc', 'pointer', 'example'), CASES, ids=[f'{d}{p}' for d, p, _ in CASES])
def test_example_matches_its_schema(doc: str, pointer: str, example: Any, registry: Registry):
    validator_for(doc, pointer, registry).validate(example)


def runner(name: str) -> str:
    return f'/components/schemas/{name}'


BAD = [
    (
        'runner-api.yaml',
        'SpawnRequest',
        {'role': 'worker', 'name': 'x', 'model': 'sonnet', 'prompt': 'p'},
    ),
    (
        'runner-api.yaml',
        'SpawnRequest',
        {
            'role': 'worker',
            'name': 'x',
            'model': 'sonnet',
            'prompt': 'p',
            'cwd': '/tmp',
            'repo': {'url': 'u', 'task_id': 't', 'slug': 's'},
        },
    ),
    (
        'runner-api.yaml',
        'SpawnRequest',
        {'role': 'worker', 'name': 'has space', 'model': 'm', 'prompt': 'p', 'cwd': '/'},
    ),
    (
        'runner-api.yaml',
        'RunnerEvent',
        {'seq': 1, 'type': 'session.state', 'at': '2026-10-04T09:00:00Z'},
    ),
    (
        'runner-api.yaml',
        'RunnerEvent',
        {'seq': 1, 'type': 'runner.warning', 'at': '2026-10-04T09:00:00Z'},
    ),
    ('session-api.yaml', 'Report', {'kind': 'done'}),
    ('session-api.yaml', 'Report', {'kind': 'progress'}),
    ('session-api.yaml', 'Report', {'kind': 'artifact'}),
    ('session-api.yaml', 'ReportedArtifact', {'kind': 'pr', 'title': 'no url'}),
    (
        'session-api.yaml',
        'ReportedArtifact',
        {'kind': 'file', 'title': 'x', 'url': 'https://x.example'},
    ),
]


@pytest.mark.parametrize(
    ('doc', 'name', 'instance'), BAD, ids=[f'{d}-{n}-{i}' for i, (d, n, _) in enumerate(BAD)]
)
def test_bad_case_is_rejected(doc: str, name: str, instance: Any, registry: Registry):
    with pytest.raises(ValidationError):
        validator_for(doc, runner(name), registry).validate(instance)


def test_session_routes_hide_bad_tokens_as_404():
    """SP3: scanners probe the tunnel; a wrong token must look like no route at all."""
    api = load_yaml('session-api.yaml')
    for path in api['paths'].values():
        for op in path.values():
            responses = op['responses']
            assert '404' in responses and '401' not in responses and '403' not in responses


def test_session_api_lives_under_the_tunnel_prefix():
    assert [s['url'] for s in load_yaml('session-api.yaml')['servers']] == [
        'https://marcel-bot.com/api'
    ]


def test_runner_spawn_failure_codes_are_documented():
    text = load_yaml('runner-api.yaml')['paths']['/sessions']['post']['responses']['422'][
        'description'
    ]
    for code in ('not_trusted', 'channel_skipped', 'exited', 'no_supervisor'):
        assert code in text
