from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from conftest import BASE, load_api
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from openapi_spec_validator import validate
from referencing import Registry


def escape(token: str) -> str:
    return token.replace('~', '~0').replace('/', '~1')


def schemas_with_examples(node: Any, pointer: str = '') -> Iterator[tuple[str, Any]]:
    """Every schema object in the spec that carries an `example`, with its JSON pointer."""
    if isinstance(node, dict):
        looks_like_schema = any(k in node for k in ('type', '$ref', 'properties', 'enum'))
        if 'example' in node and looks_like_schema:
            yield pointer, node['example']
        for key, value in node.items():
            yield from schemas_with_examples(value, f'{pointer}/{escape(str(key))}')
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from schemas_with_examples(value, f'{pointer}/{i}')


def validator_for(pointer: str, registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(
        {'$ref': f'{BASE}app-api.yaml#{pointer}'},
        registry=registry,
        format_checker=FormatChecker(),
    )


def test_spec_is_valid_openapi_3_1(api: dict[str, Any]):
    validate(api)


def test_every_component_schema_has_an_example(api: dict[str, Any]):
    missing = [name for name, s in api['components']['schemas'].items() if 'example' not in s]
    assert missing == []


EXAMPLES = list(schemas_with_examples(load_api()))


@pytest.mark.parametrize(('pointer', 'example'), EXAMPLES, ids=[p for p, _ in EXAMPLES])
def test_example_matches_its_schema(pointer: str, example: Any, registry: Registry):
    validator_for(pointer, registry).validate(example)


def test_examples_were_found():
    assert len(EXAMPLES) >= 40


def test_schemas_reject_unknown_fields(registry: Registry):
    task = load_api()['components']['schemas']['Task']['example']
    with pytest.raises(ValidationError, match='Additional properties'):
        validator_for('/components/schemas/Task', registry).validate({**task, 'colour': 'red'})


def test_formats_are_checked(registry: Registry):
    task = load_api()['components']['schemas']['Task']['example']
    with pytest.raises(ValidationError, match='date-time'):
        validator_for('/components/schemas/Task', registry).validate(
            {**task, 'created_at': 'yesterday'}
        )


def test_every_operation_has_an_id_and_a_summary(api: dict[str, Any]):
    methods = {'get', 'post', 'put', 'patch', 'delete'}
    problems = [
        f'{method.upper()} {path}'
        for path, item in api['paths'].items()
        for method, op in item.items()
        if method in methods and not (op.get('operationId') and op.get('summary'))
    ]
    assert problems == []


def test_only_health_and_pair_are_public(api: dict[str, Any]):
    public = sorted(
        path
        for path, item in api['paths'].items()
        for method, op in item.items()
        if isinstance(op, dict) and op.get('security') == []
    )
    assert public == ['/health', '/pair']
