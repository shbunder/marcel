"""S-02.16 (B-50, SP13): buddies message each other. app-api 0.8.2 adds `MessageKind` `buddy` with
`Message.from_agent_id` and `to_agent_id`; brain-api 0.2.0 adds `POST /buddy-messages`, which the
RECEIVING brain's hook calls, keyed on Claude Code's `msg_id`."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from conftest import BASE, load_yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry


def check(doc: str, pointer: str, registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(
        {'$ref': f'{BASE}{doc}#{pointer}'}, registry=registry, format_checker=FormatChecker()
    )


def app_schema(name: str) -> dict[str, Any]:
    return load_yaml('app-api.yaml')['components']['schemas'][name]


def brain_schema(name: str) -> dict[str, Any]:
    return load_yaml('brain-api.yaml')['components']['schemas'][name]


def buddy_examples() -> list[dict[str, Any]]:
    return copy.deepcopy([e for e in app_schema('Message')['examples'] if e['kind'] == 'buddy'])


def route() -> dict[str, Any]:
    return load_yaml('brain-api.yaml')['paths']['/buddy-messages']['post']


def flat(text: str) -> str:
    return ' '.join(text.split())


# app-api 0.8.2 ----------------------------------------------------------------------------------


def test_the_versions_were_bumped() -> None:
    app = load_yaml('app-api.yaml')['info']
    brain = load_yaml('brain-api.yaml')['info']
    assert app['version'] == '0.8.2'
    assert brain['version'] == '0.2.0'
    assert '**0.8.2** (additive)' in app['description']
    assert '**0.2.0: buddies message each other**' in brain['description']


def test_message_kind_has_buddy() -> None:
    kinds = app_schema('MessageKind')
    assert kinds['enum'][-1] == 'buddy', 'a new value goes last, so older values keep their place'
    assert '`buddy`' in kinds['description']


def test_the_buddy_examples_are_valid_messages(registry: Registry) -> None:
    examples = buddy_examples()
    assert len(examples) == 2, 'one copy for the sender, one for the receiver'
    for example in examples:
        check('app-api.yaml', '/components/schemas/Message', registry).validate(example)


@pytest.mark.parametrize('field', ['text', 'from_agent_id', 'to_agent_id'])
def test_a_buddy_message_needs_text_and_both_buddies(field: str, registry: Registry) -> None:
    validator = check('app-api.yaml', '/components/schemas/Message', registry)
    for example in buddy_examples():
        del example[field]
        with pytest.raises(ValidationError):
            validator.validate(example)


def test_one_copy_in_each_buddys_main_conversation() -> None:
    sender, receiver = buddy_examples()
    assert sender['role'] == 'agent' and receiver['role'] == 'system'
    assert sender['agent_id'] == sender['from_agent_id'], 'an agent message credits its writer'
    assert 'agent_id' not in receiver, 'a system message has no agent_id'
    assert sender['conversation_id'] != receiver['conversation_id']
    for field in ('text', 'from_agent_id', 'to_agent_id', 'created_at'):
        assert sender[field] == receiver[field], field
    assert sender['from_agent_id'] != sender['to_agent_id']


def test_a_buddy_message_is_a_message_created_event(registry: Registry) -> None:
    """The WebSocket carries it unchanged: `message.created` reuses `Message`."""
    validator = check('events.schema.json', '', registry)
    for i, example in enumerate(buddy_examples()):
        event = {'id': i + 1, 'type': 'message.created', 'at': example['created_at']}
        validator.validate({**event, 'data': example})


def test_the_new_fields_are_optional_for_older_kinds() -> None:
    """Additive: a 0.8.1 message without them stays valid."""
    message = app_schema('Message')
    assert {'from_agent_id', 'to_agent_id'} <= set(message['properties'])
    assert not {'from_agent_id', 'to_agent_id'} & set(message['required'])
    text = flat(message['properties']['from_agent_id']['description'])
    assert 'role `agent`' in text and 'role `system`' in text


def test_the_rollout_order_is_written_down() -> None:
    text = flat(load_yaml('app-api.yaml')['info']['description'])
    assert 'the app learns it (S-22.28) before a hub sends it (S-22.13)' in text


# brain-api 0.2.0 --------------------------------------------------------------------------------


def test_the_route_records_and_answers_each_msg_id_once() -> None:
    op = route()
    assert op['operationId'] == 'brainRecordBuddyMessage'
    body = op['requestBody']['content']['application/json']['schema']
    assert body == {'$ref': '#/components/schemas/BuddyMessage'}
    recorded = {'$ref': '#/components/schemas/BuddyMessageRecorded'}
    for status in ('201', '200'):
        assert op['responses'][status]['content']['application/json']['schema'] == recorded
    text = flat(op['description'])
    assert 'answers 201; a repeat answers 200 with the same message ids and stores nothing' in text
    assert 'RECEIVED' in text and '`UserPromptSubmit`' in text


def test_the_receiver_is_the_caller_not_a_body_field() -> None:
    """SP13: the receiving brain reports, so its own session header names the receiver."""
    schema = brain_schema('BuddyMessage')
    assert set(schema['properties']) == {'from_name', 'text', 'msg_id'}
    assert set(schema['required']) == {'from_name', 'text', 'msg_id'}
    assert schema['additionalProperties'] is False
    assert 'the receiver is the caller (`X-Marcel-Session`)' in flat(schema['description'])


def test_the_request_example_is_valid_and_strict(registry: Registry) -> None:
    validator = check('brain-api.yaml', '/components/schemas/BuddyMessage', registry)
    example = copy.deepcopy(brain_schema('BuddyMessage')['example'])
    validator.validate(example)
    for field in ('from_name', 'text', 'msg_id'):
        broken = {k: v for k, v in example.items() if k != field}
        with pytest.raises(ValidationError):
            validator.validate(broken)
        with pytest.raises(ValidationError):
            validator.validate({**example, field: ''})
    with pytest.raises(ValidationError):
        validator.validate({**example, 'to_session_id': 'f1e2'})


def test_the_answer_names_both_copies(registry: Registry) -> None:
    schema = brain_schema('BuddyMessageRecorded')
    check('brain-api.yaml', '/components/schemas/BuddyMessageRecorded', registry).validate(
        schema['example']
    )
    sender, receiver = buddy_examples()
    assert schema['example']['sender_message_id'] == sender['id']
    assert schema['example']['receiver_message_id'] == receiver['id']
    assert schema['example']['from_agent_id'] == sender['from_agent_id']
    assert schema['example']['to_agent_id'] == receiver['to_agent_id']


def test_an_unknown_or_archived_sender_is_404_and_nothing_is_stored() -> None:
    responses = route()['responses']
    text = flat(responses['404']['description'])
    assert '`agent_not_found`' in text and 'archived' in text and 'Nothing is stored' in text
    assert 'own buddy' in flat(responses['400']['description'])
    assert '`agent_not_found` (404)' in flat(load_yaml('brain-api.yaml')['info']['description'])


def test_the_route_has_the_brain_routes_auth() -> None:
    """No `security` override: the hub secret and a brain session, as on every brain route."""
    op = route()
    assert 'security' not in op
    for status, ref in (
        ('401', 'Unauthorized'),
        ('403', 'NotBrain'),
        ('413', 'TooLarge'),
        ('503', 'NoSecret'),
    ):
        assert op['responses'][status] == {'$ref': f'#/components/responses/{ref}'}, status


def test_it_is_not_a_read_before_hello_route() -> None:
    """It writes, so a rollover's new brain before its hello is refused (brain-api 0.1.4)."""
    description = load_yaml('brain-api.yaml')['info']['description']
    before_hello = flat(description.split('**Read before hello**')[1].split('**Its own')[0])
    assert '/buddy-messages' not in before_hello
    assert '401 `unknown_session`' in flat(route()['description'])
