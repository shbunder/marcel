from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

CONTRACTS = Path(__file__).resolve().parent.parent
BASE = 'https://marcel.invalid/contracts/'


def load_api() -> dict[str, Any]:
    return yaml.safe_load((CONTRACTS / 'app-api.yaml').read_text())


def load_events() -> dict[str, Any]:
    return json.loads((CONTRACTS / 'events.schema.json').read_text())


YAML_DOCS = ('app-api.yaml', 'runner-api.yaml', 'session-api.yaml')
JSON_DOCS = ('events.schema.json', 'transcript.schema.json', 'channel.schema.json')


def load_yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONTRACTS / name).read_text())


def load_json(name: str) -> dict[str, Any]:
    return json.loads((CONTRACTS / name).read_text())


def build_registry() -> Registry:
    """Every contract under one base URI, so documents can $ref each other."""
    resources = [(BASE + n, Resource(load_yaml(n), DRAFT202012)) for n in YAML_DOCS]
    resources += [(BASE + n, Resource.from_contents(load_json(n))) for n in JSON_DOCS]
    return Registry().with_resources(resources)


@pytest.fixture(scope='session')
def api() -> dict[str, Any]:
    return load_api()


@pytest.fixture(scope='session')
def events() -> dict[str, Any]:
    return load_events()


@pytest.fixture(scope='session')
def registry() -> Registry:
    return build_registry()
