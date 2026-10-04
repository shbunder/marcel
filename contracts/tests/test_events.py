from __future__ import annotations

from typing import Any

import pytest
from conftest import BASE, load_events
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry

REQUIRED_TYPES = {
    'message.created',
    'task.created',
    'task.updated',
    'task.event',
    'approval.created',
    'approval.resolved',
    'artifact.created',
    'agent.state',
    'usage.updated',
}
STRUCTURAL = {'Event', 'Envelope'}


def event_validator(registry: Registry, ref: str = '') -> Draft202012Validator:
    return Draft202012Validator(
        {'$ref': f'{BASE}events.schema.json{ref}'},
        registry=registry,
        format_checker=FormatChecker(),
    )


DEFS = {k: v for k, v in load_events()['$defs'].items() if k not in STRUCTURAL}
CASES = [(name, ex) for name, d in DEFS.items() for ex in d.get('examples', [])]


def test_every_event_kind_has_an_example():
    assert [name for name, d in DEFS.items() if not d.get('examples')] == []


def test_every_required_event_type_is_defined():
    defined = {d['properties']['type']['const'] for d in DEFS.values()}
    assert defined >= REQUIRED_TYPES


@pytest.mark.parametrize(('name', 'example'), CASES, ids=[n for n, _ in CASES])
def test_example_matches_its_definition_and_only_that_one(
    name: str, example: dict[str, Any], registry: Registry
):
    event_validator(registry, f'#/$defs/{name}').validate(example)
    event_validator(registry).validate(example)  # Event is oneOf: exactly one kind may match


def test_wrong_payload_for_type_is_rejected(registry: Registry):
    usage = DEFS['UsageUpdated']['examples'][0]
    with pytest.raises(ValidationError):
        event_validator(registry).validate({**usage, 'type': 'task.updated'})


def test_extra_envelope_field_is_rejected(registry: Registry):
    hello = DEFS['Hello']['examples'][0]
    with pytest.raises(ValidationError):
        event_validator(registry).validate({**hello, 'debug': True})
