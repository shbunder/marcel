"""Reads `contracts/` and answers with its examples. The mock invents no data of its own."""

from __future__ import annotations

import copy
import json
import os
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

BASE = 'https://marcel.invalid/contracts/'
REPO = Path(__file__).resolve().parents[3]


def contracts_dir() -> Path:
    return Path(os.environ.get('MARCEL_CONTRACTS', REPO / 'contracts'))


@cache
def load_api() -> dict[str, Any]:
    return yaml.safe_load((contracts_dir() / 'app-api.yaml').read_text())


@cache
def load_events() -> dict[str, Any]:
    return json.loads((contracts_dir() / 'events.schema.json').read_text())


@cache
def load_transcript() -> dict[str, Any]:
    return json.loads((contracts_dir() / 'transcript.schema.json').read_text())


@cache
def registry() -> Registry:
    """The documents under one base URI, as the contract tests do, so they can $ref each other."""
    return Registry().with_resources(
        [
            (BASE + 'app-api.yaml', Resource(load_api(), DRAFT202012)),
            (BASE + 'events.schema.json', Resource.from_contents(load_events())),
            (BASE + 'transcript.schema.json', Resource.from_contents(load_transcript())),
        ]
    )


def schema_name(ref: str) -> str:
    return ref.rsplit('/', 1)[-1]


def component(name: str) -> dict[str, Any]:
    return load_api()['components']['schemas'][name]


def example(name: str) -> Any:
    """A fresh copy of a component schema's example."""
    return copy.deepcopy(component(name)['example'])


def validator(pointer: str) -> Draft202012Validator:
    """A validator for a pointer such as `app-api.yaml#/components/schemas/Task`."""
    return Draft202012Validator(
        {'$ref': BASE + pointer}, registry=registry(), format_checker=FormatChecker()
    )


def validate_event(frame: Any) -> None:
    validator('events.schema.json').validate(frame)


def operations() -> list[tuple[str, str, dict[str, Any]]]:
    """(method, path, operation) for every operation in the contract."""
    methods = {'get', 'post', 'put', 'patch', 'delete'}
    return [
        (m, p, op)
        for p, item in load_api()['paths'].items()
        for m, op in item.items()
        if m in methods
    ]


def _escape(token: str) -> str:
    return token.replace('~', '~0').replace('/', '~1')


def response_pointer(path: str, method: str, status: str | int) -> str | None:
    """Pointer to the JSON schema of an operation's response, or None when it has no JSON body."""
    resp = load_api()['paths'][path][method]['responses'].get(str(status))
    if resp is None:
        return None
    if '$ref' in resp:
        name = schema_name(resp['$ref'])
        base = f'app-api.yaml#/components/responses/{name}'
        resp = load_api()['components']['responses'][name]
    else:
        base = f'app-api.yaml#/paths/{_escape(path)}/{method}/responses/{status}'
    if 'application/json' not in resp.get('content', {}):
        return None
    return f'{base}/content/application~1json/schema'


def request_pointer(path: str, method: str) -> str | None:
    body = load_api()['paths'][path][method].get('requestBody')
    if body is None or 'application/json' not in body.get('content', {}):
        return None
    return (
        f'app-api.yaml#/paths/{_escape(path)}/{method}/requestBody/content/application~1json/schema'
    )
