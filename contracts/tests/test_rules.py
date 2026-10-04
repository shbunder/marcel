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
    (
        'Message',
        {
            'id': 'm',
            'conversation_id': 'c',
            'role': 'agent',
            'kind': 'approval',
            'task_id': 't',
            'created_at': '2026-10-04T18:00:00Z',
        },
    ),
    (
        'Message',
        {
            'id': 'm',
            'conversation_id': 'c',
            'role': 'user',
            'kind': 'quote',
            'text': 'quoted',
            'created_at': '2026-10-04T18:00:00Z',
        },
    ),
    ('Artifact', {k: v for k, v in example('Artifact').items() if k != 'pr'}),
    (
        'TaskEvent',
        {
            'task_id': 't',
            'seq': 1,
            'type': 'progress',
            'at': '2026-10-04T18:00:00Z',
            'data': {'message': 'not the field name'},
        },
    ),
    (
        'TaskEvent',
        {
            'task_id': 't',
            'seq': 1,
            'type': 'artifact',
            'at': '2026-10-04T18:00:00Z',
            'data': {'kind': 'pr', 'title': 'no artifact id'},
        },
    ),
    (
        'TaskEvent',
        {
            'task_id': 't',
            'seq': 1,
            'type': 'note',
            'at': '2026-10-04T18:00:00Z',
            'data': {'text': 'no author'},
        },
    ),
    ('Me', {**example('Me'), 'push': {'apns': False, 'ntfy': True}}),
    ('Agent', {k: v for k, v in example('Agent').items() if k != 'main_conversation_id'}),
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
    'path',
    [
        '../secrets.md',
        'memory/../../x.md',
        '/etc/passwd.md',
        'notes.txt',
        'a b.md',
        '',
        '.git/config.md',
        'memory/.hidden.md',
    ],
)
def test_memory_path_rejects(path: str):
    with pytest.raises(ValidationError):
        Draft202012Validator(MEMORY_PATH).validate(path)


@pytest.mark.parametrize(
    ('source', 'target', 'why'),
    [
        ('starting', 'silent', 'B-23: a session hung at startup must not stay starting forever'),
        ('starting', 'needs_you', 'a permission or login can come before the first step'),
        ('starting', 'done', 'a short task can finish between two runner polls'),
        ('unknown', 'starting', 'the runner can come back while a session is still starting'),
        ('done', 'working', 'B-05: a follow-up goes to the same session'),
    ],
)
def test_required_transition_exists(source: str, target: str, why: str):
    assert any(source in src and target in dst for src, dst in transitions()), why


def test_server_url_is_under_api():
    """Every app path (/health, /pair and /ws too) lives under /api, the tunnel's only prefix."""
    servers = [s['url'] for s in load_api()['servers']]
    assert servers and all(url.endswith('/api') for url in servers)


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
