"""transcript.schema.json: its examples, and real recorded transcripts (SP2) normalised into it."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from conftest import BASE, CONTRACTS, load_json
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from reference_normalizer import normalize, normalize_file
from referencing import Registry

FIXTURES = CONTRACTS / 'tests' / 'fixtures' / 'transcripts'
SCHEMA = load_json('transcript.schema.json')


def event_validator(registry: Registry, ref: str = '') -> Draft202012Validator:
    return Draft202012Validator(
        {'$ref': f'{BASE}transcript.schema.json{ref}'},
        registry=registry,
        format_checker=FormatChecker(),
    )


DEF_EXAMPLES = [(n, ex) for n, d in SCHEMA['$defs'].items() for ex in d.get('examples', [])]


@pytest.mark.parametrize(('name', 'example'), DEF_EXAMPLES, ids=[n for n, _ in DEF_EXAMPLES])
def test_def_examples_are_valid(name: str, example: Any, registry: Registry):
    event_validator(registry, f'#/$defs/{name}').validate(example)


def test_every_data_shape_has_an_example():
    data_defs = [n for n in SCHEMA['$defs'] if n != 'TranscriptEvent']
    assert [n for n in data_defs if not SCHEMA['$defs'][n].get('examples')] == []


def test_top_level_example_is_valid(registry: Registry):
    for example in SCHEMA['examples']:
        event_validator(registry).validate(example)


def events_of(name: str) -> list[dict[str, Any]]:
    path = FIXTURES / name
    return normalize_file(path.read_text(), session_id=path.stem)


TRANSCRIPTS = sorted(p.name for p in FIXTURES.glob('*.jsonl'))


@pytest.mark.parametrize('name', TRANSCRIPTS)
def test_every_normalised_event_is_valid(name: str, registry: Registry):
    events = events_of(name)
    assert events, f'{name} gave no events'
    validator = event_validator(registry)
    for event in events:
        validator.validate(event)


def test_happy_path_has_the_whole_story():
    counts = Counter(e['type'] for e in events_of('t1-happy-path.jsonl'))
    for kind in (
        'text',
        'tool_call',
        'tool_result',
        'diff',
        'subagent_start',
        'subagent_stop',
        'turn_end',
    ):
        assert counts[kind] >= 1, f'no {kind} event'
    assert counts['raw'] == 0, 'every line type in the recording is known'


def test_the_fix_is_a_diff():
    diffs = [e for e in events_of('t1-happy-path.jsonl') if e['type'] == 'diff']
    lines = [line for d in diffs for h in d['data']['hunks'] for line in h['lines']]
    assert '+    return a - b' in lines and '-    return a + b' in lines


def test_subagent_start_names_what_the_agent_call_asked_for():
    [start] = [e['data'] for e in events_of('t1-happy-path.jsonl') if e['type'] == 'subagent_start']
    assert start['description'] == 'Find buggy function'
    assert start['subagent_type'] == 'general-purpose'


def test_denied_tool_use_is_marked():
    results = [
        e['data'] for e in events_of('t3-permission-denied.jsonl') if e['type'] == 'tool_result'
    ]
    assert any(r.get('denied') for r in results)


def test_offsets_increase_and_resume_cleanly():
    events = events_of('t2-permission-approved.jsonl')
    offsets = [e['offset'] for e in events]
    assert offsets == sorted(offsets)
    assert offsets[-1] == len((FIXTURES / 't2-permission-approved.jsonl').read_bytes())


def test_unknown_line_type_becomes_raw(registry: Registry):
    events = normalize({'type': 'brand-new-thing', 'x': 1}, session_id='s', offset=10)
    assert [e['type'] for e in events] == ['raw']
    event_validator(registry).validate(events[0])


@pytest.mark.parametrize(
    'text',
    [
        "You've hit your session limit · resets 2:29pm",
        'Fable 5 requires usage credits. Turn them on in your settings.',
    ],
)
def test_usage_limit_error_is_flagged(text: str):
    line = {
        'type': 'assistant',
        'isApiErrorMessage': True,
        'api_error_status': 429,
        'timestamp': '2026-10-04T12:00:00Z',
        'message': {'content': [{'type': 'text', 'text': text}]},
    }
    [event] = normalize(line, session_id='s', offset=1)
    assert event['type'] == 'error' and event['data']['usage_limit'] is True


@pytest.mark.parametrize(
    'event',
    [
        {
            'session_id': 's',
            'offset': 1,
            'at': '2026-10-04T12:00:00Z',
            'type': 'text',
            'data': {'text': 'no role'},
        },
        {
            'session_id': 's',
            'offset': 1,
            'at': '2026-10-04T12:00:00Z',
            'type': 'diff',
            'data': {'tool_use_id': 't'},
        },
        {
            'session_id': 's',
            'offset': 1,
            'at': '2026-10-04T12:00:00Z',
            'type': 'tool_call',
            'data': {'tool': 'Bash'},
        },
        {
            'session_id': 's',
            'offset': 1,
            'at': '2026-10-04T12:00:00Z',
            'type': 'permission',
            'data': {},
        },
        *[
            {
                'session_id': 's',
                'offset': 1,
                'at': '2026-10-04T12:00:00Z',
                'type': kind,
                'data': data,
            }
            for kind, data in [
                ('thinking', {}),
                ('tool_result', {'tool_use_id': 't', 'text': 'no is_error'}),
                ('subagent_start', {'tool_use_id': 't'}),
                ('subagent_stop', {'agent_id': 'a'}),
                ('turn_end', {'message_count': 3}),
                ('error', {'api_error_status': 500}),
                ('raw', {'entry': {}}),
            ]
        ],
    ],
)
def test_bad_event_is_rejected(event: dict[str, Any], registry: Registry):
    with pytest.raises(ValidationError):
        event_validator(registry).validate(event)


def test_app_transcript_types_are_a_subset():
    """The app's TaskEventType must accept every transcript type the hub forwards."""
    from conftest import load_api

    app_types = set(load_api()['components']['schemas']['TaskEventType']['enum'])
    forwarded = set(SCHEMA['$defs']['TranscriptEvent']['properties']['type']['enum']) - {
        'thinking',
        'turn_end',
    }
    assert forwarded <= app_types


def test_fixtures_are_where_the_test_expects():
    assert Path(FIXTURES).is_dir() and TRANSCRIPTS
