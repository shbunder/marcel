"""Contract 0.7 (S-02.7): people, agent looks, quoted replies, mentions, trackers and a brain per
agent. app-api 0.7.0, runner-api 0.1.9, plus additions to events.schema.json and
channel.schema.json. All additive. The people part is retired by app-api 0.8.0 (S-02.13)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from conftest import BASE, CONTRACTS, load_json, load_yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry

METHODS = {'get', 'post', 'put', 'patch', 'delete'}


def check(doc: str, pointer: str, registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(
        {'$ref': f'{BASE}{doc}#{pointer}'}, registry=registry, format_checker=FormatChecker()
    )


def app(name: str, registry: Registry) -> Draft202012Validator:
    return check('app-api.yaml', f'/components/schemas/{name}', registry)


def schema(name: str) -> dict[str, Any]:
    return load_yaml('app-api.yaml')['components']['schemas'][name]


def example(name: str) -> Any:
    return copy.deepcopy(schema(name)['example'])


def operations() -> dict[str, tuple[str, str, dict[str, Any]]]:
    paths = load_yaml('app-api.yaml')['paths']
    return {
        op['operationId']: (method, path, op)
        for path, item in paths.items()
        for method, op in item.items()
        if method in METHODS
    }


def flat(text: str) -> str:
    return ' '.join(text.split())


def refused(validator: Draft202012Validator, instance: Any) -> None:
    with pytest.raises(ValidationError):
        validator.validate(instance)


def test_the_versions_were_bumped() -> None:
    assert load_yaml('app-api.yaml')['info']['version'] == '0.8.2'  # 0.8.2 in S-02.16
    assert load_yaml('runner-api.yaml')['info']['version'] == '0.1.14'  # 0.1.14 in S-02.13


# 1. People --------------------------------------------------------------------------------------
# app-api 0.8.0 (S-02.13, ADR-013) removed the people model on purpose: the `/users` routes, `User`,
# `NewUser`, `UserRole`, `Me.role`, `owner_only` and the owner-only mark. The tests that they exist
# are retired; test_contract_s02_13.py checks they are gone. What stays from 0.7 is below.


def test_new_error_codes_are_named() -> None:
    codes = schema('Error')['properties']['code']['description']
    for code in ('mention_not_found',):
        assert f'`{code}`' in codes, code


def test_a_pairing_code_comes_as_a_code_and_a_link(registry: Registry) -> None:
    issued = app('IssuedPairingCode', registry)
    body = example('IssuedPairingCode')
    issued.validate(body)
    assert '&code=' + body['code'] in body['link']
    refused(issued, {**body, 'link': 'https://marcel-bot.com/pair'})
    refused(issued, {k: v for k, v in body.items() if k != 'expires_at'})
    _, _, op = operations()['createPairingCode']
    assert op['responses']['201']['content']['application/json']['schema'] == {
        '$ref': '#/components/schemas/IssuedPairingCode'
    }


# 2. Agent looks ----------------------------------------------------------------------------------

# From the Pixel Zoo generator, scratch/spikes/SP5-avatar/zoo/zoo.js (ADR-008).
ANIMALS = ['giraffe', 'crocodile', 'frog', 'cat', 'fox', 'bear', 'panda', 'rabbit', 'dog', 'pig',
           'mouse']  # fmt: skip
COLORS = ['rose', 'coral', 'tangerine', 'honey', 'lime', 'green', 'teal', 'sky', 'blue',
          'lavender', 'plum', 'cocoa', 'sand', 'slate', 'snow', 'ink']  # fmt: skip
PERSONALITIES = ['professional', 'cheerful', 'curious', 'cozy', 'bold', 'cool', 'sporty', 'shy',
                 'grumpy']  # fmt: skip
MOODS = ['neutral', 'happy', 'laughing', 'love', 'excited', 'wink', 'proud', 'calm', 'thinking',
         'idea', 'focused', 'working', 'surprised', 'confused', 'worried', 'sad', 'crying', 'angry',
         'grumpy', 'sleepy', 'dizzy', 'embarrassed', 'singing', 'shocked', 'kiss']  # fmt: skip


def test_the_look_enums_are_the_zoos() -> None:
    assert schema('LookAnimal')['enum'] == ANIMALS
    assert schema('LookColor')['enum'] == ['natural', *COLORS]  # a name can pick `natural`
    assert schema('LookPersonality')['enum'] == PERSONALITIES
    assert schema('Mood')['enum'] == MOODS
    runner = load_yaml('runner-api.yaml')['components']['schemas']['PrepareBrain']
    assert runner['properties']['persona']['properties']['personality']['enum'] == PERSONALITIES


def test_a_look_sent_to_the_hub_is_all_three_known_values(registry: Registry) -> None:
    look = app('NewLook', registry)
    look.validate({'animal': 'giraffe', 'color': 'rose', 'personality': 'professional'})
    look.validate({'animal': 'frog', 'color': 'natural', 'personality': 'shy'})
    refused(look, {'animal': 'giraffe', 'color': 'rose'})
    refused(look, {'animal': 'unicorn', 'color': 'rose', 'personality': 'shy'})
    refused(look, {'animal': 'cat', 'color': '#f08aa0', 'personality': 'shy'})
    refused(look, {'animal': 'cat', 'color': 'rose', 'personality': 'shy', 'mood': 'happy'})


def test_a_look_from_the_hub_is_open_so_old_apps_still_read_it(registry: Registry) -> None:
    # 0.7.1: a new zoo animal must not break an older app's /me; it shows the default avatar.
    look = app('Look', registry)
    look.validate({'animal': 'unicorn', 'color': 'galaxy', 'personality': 'dreamy'})
    refused(look, {'animal': 'giraffe', 'color': 'rose'})
    refused(look, {'animal': '', 'color': 'rose', 'personality': 'shy'})
    refused(look, {'animal': 'cat', 'color': 'rose', 'personality': 'shy', 'mood': 'happy'})
    text = flat(schema('Look')['description'])
    assert 'treats a look with any unknown value as no look' in text and '`NewLook`' in text
    for name in ('animal', 'color', 'personality'):
        known = 'Look' + name.capitalize()
        assert f'`{known}`' in schema('Look')['properties'][name]['description']
    me = app('Me', registry)
    agent = {
        **example('Me')['agents'][0],
        'look': {'animal': 'unicorn', 'color': 'x', 'personality': 'y'},
    }
    me.validate({**example('Me'), 'agents': [agent]})


def test_what_the_hub_accepts_stays_strict() -> None:
    assert schema('NewAgent')['properties']['look']['$ref'] == '#/components/schemas/NewLook'
    assert schema('AgentUpdate')['properties']['look']['$ref'] == '#/components/schemas/NewLook'
    preview = load_yaml('app-api.yaml')['paths']['/avatar-previews']['get']['parameters']
    assert [p['schema']['$ref'].rsplit('/', 1)[1] for p in preview[:3]] == [
        'LookAnimal',
        'LookColor',
        'LookPersonality',
    ]


def agent_07() -> dict[str, Any]:
    agent = {k: v for k, v in example('Agent').items() if k not in ('palette', 'seed')}
    return {
        **agent,
        'look': {'animal': 'giraffe', 'color': 'rose', 'personality': 'professional'},
        'onboarded_at': '2026-10-13T17:25:00Z',
    }


def test_an_agent_has_a_look_and_palette_is_deprecated(registry: Registry) -> None:
    agent = app('Agent', registry)
    agent.validate(example('Agent'))  # a 0.6 agent still matches
    agent.validate(agent_07())
    props = schema('Agent')['properties']
    assert props['palette']['deprecated'] is True and props['seed']['deprecated'] is True
    assert props['animal']['readOnly'] is True
    assert 'look.animal' in props['animal']['description']
    assert 'animal' in schema('Agent')['required']
    refused(agent, {**agent_07(), 'onboarded_at': 'today'})
    refused(agent, {k: v for k, v in agent_07().items() if k != 'animal'})


def test_an_agent_update_takes_name_look_and_onboarded(registry: Registry) -> None:
    update = app('AgentUpdate', registry)
    update.validate(example('AgentUpdate'))
    update.validate({'look': {'animal': 'cat', 'color': 'sky', 'personality': 'cool'}})
    refused(update, {'look': {'animal': 'unicorn', 'color': 'sky', 'personality': 'cool'}})
    update.validate({'onboarded': True})
    update.validate({'palette': 'dusk'})  # deprecated, still accepted
    refused(update, {})
    refused(update, {'onboarded': False})
    refused(update, {'name': 'x' * 41})
    refused(update, {'look': {'animal': 'cat'}})
    assert schema('AgentUpdate')['properties']['palette']['deprecated'] is True


def test_the_avatar_sheet_is_served_as_json_and_png() -> None:
    _, path, op = operations()['getAgentAvatar']
    assert path == '/agents/{agent_id}/avatar'
    ok = op['responses']['200']
    assert set(ok['content']) == {'application/json', 'image/png'}
    assert 'ETag' in ok['headers']
    assert '304' in op['responses'] and '404' in op['responses']
    assert [p['name'] for p in op['parameters']] == ['If-None-Match']
    text = flat(op['description'])
    assert '`avatar-sheet.schema.json`' in text and '`avatar_etag`' in text
    sheet_schema = load_json('avatar-sheet.schema.json')
    sheet = ok['content']['application/json']['schema']['example']
    Draft202012Validator(sheet_schema).validate(sheet)
    assert sheet['frame'] == {'width': 32, 'height': 32}  # the zoo's grid
    used = {frame for loop in sheet['loops'].values() for frame, _ in loop}
    assert max(used) < sheet['frame_count']


def test_previews_take_a_look_and_a_mood() -> None:
    _, path, op = operations()['getAvatarPreview']
    assert path == '/avatar-previews'
    params = {p['name']: p for p in op['parameters']}
    assert {n: p.get('required', False) for n, p in params.items()} == {
        'animal': True,
        'color': True,
        'personality': True,
        'mood': False,
    }
    assert params['mood']['schema'] == {'$ref': '#/components/schemas/Mood'}
    assert set(op['responses']['200']['content']) == {'image/png'}
    assert {'400', '401'} <= set(op['responses'])


def test_agent_updated_carries_the_agent_and_its_avatar_etag(registry: Registry) -> None:
    event = check('events.schema.json', '', registry)
    [example_event] = load_json('events.schema.json')['$defs']['AgentUpdated']['examples']
    event.validate(example_event)
    assert example_event['type'] == 'agent.updated'
    data = example_event['data']
    event.validate({**example_event, 'data': {'agent': data['agent']}})  # no sheets built yet
    refused(event, {**example_event, 'data': {'avatar_etag': '"x"'}})
    refused(event, {**example_event, 'data': {**data, 'avatar_etag': ''}})
    refused(event, {**example_event, 'data': {**data, 'agent': {'id': 'agt_1'}}})


def test_the_operations_for_agents_say_what_changed() -> None:
    _, _, op = operations()['updateAgent']
    text = flat(op['description'])
    assert '`agent.updated`' in text and '`agent_renamed`' in text
    assert '404' in op['responses']


# 3. Quoted replies -------------------------------------------------------------------------------


def test_a_message_can_name_the_message_it_answers(registry: Registry) -> None:
    message = app('Message', registry)
    reply = {**example('Message'), 'reply_to_message_id': 'msg_01J9ZQZ1'}
    message.validate(reply)
    refused(message, {**reply, 'reply_to_message_id': 7})
    assert 'B-41' in schema('Message')['properties']['reply_to_message_id']['description']


def test_the_brain_reply_is_stored_and_a_rename_reaches_the_brain(registry: Registry) -> None:
    channel = load_json('channel.schema.json')['$defs']
    assert 'reply_to_message_id' in channel['Reply']['properties']['in_reply_to']['description']
    frame = check('channel.schema.json', '', registry)
    renamed = {
        'type': 'system_event',
        'seq': 7,
        'kind': 'agent_renamed',
        'text': 'You are Pip now.',
    }
    frame.validate({**renamed, 'data': {'name': 'Pip'}})
    refused(frame, renamed)
    refused(frame, {**renamed, 'data': {}})
    refused(frame, {**renamed, 'data': {'name': ''}})
    protocol = flat((CONTRACTS / 'channel-protocol.md').read_text())
    assert '`agent_renamed`' in protocol and '`data: {name}`' in protocol


# 4. Mentions -------------------------------------------------------------------------------------

MENTION = {'kind': 'task', 'id': 'tsk_01J9ZQ5', 'title': 'Fix flaky login test'}


def test_a_mention_names_a_quest_project_or_item(registry: Registry) -> None:
    mention = app('Mention', registry)
    for kind in ('task', 'project', 'artifact'):
        mention.validate({**MENTION, 'kind': kind})
    refused(mention, {**MENTION, 'kind': 'person'})
    refused(mention, {**MENTION, 'title': ''})
    refused(mention, {'kind': 'task', 'id': 'tsk_1'})


def test_messages_carry_mentions_both_ways(registry: Registry) -> None:
    new = app('NewMessage', registry)
    new.validate({'text': '@Fix flaky login test also the signup one', 'mentions': [MENTION]})
    refused(new, {'text': 'x', 'mentions': [MENTION] * 11})
    refused(new, {'text': 'x', 'mentions': [{'kind': 'task', 'id': 'tsk_1'}]})
    message = app('Message', registry)
    user_text = {**example('MessagePage')['items'][0], 'mentions': [MENTION]}
    message.validate(user_text)
    _, _, send = operations()['sendMessage']
    text = flat(send['description'])
    assert '`mention_not_found`' in text and '`project_id`' in text


def test_mentions_are_searched_at_most_twenty() -> None:
    method, path, op = operations()['searchMentions']
    assert (method, path) == ('get', '/mentions')
    params = {p['name']: p for p in op['parameters']}
    assert set(params) == {'q', 'kind'}
    assert not any(p.get('required') for p in params.values())
    assert params['kind']['schema'] == {'$ref': '#/components/schemas/MentionKind'}
    listing = op['responses']['200']['content']['application/json']['schema']
    assert listing['maxItems'] == 20
    assert listing['items'] == {'$ref': '#/components/schemas/Mention'}
    assert 'of every buddy' in flat(op['description'])  # one person since 0.8.0


def test_the_channel_mention_is_the_apps_mention(registry: Registry) -> None:
    user_message = load_json('channel.schema.json')['$defs']['UserMessage']
    item = user_message['properties']['mentions']['items']
    mention = schema('Mention')
    assert item['required'] == mention['required']
    assert item['properties']['kind']['enum'] == schema('MentionKind')['enum']
    assert item['properties']['title'] == {
        k: v for k, v in mention['properties']['title'].items() if k != 'description'
    }
    frame = check('channel.schema.json', '', registry)
    base = {
        'type': 'user_message',
        'seq': 3,
        'message_id': 'msg_1',
        'conversation_id': 'cnv_main_marcel',
        'text': 'is @Fix flaky login test done?',
    }
    frame.validate({**base, 'mentions': [MENTION]})
    refused(frame, {**base, 'mentions': [{**MENTION, 'kind': 'person'}]})
    protocol = (CONTRACTS / 'channel-protocol.md').read_text()
    assert 'mentions?}' in protocol and 'JSON string' in protocol


# 5. Trackers -------------------------------------------------------------------------------------


def test_a_project_may_have_a_tracker(registry: Registry) -> None:
    project = app('Project', registry)
    url = 'https://claude.ai/artifact/1V2swuomSbb9r1cB23Y2eh'
    project.validate({**example('Project'), 'tracker_url': url})
    refused(project, {**example('Project'), 'tracker_url': 'http://example.com/board'})
    # Only a worker's report sets it.
    assert 'tracker_url' not in schema('ProjectUpdate')['properties']


TRACKER = {
    'type': 'report',
    'call_id': 'c_1',
    'kind': 'artifact',
    'artifact': {
        'kind': 'link',
        'title': 'Marcel tracker',
        'url': 'https://claude.ai/artifact/1V2swuomSbb9r1cB23Y2eh',
        'role': 'tracker',
    },
}


def test_a_worker_reports_the_tracker_as_a_link(registry: Registry) -> None:
    frame = check('channel.schema.json', '', registry)
    frame.validate(TRACKER)
    for bad in (
        {**TRACKER['artifact'], 'kind': 'doc', 'path': '/tmp/tracker.md'},
        {**TRACKER['artifact'], 'kind': 'pr'},
        {**TRACKER['artifact'], 'url': 'http://example.com/board'},
        {**TRACKER['artifact'], 'role': 'board'},
    ):
        refused(frame, {**TRACKER, 'artifact': bad})
    plain = {k: v for k, v in TRACKER['artifact'].items() if k != 'role'}
    frame.validate({**TRACKER, 'artifact': {**plain, 'url': 'http://example.com/board'}})


# 6. A brain per agent ----------------------------------------------------------------------------


def runner_op() -> dict[str, Any]:
    return load_yaml('runner-api.yaml')['paths']['/brains']['post']


def test_the_runner_prepares_a_brain_folder(registry: Registry) -> None:
    op = runner_op()
    assert op['operationId'] == 'prepareBrain'
    assert set(op['responses']) == {'200', '400', '422', '503'}
    text = flat(op['description'])
    assert '`~/marcel/brains/<agent_slug>`' in text and '`agents/<agent_slug>/`' in text
    assert 'Safe to repeat' in text
    for code in ('render_failed', 'not_trusted'):
        assert f'`{code}`' in op['responses']['422']['description']
    body = check('runner-api.yaml', '/components/schemas/PrepareBrain', registry)
    good = load_yaml('runner-api.yaml')['components']['schemas']['PrepareBrain']['example']
    body.validate(good)
    for slug in ('../marcel', '-x', 'Pip', 'a/b', ''):
        refused(body, {**good, 'agent_slug': slug})
    refused(body, {**good, 'persona': {}})
    refused(body, {k: v for k, v in good.items() if k != 'persona'})
    done = check('runner-api.yaml', '/components/schemas/PreparedBrain', registry)
    done.validate(load_yaml('runner-api.yaml')['components']['schemas']['PreparedBrain']['example'])


def test_every_new_example_is_valid_json_for_its_schema(registry: Registry) -> None:
    for name in ('IssuedPairingCode', 'Look', 'NewLook', 'Mention', 'MentionKind', 'LookAnimal',
                 'LookColor', 'LookPersonality', 'Mood'):  # fmt: skip
        app(name, registry).validate(json.loads(json.dumps(example(name))))
