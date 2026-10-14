"""Rules added in app-api 0.6.0, runner-api 0.1.7, session-api 0.1.2 and brain-api 0.1.1."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import BASE, CONTRACTS, load_json, load_yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from reference_normalizer import normalize_file
from referencing import Registry


def validator(doc: str, pointer: str, registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(
        {'$ref': f'{BASE}{doc}#{pointer}'}, registry=registry, format_checker=FormatChecker()
    )


def app_schema(name: str) -> dict[str, Any]:
    return load_yaml('app-api.yaml')['components']['schemas'][name]


def flat(text: str) -> str:
    return ' '.join(text.split())


def test_the_versions_were_bumped() -> None:
    # Later contracts bumped these again (test_contract_0_6_1.py, test_contract_0_7.py,
    # test_contract_s02_9.py).
    assert load_yaml('app-api.yaml')['info']['version'] == '0.7.2'  # 0.7.2 in S-02.10
    assert load_yaml('runner-api.yaml')['info']['version'] == '0.1.11'  # 0.1.11 in S-02.10
    assert load_yaml('session-api.yaml')['info']['version'] == '0.1.7'  # 0.1.7 in S-02.10
    assert load_yaml('brain-api.yaml')['info']['version'] == '0.1.4'  # 0.1.4 in S-02.10


# runner-api.yaml: cloud sessions (S-03.6) ------------------------------------------------------


def cloud_ops() -> tuple[dict[str, Any], dict[str, Any]]:
    paths = load_yaml('runner-api.yaml')['paths']
    return (
        paths['/cloud/sessions']['post'],
        paths['/cloud/sessions/{session_id}/messages']['post'],
    )


def test_a_cloud_session_may_name_its_repo(registry: Registry) -> None:
    spawn, _ = cloud_ops()
    body = spawn['requestBody']['content']['application/json']['schema']
    assert body['properties']['repo'] == {'$ref': '#/components/schemas/CloudRepo'}
    check = validator('runner-api.yaml', '/components/schemas/CloudRepo', registry)
    check.validate({'url': 'https://github.com/shbunder/marcel'})
    check.validate({'url': 'https://github.com/shbunder/marcel', 'base': 'release/1.2'})
    for bad in (
        {},
        {'url': 'https://user@github.com/shbunder/marcel'},
        {'url': 'https://github.com/shbunder/marcel', 'base': 'a..b'},
        {'url': 'https://github.com/shbunder/marcel', 'base': '-flag'},
        {'url': 'https://github.com/shbunder/marcel', 'task_id': 'tsk_1'},
    ):
        with pytest.raises(ValidationError):
            check.validate(bad)


def test_cloud_operations_answer_400_and_503_like_the_others() -> None:
    for op in cloud_ops():
        assert {'400', '422', '503'} <= set(op['responses'])
        assert '`bad_request`' in op['responses']['400']['description']
        text = op['responses']['503']['description']
        assert '`claude_timeout`' in text and '`claude_unavailable`' in text
    spawn, _ = cloud_ops()
    assert '`clone_failed`' in spawn['responses']['422']['description']
    assert '`git_timeout`' in spawn['responses']['503']['description']


@pytest.mark.parametrize('name', ['CloudSpawned', 'CloudAck'])
@pytest.mark.parametrize(
    ('url', 'ok'),
    [
        ('https://claude.ai/code/session_0168oBNCs8Q2BrHDFu6tYUdh', True),
        ('https://claude.ai/code/session_0168oBNCs8Q2BrHDFu6tYUdh?from=cli&m=0', False),
        ('https://evil.example/code/session_0168oBNCs8Q2BrHDFu6tYUdh', False),
        ('http://claude.ai/code/session_0168oBNCs8Q2BrHDFu6tYUdh', False),
    ],
)
def test_the_cloud_url_is_the_bare_claude_ai_page(
    registry: Registry, name: str, url: str, ok: bool
) -> None:
    schemas = load_yaml('runner-api.yaml')['components']['schemas']
    assert (
        '`https://claude.ai/code/<session_id>`' in schemas[name]['properties']['url']['description']
    )
    check = validator('runner-api.yaml', f'/components/schemas/{name}', registry)
    body = {**schemas[name]['example'], 'url': url}
    if ok:
        check.validate(body)
    else:
        with pytest.raises(ValidationError):
            check.validate(body)


# transcript.schema.json: token counts on turn_end (S-06.2) -------------------------------------

USAGE = {
    'input_tokens': 2,
    'cache_creation_input_tokens': 730,
    'cache_read_input_tokens': 50361,
    'output_tokens': 258,
}


def test_a_turn_end_may_carry_token_counts(registry: Registry) -> None:
    check = validator('transcript.schema.json', '/$defs/TurnEnd', registry)
    check.validate({'duration_ms': 1, 'usage': USAGE})
    for bad in (
        {k: v for k, v in USAGE.items() if k != 'output_tokens'},
        {**USAGE, 'input_tokens': -1},
        {**USAGE, 'thinking_tokens': 3},
    ):
        with pytest.raises(ValidationError):
            check.validate({'duration_ms': 1, 'usage': bad})


def test_real_transcripts_give_token_counts_at_turn_end(registry: Registry) -> None:
    path = CONTRACTS / 'tests' / 'fixtures' / 'transcripts' / 't1-happy-path.jsonl'
    events = normalize_file(path.read_text(), 't1', with_usage=True)
    check = validator('transcript.schema.json', '', registry)
    ends = [e for e in events if e['type'] == 'turn_end']
    assert ends and all('usage' in e['data'] for e in ends)
    for event in events:
        check.validate(event)
    assert sum(ends[0]['data']['usage'].values()) > 1000, 'a real context is thousands of tokens'
    without = normalize_file(path.read_text(), 't1')
    assert not [e for e in without if 'usage' in e['data']]


# app-api.yaml: rollovers (S-06.2, S-12.9) ------------------------------------------------------


def test_the_owner_can_ask_for_a_rollover() -> None:
    op = load_yaml('app-api.yaml')['paths']['/agents/{agent_id}/brain/rollover']['post']
    assert op['operationId'] == 'rollOverBrain'
    assert 'requestBody' not in op
    assert set(op['responses']) == {'202', '401', '404', '409', '503'}
    assert '`rollover_running`' in op['responses']['409']['description']
    assert '`rollover_running`' in app_schema('Error')['properties']['code']['description']


def test_past_rollovers_can_be_read(registry: Registry) -> None:
    op = load_yaml('app-api.yaml')['paths']['/agents/{agent_id}/brain/rollovers']['get']
    assert op['operationId'] == 'listBrainRollovers'
    limit = next(p for p in op['parameters'] if p['name'] == 'limit')
    assert limit['schema'] == {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 10}
    check = validator('app-api.yaml', '/components/schemas/Rollover', registry)
    example = app_schema('Rollover')['example']
    check.validate(example)
    for bad in ({**example, 'reason': 'bored'}, {k: v for k, v in example.items() if k != 'at'}):
        with pytest.raises(ValidationError):
            check.validate(bad)
    event_reasons = load_json('events.schema.json')['$defs']['BrainRollover']['properties']['data'][
        'properties'
    ]['reason']['enum']
    assert app_schema('Rollover')['properties']['reason']['enum'] == event_reasons


def test_a_replaced_brains_reply_is_handed_to_the_new_one() -> None:
    ack = load_json('channel.schema.json')['$defs']['CallAck']['properties']['error']
    assert '`replaced`' in ack['description'] and '`data.unsent_reply`' in ack['description']
    protocol = flat((CONTRACTS / 'channel-protocol.md').read_text())
    assert '## Brain rollover' in protocol
    assert '`rejected unknown_session`' in protocol
    assert '`error.code: replaced`' in protocol and 'unsent_reply' in protocol


# Worker notes (S-06.6) -------------------------------------------------------------------------

NOTE_SCHEMAS = [
    ('session-api.yaml', '/components/schemas/Report', {'kind': 'note'}),
    ('channel.schema.json', '/$defs/Report', {'type': 'report', 'call_id': 'c1', 'kind': 'note'}),
]


@pytest.mark.parametrize(('doc', 'pointer', 'base'), NOTE_SCHEMAS)
def test_a_worker_can_report_one_project_note(
    registry: Registry, doc: str, pointer: str, base: dict[str, Any]
) -> None:
    check = validator(doc, pointer, registry)
    check.validate({**base, 'text': 'The Luminus contract ends on 1 March 2027.'})
    check.validate({**base, 'text': 'x' * 1000})
    for bad in (base, {**base, 'text': ''}, {**base, 'text': 'x' * 1001}):
        with pytest.raises(ValidationError):
            check.validate(bad)
    progress = {**base, 'kind': 'progress', 'text': 'x' * 1001}
    check.validate(progress)  # the 1000-character limit is the note's alone


def test_the_hub_caps_notes_per_task() -> None:
    report = load_yaml('session-api.yaml')['paths']['/session/report']['post']
    assert '`too_many_notes`' in report['responses']['409']['description']
    text = flat(load_yaml('session-api.yaml')['components']['schemas']['Report']['description'])
    assert 'At most 10 notes per task' in text
    assert '`memory/projects/<slug>.md`' in text
    ack = load_json('channel.schema.json')['$defs']['CallAck']['properties']['error']
    assert '`too_many_notes`' in ack['description']


# Schedules and digests (S-09.1, S-09.2) --------------------------------------------------------


def test_schedule_errors_are_named() -> None:
    codes = app_schema('Error')['properties']['code']['description']
    assert '`schedule_not_found` (404)' in codes and '`schedule_builtin` (409)' in codes
    delete = load_yaml('app-api.yaml')['paths']['/schedules/{schedule_id}']['delete']
    assert '`schedule_builtin`' in delete['responses']['409']['description']
    assert '`schedule_not_found`' in delete['responses']['404']['description']


def test_builtin_schedule_ids_name_their_agent() -> None:
    schedule = app_schema('Schedule')
    assert schedule['example']['id'] == 'sch_digest_morning_agt_marcel'
    assert '`sch_digest_<digest>_<agent id>`' in schedule['properties']['builtin']['description']


def test_a_missed_schedule_is_a_note_push() -> None:
    text = flat(app_schema('PushNotification')['properties']['category']['description'])
    assert 'a scheduled job that did not run or ran late' in text


def digest_due(data: Any) -> dict[str, Any]:
    return {'type': 'system_event', 'seq': 5, 'kind': 'digest_due', 'text': 'Due.', 'data': data}


def test_a_digest_due_event_says_which_digest(registry: Registry) -> None:
    check = validator('channel.schema.json', '/$defs/SystemEvent', registry)
    good = {'digest': 'morning', 'title': 'Morning digest — Tuesday 13 October', 'facts': {}}
    check.validate(digest_due(good))
    check.validate(digest_due({**good, 'digest_id': 'dig_01', 'artifact_id': 'art_01'}))
    for bad in (
        None,
        {k: v for k, v in good.items() if k != 'title'},
        {k: v for k, v in good.items() if k != 'facts'},
        {**good, 'digest': 'hourly'},
    ):
        event = digest_due(bad)
        if bad is None:
            del event['data']
        with pytest.raises(ValidationError):
            check.validate(event)
    other = {**digest_due({}), 'kind': 'task_failed'}
    check.validate(other)


def test_a_published_digest_can_name_its_event(registry: Registry) -> None:
    check = validator('brain-api.yaml', '/components/schemas/NewArtifact', registry)
    doc = {'kind': 'doc', 'title': 'Evening digest', 'path': '/tmp/d.md'}
    check.validate({**doc, 'in_reply_to': 'dig_01'})
    with pytest.raises(ValidationError):
        check.validate({**doc, 'in_reply_to': ''})
    publish = load_yaml('brain-api.yaml')['paths']['/artifacts']['post']
    assert '`digest_not_found`' in publish['responses']['404']['description']


def test_the_digest_board_is_a_doc() -> None:
    assert "The agent's digest board" in flat(app_schema('ArtifactKind')['description'])


# PR cards and memory (S-04.13, S-12.9) ---------------------------------------------------------


def test_a_pr_card_says_when_it_was_last_refreshed(registry: Registry) -> None:
    check = validator('app-api.yaml', '/components/schemas/Artifact', registry)
    card = app_schema('Artifact')['example']
    check.validate({**card, 'pr': {**card['pr'], 'refreshed_at': '2026-10-04T18:25:00Z'}})
    with pytest.raises(ValidationError):
        check.validate({**card, 'pr': {**card['pr'], 'refreshed_at': 'five minutes ago'}})


def test_memory_saves_send_the_bare_etag_and_conflict_is_409() -> None:
    put = load_yaml('app-api.yaml')['paths']['/memory/files/{path}']['put']
    text = flat(put['description'])
    assert 'clients should send the **bare** etag' in text
    assert "not HTTP's usual 412" in text
    assert '409' in put['responses'] and '412' not in put['responses']


# Push targets (S-08.2) -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('token', 'ok'),
    [
        ('a1' * 32, True),
        ('A1' * 100, True),
        ('a1' * 31, False),
        ('a' * 201, False),
        ('zz' * 32, False),
    ],
)
def test_an_apns_token_is_hex(registry: Registry, token: str, ok: bool) -> None:
    check = validator('app-api.yaml', '/components/schemas/PushTarget', registry)
    target = {'kind': 'apns', 'apns_token': token}
    if ok:
        check.validate(target)
    else:
        with pytest.raises(ValidationError):
            check.validate(target)
    put = load_yaml('app-api.yaml')['paths']['/devices/{device_id}/push']['put']
    assert '400' in put['responses']


def test_adopt_never_answers_429_so_the_route_stays_hidden():
    responses = load_yaml('session-api.yaml')['paths']['/session/adopt']['post']['responses']
    assert '429' not in responses and '404' in responses
