"""S-02.9: the session-end hooks (session-api 0.1.6), the brain's day summary (brain-api 0.1.3), the
`ended_without_report` reason (task-states.md) and the pinned claude version (runner-api 0.1.10).
All additive; answers contract requests S-05.8, S-06.8 and S-03.10."""

from __future__ import annotations

import re
from typing import Any

import pytest
from conftest import BASE, CONTRACTS, load_yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry


def check(doc: str, name: str, registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(
        {'$ref': f'{BASE}{doc}#/components/schemas/{name}'},
        registry=registry,
        format_checker=FormatChecker(),
    )


def flat(text: str) -> str:
    return ' '.join(text.split())


def refused(validator: Draft202012Validator, instance: Any) -> None:
    with pytest.raises(ValidationError):
        validator.validate(instance)


def test_the_versions_were_bumped() -> None:
    assert load_yaml('session-api.yaml')['info']['version'] == '0.1.6'
    assert load_yaml('brain-api.yaml')['info']['version'] == '0.1.3'
    assert load_yaml('runner-api.yaml')['info']['version'] == '0.1.10'


# 1. Session end ----------------------------------------------------------------------------------


def session_end() -> tuple[dict[str, Any], dict[str, Any]]:
    item = load_yaml('session-api.yaml')['paths']['/channel/session-end']
    return item, item['post']


def test_session_end_is_on_the_nuc_port_outside_api() -> None:
    item, op = session_end()
    assert [s['url'] for s in item['servers']] == ['http://127.0.0.1:7420']
    assert op['operationId'] == 'sessionEnded'
    assert op['security'] == [{'secret': [], 'session': []}]
    schemes = load_yaml('session-api.yaml')['components']['securitySchemes']
    assert schemes['secret']['name'] == 'X-Marcel-Secret'
    assert schemes['session']['name'] == 'X-Marcel-Session'
    text = flat(load_yaml('session-api.yaml')['info']['description'])
    assert 'not reachable through the tunnel' in text and '`cf-connecting-ip`' in text


def test_session_end_answers_whether_a_report_is_owed(registry: Registry) -> None:
    _, op = session_end()
    assert set(op['responses']) == {'200', '400', '401', '503'}
    ok = op['responses']['200']['content']['application/json']['schema']
    assert ok == {'$ref': '#/components/schemas/SessionEndAnswer'}
    for code in ('bad_secret', 'unknown_session'):
        assert f'`{code}`' in op['responses']['401']['description']
    assert '`no_secret`' in op['responses']['503']['description']
    text = flat(op['description'])
    assert '`working` or `needs_you`' in text and '`ended_without_report`' in text
    answer = check('session-api.yaml', 'SessionEndAnswer', registry)
    answer.validate({'report_owed': False})
    refused(answer, {})
    refused(answer, {'report_owed': 'yes'})


def test_the_hook_says_which_event_and_why(registry: Registry) -> None:
    body = check('session-api.yaml', 'SessionEnd', registry)
    body.validate({'event': 'stop'})
    body.validate({'event': 'session_end', 'reason': 'logout'})
    body.validate({'event': 'session_end', 'reason': 'a_reason_claude_code_adds_later'})
    refused(body, {})
    refused(body, {'event': 'crash'})
    refused(body, {'event': 'session_end', 'reason': ''})
    refused(body, {'event': 'stop', 'session_id': 'x'})  # the session is the header


def test_the_protocol_says_who_calls_it() -> None:
    text = flat((CONTRACTS / 'channel-protocol.md').read_text())
    assert '## Session-end hooks' in (CONTRACTS / 'channel-protocol.md').read_text()
    assert '`POST http://127.0.0.1:7420/channel/session-end`' in text
    assert '`report_owed`' in text and 'never looped' in text


# 2. The reason -----------------------------------------------------------------------------------


def test_ending_without_a_report_is_a_failed_transition() -> None:
    rows = [
        line
        for line in (CONTRACTS / 'task-states.md').read_text().splitlines()
        if 'ended_without_report' in line
    ]
    assert len(rows) == 1
    cells = [c.strip() for c in rows[0].strip('|').split('|')]
    assert cells[0] == 'working, needs_you, silent' and cells[1] == 'failed'
    assert re.search(r'`SessionEnd` hook', cells[2])


# 3. The day so far -------------------------------------------------------------------------------


def test_the_brain_can_read_the_day_so_far(registry: Registry) -> None:
    op = load_yaml('brain-api.yaml')['paths']['/summary']['get']
    assert op['operationId'] == 'brainGetSummary'
    assert set(op['responses']) == {'200', '401', '403', '503'}
    assert op['responses']['403'] == {'$ref': '#/components/responses/NotBrain'}
    assert 'compact' in op['description'] and 'ADR-010' in op['description']
    summary = check('brain-api.yaml', 'DaySummary', registry)
    good = load_yaml('brain-api.yaml')['components']['schemas']['DaySummary']['example']
    summary.validate(good)
    refused(summary, {**good, 'text': ''})
    refused(summary, {**good, 'text': 'x' * 4097})
    refused(summary, {**good, 'date': '13 October'})
    refused(summary, {'text': 'no date'})


# 4. The pinned claude ----------------------------------------------------------------------------

STARTS = [
    ('/sessions', 'post'),
    ('/sessions/{session_id}/resume', 'post'),
    ('/sessions/{session_id}/fork', 'post'),
    ('/cloud/sessions', 'post'),
]


@pytest.mark.parametrize(('path', 'method'), STARTS)
def test_every_start_refuses_another_claude_version(path: str, method: str) -> None:
    op = load_yaml('runner-api.yaml')['paths'][path][method]
    assert '`claude_version`' in op['responses']['422']['description']


def test_the_pin_says_both_versions_on_spawn() -> None:
    text = flat(
        load_yaml('runner-api.yaml')['paths']['/sessions']['post']['responses']['422'][
            'description'
        ]
    )
    assert 'nothing was started' in text and '`message` names both versions' in text


def test_health_names_the_expected_version(registry: Registry) -> None:
    health = load_yaml('runner-api.yaml')['components']['schemas']['Health']
    assert 'claude_version_expected' not in health['required']  # optional
    assert '`ok` is false' in flat(health['properties']['claude_version_expected']['description'])
    check('runner-api.yaml', 'Health', registry).validate(
        {**health['example'], 'ok': False, 'claude_version_expected': '2.1.290 (Claude Code)'}
    )
