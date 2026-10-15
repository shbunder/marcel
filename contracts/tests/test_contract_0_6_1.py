"""Rules clarified in contract 0.6.1: runner-api 0.1.8, session-api 0.1.4, brain-api 0.1.2 and the
channel's note text and call_ack codes. All additive."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import BASE, CONTRACTS, load_json, load_yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry


def flat(text: str) -> str:
    return ' '.join(text.split())


def test_the_versions_were_bumped() -> None:
    # Bumped again by test_contract_0_7.py and test_contract_s02_9.py.
    assert load_yaml('runner-api.yaml')['info']['version'] == '0.1.13'  # 0.1.13 in S-02.12
    assert load_yaml('session-api.yaml')['info']['version'] == '0.1.7'  # 0.1.7 in S-02.10
    assert load_yaml('brain-api.yaml')['info']['version'] == '0.1.5'  # 0.1.5 in S-02.13


# session-api.yaml: report tokens (S-07.1) -------------------------------------------------------


def test_each_spawn_and_follow_up_mints_its_own_report_token() -> None:
    text = flat(load_yaml('session-api.yaml')['info']['description'])
    assert 'mints a new token for the spawn and for each follow-up message' in text
    assert 'earlier tokens of the same task stay valid until the task finishes' in text
    assert 'refused once the task is `done`, `failed` or `stopped`' in text


# Note text must hold a non-space character (both paths) ---------------------------------------

NOTE_SCHEMAS = [
    ('session-api.yaml', '/components/schemas/Report', {'kind': 'note'}),
    ('channel.schema.json', '/$defs/Report', {'type': 'report', 'call_id': 'c1', 'kind': 'note'}),
]


@pytest.mark.parametrize(('doc', 'pointer', 'base'), NOTE_SCHEMAS)
def test_a_note_of_only_spaces_is_refused(
    registry: Registry, doc: str, pointer: str, base: dict[str, Any]
) -> None:
    check = Draft202012Validator({'$ref': f'{BASE}{doc}#{pointer}'}, registry=registry)
    for blank in (' ', '   ', '\n\t '):
        with pytest.raises(ValidationError):
            check.validate({**base, 'text': blank})
    check.validate({**base, 'text': '  The meter is in the garage.  '})
    check.validate({**base, 'kind': 'progress', 'text': '   '})  # the rule is the note's alone


# call_ack error codes (S-04.14) ------------------------------------------------------------------

CALL_ACK_CODES = (
    'not_allowed', 'bad_reply', 'bad_report', 'bad_next', 'bad_note', 'unknown_artifact',
    'replaced', 'too_many_notes', 'notes_full', 'memory_unavailable',
)  # fmt: skip


def test_every_call_ack_code_the_hub_uses_is_named() -> None:
    ack = load_json('channel.schema.json')['$defs']['CallAck']['properties']['error']
    protocol = flat((CONTRACTS / 'channel-protocol.md').read_text())
    for code in CALL_ACK_CODES:
        assert f'`{code}`' in ack['description'], code
        assert f'`{code}`' in protocol, code


def test_the_cloud_path_names_the_note_refusals() -> None:
    report = load_yaml('session-api.yaml')['paths']['/session/report']['post']['responses']
    assert '`too_many_notes`' in report['409']['description']
    assert '`notes_full`' in report['409']['description']
    assert '`memory_unavailable`' in report['503']['description']
    bad = load_yaml('session-api.yaml')['components']['responses']['BadRequest']['description']
    for code in ('bad_note', 'bad_next', 'bad_report'):
        assert f'`{code}`' in bad, code


# Digests (S-05.7) ------------------------------------------------------------------------------


def test_digest_id_is_passed_when_present() -> None:
    schemas = load_yaml('brain-api.yaml')['components']['schemas']
    [new_artifact] = [s for s in schemas.values() if 'in_reply_to' in s.get('properties', {})]
    text = flat(new_artifact['properties']['in_reply_to']['description'])
    assert 'This hub always sends `digest_id`' in text
    assert 'when `data.digest_id` is present' in text
    event = load_json('channel.schema.json')['$defs']['SystemEvent']
    assert 'this hub always sends it' in event['properties']['data']['description']
    digest = event['allOf'][0]['then']['properties']['data']
    assert 'digest_id' not in digest['required']  # still optional


# Harry's two always-shown tools are pre-allowed for brain and side sessions (S-15.1) -----------


def test_harrys_two_tools_are_pre_allowed_by_name() -> None:
    text = flat(load_yaml('runner-api.yaml')['info']['description'])
    assert '`mcp__plugin_marcel_harry__harry_find_tools`' in text
    assert '`mcp__plugin_marcel_harry__harry_mark_done`' in text
    assert 'mcp__plugin_marcel_harry__*' not in text
    assert "Workers get the plugin's `harry` server but none of its tools pre-allowed" in text


def test_report_429_is_only_too_many_reports_for_a_valid_token():
    r429 = load_yaml('session-api.yaml')['paths']['/session/report']['post']['responses']['429']
    assert 'too_many_reports' in r429['description'] and 'wrong token' in r429['description']
