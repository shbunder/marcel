"""The hub's frame checks match channel.schema.json, and refuse what the contract refuses."""

import json
from pathlib import Path
from typing import Any

import pytest

from marcel_hub.channel.frames import ARTIFACT_FIELDS, FIELDS, BadFrame, parse, version_tuple

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / 'contracts' / 'channel.schema.json').read_text()
)
DEFS = SCHEMA['$defs']
TO_HUB = {ref['$ref'].rsplit('/', 1)[1] for ref in DEFS['ToHub']['oneOf']}
NAMES = {
    'Hello': 'hello',
    'Reply': 'reply',
    'Report': 'report',
    'PermissionRequest': 'permission_request',
    'Ack': 'ack',
    'Ping': 'ping',
}


def fields_of(definition: dict[str, Any]) -> dict[str, bool]:
    required = set(definition.get('required', []))
    return {k: k in required for k in definition['properties'] if k != 'type'}


def test_the_field_tables_match_the_contract() -> None:
    assert set(NAMES) == TO_HUB
    for name, kind in NAMES.items():
        assert fields_of(DEFS[name]) == FIELDS[kind], name
    assert fields_of(DEFS['Artifact']) == ARTIFACT_FIELDS


def test_every_contract_example_parses() -> None:
    for name in NAMES:
        for example in DEFS[name].get('examples', []):
            assert parse(json.dumps(example)) == example


HELLO = {
    'type': 'hello',
    'session_id': '3986e1fc-278e-4b56-ba17-ad79a8130252',
    'secret': 'x' * 32,
    'plugin_version': '0.1.0',
}


@pytest.mark.parametrize(
    ('frame', 'why'),
    [
        ('[]', 'not an object'),
        ('{"type": "welcome"}', 'unknown frame type'),
        (json.dumps({**HELLO, 'session_id': 'brain'}), 'not a UUID'),
        (json.dumps({**HELLO, 'resume_after': -1}), 'bad resume_after'),
        (json.dumps({**HELLO, 'resume_after': True}), 'bad resume_after'),
        (json.dumps({**HELLO, 'role': 'brain'}), 'unknown fields role'),
        (json.dumps({**HELLO, 'secret': 7}), 'wrong type for secret'),
        ('{"type": "ack", "seq": 0}', 'bad seq'),
        ('{"type": "ack"}', 'lacks seq'),
        (
            '{"type": "permission_request", "request_id": "QIWXO", "tool_name": "Bash", '
            '"description": "d"}',
            'bad request_id',
        ),
        ('{"type": "report", "call_id": "c", "kind": "nap"}', 'unknown report kind'),
        ('{"type": "report", "call_id": "c", "kind": "progress"}', 'needs text'),
        ('{"type": "report", "call_id": "c", "kind": "artifact"}', 'needs an artifact'),
        ('{"type": "report", "call_id": "c", "kind": "done"}', 'needs a summary'),
        ('{"type": "report", "call_id": "c", "kind": "done", "summary": "s", "artifacts": {}}',
         'not a list'),
        ('{"type": "report", "call_id": "c", "kind": "artifact", "artifact": 1}', 'not an object'),
        ('{"type": "report", "call_id": "c", "kind": "artifact", '
         '"artifact": {"kind": "pr", "title": "t"}}', 'needs a url'),
        ('{"type": "report", "call_id": "c", "kind": "artifact", '
         '"artifact": {"kind": "doc", "title": "t"}}', 'needs a path'),
        ('{"type": "report", "call_id": "c", "kind": "artifact", '
         '"artifact": {"kind": "gif", "title": "t"}}', 'unknown artifact kind'),
        ('{"type": "report", "call_id": "c", "kind": "artifact", "artifact": '
         '{"kind": "link", "title": "t", "url": "u", "summary": "' + 'x' * 201 + '"}}',
         'longer than 200'),
    ],
)  # fmt: skip
def test_bad_frames_are_refused(frame: str, why: str) -> None:
    with pytest.raises(BadFrame, match=why):
        parse(frame)


def test_not_json_is_refused() -> None:
    with pytest.raises(BadFrame, match='not JSON'):
        parse('{')


@pytest.mark.parametrize(
    ('version', 'parsed'),
    [('0.1.0', (0, 1, 0)), ('1', (1, 0, 0)), ('0.2.0-dev', (0, 2, 0)), ('dev', None)],
)
def test_plugin_versions(version: str, parsed: tuple[int, ...] | None) -> None:
    assert version_tuple(version) == parsed
