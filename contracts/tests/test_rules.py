"""The conditional rules a lane would otherwise guess: each bad case must be rejected."""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import BASE, CONTRACTS, load_api
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry
from test_task_states import transitions

SCHEMAS = load_api()['components']['schemas']


def check(name: str, instance: Any, registry: Registry) -> None:
    Draft202012Validator(
        {'$ref': f'{BASE}app-api.yaml#/components/schemas/{name}'},
        registry=registry,
        format_checker=FormatChecker(),
    ).validate(instance)


def example(name: str) -> dict[str, Any]:
    return dict(SCHEMAS[name]['example'])


BAD = [
    ('PushTarget', {'kind': 'apns'}),
    ('PushTarget', {'kind': 'ntfy', 'apns_token': 'abc'}),
    ('AgentState', {'kind': 'working'}),
    ('Message', {k: v for k, v in example('Message').items() if k != 'milestone'}),
    (
        'Message',
        {
            'id': 'm',
            'conversation_id': 'c',
            'role': 'user',
            'kind': 'text',
            'created_at': '2026-10-04T18:00:00Z',
        },
    ),
    ('Artifact', {k: v for k, v in example('Artifact').items() if k != 'url'}),
    (
        'Artifact',
        {'id': 'a', 'kind': 'doc', 'title': 'Report', 'created_at': '2026-10-04T18:00:00Z'},
    ),
    ('Approval', {**example('Approval'), 'tool': 'self_change'}),
    (
        'PushNotification',
        {k: v for k, v in example('PushNotification').items() if k != 'approval_id'},
    ),
    ('PushNotification', {**example('PushNotification'), 'deep_link': 'https://evil.example/x'}),
    (
        'TaskEvent',
        {
            'task_id': 't',
            'seq': 1,
            'type': 'state',
            'at': '2026-10-04T18:00:00Z',
            'data': {'reason': 'no target state'},
        },
    ),
    (
        'TaskEvent',
        {
            'task_id': 't',
            'seq': 1,
            'type': 'permission',
            'at': '2026-10-04T18:00:00Z',
            'data': {'tool': 'Bash', 'summary': 'no approval id'},
        },
    ),
    ('PairingCode', 'https://marcel-bot.com/pair?code=K7Q2M9'),
]


@pytest.mark.parametrize(
    ('name', 'instance'), BAD, ids=[f'{n}-{i}' for i, (n, _) in enumerate(BAD)]
)
def test_bad_case_is_rejected(name: str, instance: Any, registry: Registry):
    with pytest.raises(ValidationError):
        check(name, instance, registry)


def test_working_agent_with_count_is_accepted(registry: Registry):
    check('AgentState', {'kind': 'working', 'active_tasks': 2}, registry)


MEMORY_PATH = next(
    p['schema']
    for p in load_api()['paths']['/memory/files/{path}']['parameters']
    if p['name'] == 'path'
)


@pytest.mark.parametrize('path', ['MEMORY.md', 'memory/2026-10-04.md', 'people/Anna-Lena.md'])
def test_memory_path_accepts(path: str):
    Draft202012Validator(MEMORY_PATH).validate(path)


@pytest.mark.parametrize(
    'path', ['../secrets.md', 'memory/../../x.md', '/etc/passwd.md', 'notes.txt', 'a b.md', '']
)
def test_memory_path_rejects(path: str):
    with pytest.raises(ValidationError):
        Draft202012Validator(MEMORY_PATH).validate(path)


def test_a_session_stuck_starting_can_go_silent():
    """B-23: a session hung at startup must not stay `starting` forever."""
    assert any('starting' in src and 'silent' in dst for src, dst in transitions())


def dashboard_validator() -> Draft202012Validator:
    schema = json.loads((CONTRACTS / 'dashboard.schema.json').read_text())
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_dashboard_examples_are_valid():
    validator = dashboard_validator()
    examples = validator.schema['examples']
    assert examples
    for ex in examples:
        validator.validate(ex)


def test_dashboard_rejects_unknown_widget():
    with pytest.raises(ValidationError):
        dashboard_validator().validate(
            {'version': 1, 'title': 'x', 'widgets': [{'type': 'script', 'code': 'alert(1)'}]}
        )
