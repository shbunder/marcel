"""Finding and checking the examples inside an OpenAPI document."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from conftest import BASE
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry


def escape(token: str) -> str:
    return token.replace('~', '~0').replace('/', '~1')


def schemas_with_examples(node: Any, pointer: str = '') -> Iterator[tuple[str, Any]]:
    """Every schema object that carries an `example`, with its JSON pointer."""
    if isinstance(node, dict):
        looks_like_schema = any(k in node for k in ('type', '$ref', 'properties', 'enum'))
        if 'example' in node and looks_like_schema:
            yield pointer, node['example']
        for key, value in node.items():
            yield from schemas_with_examples(value, f'{pointer}/{escape(str(key))}')
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from schemas_with_examples(value, f'{pointer}/{i}')


def validator_for(doc: str, pointer: str, registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(
        {'$ref': f'{BASE}{doc}#{pointer}'},
        registry=registry,
        format_checker=FormatChecker(),
    )
