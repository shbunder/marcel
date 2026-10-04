"""channel.schema.json: each frame kind is exemplified, unambiguous, and strict."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import BASE, load_json
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry

SCHEMA = load_json('channel.schema.json')
STRUCTURAL = {'ToHub', 'ToSession', 'Artifact'}
FRAMES = {k: v for k, v in SCHEMA['$defs'].items() if k not in STRUCTURAL}
CASES = [(n, ex) for n, d in FRAMES.items() for ex in d.get('examples', [])]


def frame_validator(registry: Registry, ref: str = '') -> Draft202012Validator:
    return Draft202012Validator(
        {'$ref': f'{BASE}channel.schema.json{ref}'},
        registry=registry,
        format_checker=FormatChecker(),
    )


def test_every_frame_kind_has_an_example():
    assert [n for n, d in FRAMES.items() if not d.get('examples')] == []


@pytest.mark.parametrize(('name', 'example'), CASES, ids=[n for n, _ in CASES])
def test_example_matches_its_frame_and_only_that_one(name: str, example: Any, registry: Registry):
    frame_validator(registry, f'#/$defs/{name}').validate(example)
    frame_validator(registry).validate(example)  # oneOf: exactly one frame kind matches


BAD = [
    {'type': 'welcome', 'role': 'worker', 'agent_id': 'a'},
    {'type': 'welcome', 'role': 'brain', 'agent_id': 'a'},
    {'type': 'report', 'call_id': 'c', 'kind': 'done'},
    {'type': 'report', 'call_id': 'c', 'kind': 'progress'},
    {'type': 'report', 'call_id': 'c', 'kind': 'artifact'},
    {
        'type': 'report',
        'call_id': 'c',
        'kind': 'artifact',
        'artifact': {'kind': 'pr', 'title': 'no url'},
    },
    {
        'type': 'report',
        'call_id': 'c',
        'kind': 'artifact',
        'artifact': {'kind': 'file', 'title': 'no path'},
    },
    {'type': 'permission_decision', 'seq': 1, 'request_id': 'hello', 'behavior': 'allow'},
    {'type': 'hello', 'session_id': 'not-a-uuid', 'secret': 'x' * 40, 'plugin_version': '0.1.0'},
    {
        'type': 'user_message',
        'seq': 1,
        'message_id': 'm',
        'conversation_id': 'c',
        'text': 't',
        'role': 'x',
    },
]


@pytest.mark.parametrize('frame', BAD, ids=[f'{b["type"]}-{i}' for i, b in enumerate(BAD)])
def test_bad_frame_is_rejected(frame: dict[str, Any], registry: Registry):
    with pytest.raises(ValidationError):
        frame_validator(registry).validate(frame)


def test_request_ids_follow_claude_codes_alphabet():
    """SP1: request ids are five letters from a-z without 'l', as the official channels use."""
    pattern = FRAMES['PermissionRequest']['properties']['request_id']['pattern']
    assert (
        pattern
        == FRAMES['PermissionDecision']['properties']['request_id']['pattern']
        == '^[a-km-z]{5}$'
    )
