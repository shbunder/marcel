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


def build_registry() -> Registry:
    """Both documents under one base URI, so events.schema.json can $ref app-api.yaml."""
    return Registry().with_resources(
        [
            (BASE + 'app-api.yaml', Resource(load_api(), DRAFT202012)),
            (BASE + 'events.schema.json', Resource.from_contents(load_events())),
        ]
    )


@pytest.fixture(scope='session')
def api() -> dict[str, Any]:
    return load_api()


@pytest.fixture(scope='session')
def events() -> dict[str, Any]:
    return load_events()


@pytest.fixture(scope='session')
def registry() -> Registry:
    return build_registry()
