"""S-02.13 (ADR-013): one person, buddies on phones, push through the relay. app-api 0.8.0
(breaking: the people model goes), brain-api 0.1.5, the new push-relay.yaml 0.1.0,
events.schema.json `device.updated`, and a tracker that is a claude.ai artifact set by the
kickoff quest only."""

from __future__ import annotations

import base64
import copy
import json
from typing import Any

import pytest
from conftest import BASE, CONTRACTS, load_json, load_yaml
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry

METHODS = {'get', 'post', 'put', 'patch', 'delete'}
VECTOR: dict[str, Any] = json.loads(
    (CONTRACTS / 'tests' / 'fixtures' / 'sealed-push' / 'vector.json').read_text()
)
INFO = b'marcel-push-v1'


def check(doc: str, pointer: str, registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(
        {'$ref': f'{BASE}{doc}#{pointer}'}, registry=registry, format_checker=FormatChecker()
    )


def app(name: str, registry: Registry) -> Draft202012Validator:
    return check('app-api.yaml', f'/components/schemas/{name}', registry)


def relay(name: str, registry: Registry) -> Draft202012Validator:
    return check('push-relay.yaml', f'/components/schemas/{name}', registry)


def schema(name: str) -> dict[str, Any]:
    return load_yaml('app-api.yaml')['components']['schemas'][name]


def example(name: str) -> Any:
    return copy.deepcopy(schema(name)['example'])


def operations(doc: str = 'app-api.yaml') -> dict[str, tuple[str, str, dict[str, Any]]]:
    return {
        op['operationId']: (method, path, op)
        for path, item in load_yaml(doc)['paths'].items()
        for method, op in item.items()
        if method in METHODS
    }


def flat(text: str) -> str:
    return ' '.join(text.split())


def refused(validator: Draft202012Validator, instance: Any) -> None:
    with pytest.raises(ValidationError):
        validator.validate(instance)


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def test_the_versions_were_bumped() -> None:
    assert load_yaml('app-api.yaml')['info']['version'] == '0.8.2'  # 0.8.2 in S-02.16
    assert load_yaml('brain-api.yaml')['info']['version'] == '0.2.0'  # 0.2.0 in S-02.16
    assert load_yaml('push-relay.yaml')['info']['version'] in (
        '0.1.0',
        '0.1.1',
    )  # 0.1.1: register 429
    assert load_yaml('runner-api.yaml')['info']['version'] == '0.1.14'


# 1. One person -----------------------------------------------------------------------------------


def test_the_people_model_is_gone() -> None:
    text = (CONTRACTS / 'app-api.yaml').read_text()
    assert 'x-owner-only' not in text and '/users' not in text
    api = load_yaml('app-api.yaml')
    assert not [p for p in api['paths'] if p.startswith('/users')]
    for name in ('User', 'NewUser', 'UserRole'):
        assert name not in api['components']['schemas'], name
    assert 'OwnerOnly' not in api['components']['responses']
    assert 'UserId' not in api['components']['parameters']
    assert 'people' not in [t['name'] for t in api['tags']]
    assert 'role' not in schema('Me')['properties']
    assert {'user_id', 'name'} <= set(schema('Me')['required'])
    for method, path, op in operations().values():
        if path != '/ws':  # a refused WebSocket upgrade is a 403, not an owner check
            assert '403' not in op['responses'], f'{method} {path}: no route is owner-only now'


def test_me_has_no_role(registry: Registry) -> None:
    me = app('Me', registry)
    me.validate(example('Me'))
    refused(me, {**example('Me'), 'role': 'owner'})


def test_any_phone_answers_any_approval() -> None:
    _, _, op = operations()['answerApproval']
    text = flat(op['description'])
    assert 'Any paired phone' in text and '`owner_only`' not in text


def test_the_error_codes_follow() -> None:
    codes = flat(schema('Error')['properties']['code']['description'])
    assert '`last_buddy`' in codes
    assert '0.8.0 removed `owner_only`, `user_not_found` and `cannot_remove_owner`' in codes


# 2. Buddies --------------------------------------------------------------------------------------


def test_a_buddy_is_added_with_a_name_and_maybe_a_look(registry: Registry) -> None:
    method, path, op = operations()['createAgent']
    assert (method, path) == ('post', '/agents')
    body = op['requestBody']['content']['application/json']['schema']
    assert body == {'$ref': '#/components/schemas/NewAgent'}
    assert op['responses']['201']['content']['application/json']['schema'] == {
        '$ref': '#/components/schemas/Agent'
    }
    new = app('NewAgent', registry)
    new.validate(example('NewAgent'))
    new.validate({'name': 'Pip'})  # look picked from the name
    refused(new, {})
    refused(new, {'name': ''})
    refused(
        new, {'name': 'Pip', 'look': {'animal': 'unicorn', 'color': 'rose', 'personality': 'shy'}}
    )
    refused(new, {'name': 'Pip', 'user_id': 'usr_owner'})
    assert 'from the name' in flat(op['description'])


def test_a_buddy_is_archived_but_never_the_last(registry: Registry) -> None:
    method, path, op = operations()['archiveAgent']
    assert (method, path) == ('post', '/agents/{agent_id}/archive')
    assert '`last_buddy`' in op['responses']['409']['description']
    assert {'200', '401', '404', '409'} == set(op['responses'])
    text = flat(op['description'])
    assert 'stay readable' in text and '`device.updated`' in text and '`agent.updated`' in text
    agent = app('Agent', registry)
    archived = {**example('Agent'), 'archived_at': '2026-10-15T19:00:00Z'}
    agent.validate(archived)
    refused(agent, {**archived, 'archived_at': 'yesterday'})


def test_me_lists_the_live_buddies() -> None:
    assert 'Archived buddies are not listed' in schema('Me')['properties']['agents']['description']


# 3. A phone's buddy ------------------------------------------------------------------------------


def test_every_device_says_which_buddy_it_shows(registry: Registry) -> None:
    device = app('Device', registry)
    device.validate(example('Device'))
    assert 'agent_id' in schema('Device')['required']
    refused(device, {k: v for k, v in example('Device').items() if k != 'agent_id'})
    assert schema('Device')['properties']['push']['enum'] == ['apns', 'ntfy', 'relay', 'none']
    assert '410' in schema('Device')['properties']['push']['description']


def test_a_phone_switches_buddy(registry: Registry) -> None:
    method, path, op = operations()['updateDevice']
    assert (method, path) == ('patch', '/devices/{device_id}')
    assert op['requestBody']['content']['application/json']['schema'] == {
        '$ref': '#/components/schemas/DeviceUpdate'
    }
    assert op['responses']['200']['content']['application/json']['schema'] == {
        '$ref': '#/components/schemas/Device'
    }
    assert '`agent_not_found`' in op['responses']['404']['description']
    assert '`device.updated`' in op['description']
    update = app('DeviceUpdate', registry)
    update.validate(example('DeviceUpdate'))
    refused(update, {})
    refused(update, {'agent_id': ''})
    refused(update, {'agent_id': 'agt_1', 'name': 'x'})


def test_device_updated_carries_the_device(registry: Registry) -> None:
    event = check('events.schema.json', '', registry)
    [frame] = load_json('events.schema.json')['$defs']['DeviceUpdated']['examples']
    event.validate(frame)
    assert frame['type'] == 'device.updated'
    refused(event, {**frame, 'data': {k: v for k, v in frame['data'].items() if k != 'agent_id'}})
    refused(event, {**frame, 'data': {**frame['data'], 'agent_id': 7}})


def test_no_user_events() -> None:
    types = [
        d['properties']['type']['const']
        for d in load_json('events.schema.json')['$defs'].values()
        if 'const' in d.get('properties', {}).get('type', {})
    ]
    assert 'device.updated' in types
    assert not [t for t in types if t.startswith('user.')]


def test_any_phone_makes_a_pairing_code_for_a_buddy(registry: Registry) -> None:
    method, path, op = operations()['createPairingCode']
    assert (method, path) == ('post', '/pairing-codes')
    assert op['requestBody']['required'] is False
    assert op['responses']['201']['content']['application/json']['schema'] == {
        '$ref': '#/components/schemas/IssuedPairingCode'
    }
    assert '`agent_not_found`' in op['responses']['404']['description']
    body = app('NewPairingCode', registry)
    body.validate(example('NewPairingCode'))
    body.validate({})
    refused(body, {'user_id': 'usr_owner'})


def test_a_pairing_link_may_name_the_buddy(registry: Registry) -> None:
    link = app('PairingCode', registry)
    hub = 'marcel://pair?hub=https%3A%2F%2Fmarcel-bot.com%2Fapi&code=K7Q2M9'
    link.validate(hub)
    link.validate(hub + '&buddy=agt_01JA3E2')
    link.validate(example('IssuedPairingCode')['link'])
    refused(link, hub + '&buddy=')
    refused(link, hub + '&buddy=agt 1')
    refused(link, hub + '&user=usr_1')


# 4. Relay push -----------------------------------------------------------------------------------

RELAY_TARGET = {
    'kind': 'relay',
    'relay_url': 'https://marcel-relay.example.workers.dev',
    'handle': 'hY3kQ9vT2mW8pL5xR1nB7c',
    'send_secret': 'Zk1QbXJ4V2c5dEw3cU5hYjJ5Rk1uVDhvSzZlUjRzVmQ',
    'device_key': VECTOR['device_key'],
}


def test_a_phone_registers_a_relay_target(registry: Registry) -> None:
    target = app('PushTarget', registry)
    target.validate(RELAY_TARGET)
    for field in ('relay_url', 'handle', 'send_secret', 'device_key'):
        refused(target, {k: v for k, v in RELAY_TARGET.items() if k != field})
    refused(target, {**RELAY_TARGET, 'relay_url': 'http://relay.example.com'})
    refused(target, {**RELAY_TARGET, 'handle': 'short'})
    refused(target, {**RELAY_TARGET, 'device_key': b64(bytes(31))})  # not 32 bytes
    refused(target, {**RELAY_TARGET, 'device_key': b64(bytes(32)) + 'A'})
    refused(target, {**RELAY_TARGET, 'device_key': VECTOR['device_key'][:-1] + 'B'})
    # The apns and ntfy kinds still work as before.
    target.validate(example('PushTarget'))
    refused(target, {'kind': 'apns'})


def test_me_says_whether_the_relay_is_on(registry: Registry) -> None:
    me = app('Me', registry)
    push = example('Me')['push']
    assert push['relay'] is True
    me.validate({**example('Me'), 'push': {k: v for k, v in push.items() if k != 'relay'}})
    refused(me, {**example('Me'), 'push': {**push, 'relay': 'yes'}})


def test_the_relay_carries_only_sealed_bytes(registry: Registry) -> None:
    sealed = app('SealedPush', registry)
    sealed.validate(VECTOR['sealed'])
    assert example('SealedPush') == VECTOR['sealed']
    refused(sealed, {**VECTOR['sealed'], 'v': 2})
    refused(sealed, {**VECTOR['sealed'], 'title': 'Pip needs you'})
    refused(sealed, {**VECTOR['sealed'], 'nonce': b64(bytes(8))})
    refused(sealed, {k: v for k, v in VECTOR['sealed'].items() if k != 'box'})
    content = app('SealedPushContent', registry)
    content.validate(VECTOR['content'])
    assert example('SealedPushContent') == VECTOR['content']
    content.validate({k: v for k, v in VECTOR['content'].items() if k != 'agent_id'})  # Marcel-wide
    refused(content, {**VECTOR['content'], 'link': 'https://evil.example'})
    refused(content, {k: v for k, v in VECTOR['content'].items() if k != 'link'})


def open_box(device_private: bytes, sealed: dict[str, Any]) -> bytes:
    """What the phone does: the scheme exactly as `SealedPush` describes it."""
    device = X25519PrivateKey.from_private_bytes(device_private)
    device_key = device.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    eph = unb64(sealed['eph'])
    shared = device.exchange(X25519PublicKey.from_public_bytes(eph))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=eph + device_key, info=INFO).derive(
        shared
    )
    return ChaCha20Poly1305(key).decrypt(unb64(sealed['nonce']), unb64(sealed['box']), INFO)


def test_the_known_answer_vector_opens_with_the_device_key() -> None:
    plain = open_box(unb64(VECTOR['device_private_key']), VECTOR['sealed'])
    assert plain.decode() == VECTOR['plaintext']
    assert json.loads(plain) == VECTOR['content']


def test_the_hub_side_seals_to_the_same_bytes() -> None:
    """Sealing with the vector's fixed one-time key and nonce gives its `box`, byte for byte."""
    eph_private = X25519PrivateKey.from_private_bytes(unb64(VECTOR['eph_private_key']))
    device_key = unb64(VECTOR['device_key'])
    eph = eph_private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    assert b64(eph) == VECTOR['sealed']['eph']
    shared = eph_private.exchange(X25519PublicKey.from_public_bytes(device_key))
    assert b64(shared) == VECTOR['shared_secret']
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=eph + device_key, info=INFO).derive(
        shared
    )
    assert b64(key) == VECTOR['key']
    box = ChaCha20Poly1305(key).encrypt(
        unb64(VECTOR['sealed']['nonce']), VECTOR['plaintext'].encode(), INFO
    )
    assert b64(box) == VECTOR['sealed']['box']


def test_a_box_that_was_changed_or_sealed_for_another_phone_does_not_open() -> None:
    private = unb64(VECTOR['device_private_key'])
    box = bytearray(unb64(VECTOR['sealed']['box']))
    box[0] ^= 1
    with pytest.raises(InvalidTag):
        open_box(private, {**VECTOR['sealed'], 'box': b64(bytes(box))})
    with pytest.raises(InvalidTag):
        open_box(bytes(range(32)), VECTOR['sealed'])


def test_the_sealing_is_written_down_exactly() -> None:
    text = flat(schema('SealedPush')['description'])
    for part in ('X25519', 'HKDF-SHA256', '`eph ‖ D`', '`marcel-push-v1`', '32 bytes out',
                 'ChaCha20-Poly1305', '12-byte `nonce`', '16-byte tag', 'base64url without padding',
                 'tests/fixtures/sealed-push/vector.json'):  # fmt: skip
        assert part in text, part


# 5. The relay's own API --------------------------------------------------------------------------


def test_the_relay_api_has_three_routes() -> None:
    ops = operations('push-relay.yaml')
    assert {name: (m, p) for name, (m, p, _) in ops.items()} == {
        'registerPhone': ('post', '/v1/register'),
        'sendPush': ('post', '/v1/push'),
        'deleteHandle': ('delete', '/v1/handles/{handle}'),
    }
    assert set(ops['registerPhone'][2]['responses']) == {'201', '400', '429'}
    assert ops['registerPhone'][2]['security'] == []
    assert set(ops['sendPush'][2]['responses']) == {'202', '400', '401', '410', '413', '429', '502'}
    assert ops['sendPush'][2]['security'] == [{'sendSecret': []}]
    assert set(ops['deleteHandle'][2]['responses']) == {'204', '401'}
    assert ops['deleteHandle'][2]['security'] == [{'sendSecret': []}]
    push = ops['sendPush'][2]['responses']
    assert '3 KB' in push['413']['description'] and '500' in push['429']['description']
    assert '`handle_gone`' in push['410']['description']


def test_the_relay_stores_only_what_it_needs() -> None:
    text = flat(load_yaml('push-relay.yaml')['info']['description'])
    assert (
        "the phone's APNs token, `sha256(send_secret)`, the `sandbox` flag and today's push count"
        in text
    )
    assert 'no request body, no notification text, no key' in text


def test_the_relay_bodies(registry: Registry) -> None:
    schemas = load_yaml('push-relay.yaml')['components']['schemas']
    registration = relay('Registration', registry)
    registration.validate(schemas['Registration']['example'])
    refused(registration, {'apns_token': 'zz', 'sandbox': False})
    refused(registration, {'apns_token': schemas['Registration']['example']['apns_token']})
    registered = relay('Registered', registry)
    registered.validate(schemas['Registered']['example'])
    refused(registered, {**schemas['Registered']['example'], 'handle': 'x' * 21})
    push = relay('Push', registry)
    good = schemas['Push']['example']
    push.validate(good)
    assert good['sealed'] == VECTOR['sealed']
    refused(push, {k: v for k, v in good.items() if k != 'urgent'})
    refused(push, {**good, 'sealed': VECTOR['content']})  # never the plain text
    # The app hands the relay's answer to its hub unchanged.
    assert (
        schemas['Registered']['properties']['handle']['pattern']
        == (schema('PushTarget')['properties']['handle']['pattern'])
    )
    assert (
        schemas['Registered']['properties']['send_secret']['pattern']
        == (schema('PushTarget')['properties']['send_secret']['pattern'])
    )


# 6. The computer's kind --------------------------------------------------------------------------


def test_health_says_mac_or_linux(registry: Registry) -> None:
    health = app('Health', registry)
    health.validate(example('Health'))
    assert schema('Health')['properties']['os']['enum'] == ['macos', 'linux']
    health.validate({k: v for k, v in example('Health').items() if k != 'os'})  # an older hub
    refused(health, {**example('Health'), 'os': 'windows'})


# 7. The tracker: a claude.ai artifact, set by the kickoff quest ----------------------------------


@pytest.mark.parametrize(
    'url',
    [
        'https://claude.ai/artifact/1V2swuomSbb9r1cB23Y2eh',
        'https://claude.ai/code/artifact/Tr4ck_er-01',
    ],
)
def test_a_tracker_is_a_claude_ai_artifact(url: str, registry: Registry) -> None:
    project = app('Project', registry)
    project.validate({**example('Project'), 'tracker_url': url})
    frame = check('channel.schema.json', '', registry)
    report = {
        'type': 'report',
        'call_id': 'c_1',
        'kind': 'artifact',
        'artifact': {'kind': 'link', 'title': 'Tracker', 'url': url, 'role': 'tracker'},
    }
    frame.validate(report)


@pytest.mark.parametrize(
    'url',
    [
        'https://github.com/shbunder/garden/issues',
        'https://claude.ai/code/session_0168oBNCs8Q2BrHDFu6tYUdh',
        'https://claude.ai.evil.example/artifact/x',
        'https://claude.ai/artifact/',
        'https://claude.ai/artifact/x?y=1',
        'http://claude.ai/artifact/x',
    ],
)
def test_any_other_tracker_url_is_refused(url: str, registry: Registry) -> None:
    refused(app('Project', registry), {**example('Project'), 'tracker_url': url})
    frame = check('channel.schema.json', '', registry)
    report = {
        'type': 'report',
        'call_id': 'c_1',
        'kind': 'artifact',
        'artifact': {'kind': 'link', 'title': 'Tracker', 'url': url, 'role': 'tracker'},
    }
    refused(frame, report)
    plain = {**report, 'artifact': {k: v for k, v in report['artifact'].items() if k != 'role'}}
    frame.validate(plain)  # still fine as an ordinary link


def test_only_the_kickoff_quest_sets_the_tracker(registry: Registry) -> None:
    kickoff = load_yaml('brain-api.yaml')['components']['schemas']['NewTask']['properties'][
        'kickoff'
    ]
    assert kickoff['type'] == 'boolean'
    new_task = check('brain-api.yaml', '/components/schemas/NewTask', registry)
    base = load_yaml('brain-api.yaml')['components']['schemas']['NewTask']['example']
    new_task.validate({**base, 'project': 'garden-planner', 'kickoff': True})
    refused(new_task, {**base, 'kickoff': 'yes'})
    refused(new_task, {**base, 'kickoff': True})  # a kickoff names its project
    new_task.validate({**base, 'kickoff': False})
    role = load_json('channel.schema.json')['$defs']['Artifact']['properties']['role'][
        'description'
    ]
    assert 'only from the project' in role and 'kickoff quest' in role and 'error ack' in role
    tracker = flat(schema('Project')['properties']['tracker_url']['description'])
    assert 'Set only by the project' in tracker and '`NewTask.kickoff`' in tracker
    protocol = flat((CONTRACTS / 'channel-protocol.md').read_text())
    assert 'only from the project' in protocol


# 8. Wording: the runner's Error.detail ----------------------------------------------------------


def test_the_runner_error_detail_fits_usage() -> None:
    detail = load_yaml('runner-api.yaml')['components']['schemas']['Error']['properties']['detail']
    text = flat(detail['description'])
    assert '`no_reading`' in text and 'plain words' in text and 'never leaves the runner' in text


# 9. No pin on a Mac (runner-api 0.1.14) ---------------------------------------------------------

CHANGED = {
    'seq': 1760000000001,
    'type': 'runner.claude_changed',
    'at': '2026-10-15T02:00:00Z',
    'message': 'Claude Code changed from 2.1.296 to 2.1.301.',
    'claude_change': {'from': '2.1.296', 'to': '2.1.301', 'expected': '2.1.296'},
}


def test_a_claude_change_may_come_without_a_pin(registry: Registry) -> None:
    event = check('runner-api.yaml', '/components/schemas/RunnerEvent', registry)
    event.validate(CHANGED)  # a pinned runner sends exactly what it sends today
    unpinned = {**CHANGED, 'claude_change': {'from': '2.1.296', 'to': '2.1.301'}}
    event.validate(unpinned)
    refused(event, {**CHANGED, 'claude_change': {'from': '2.1.296'}})
    schemas = load_yaml('runner-api.yaml')['components']['schemas']
    change = schemas['RunnerEvent']['properties']['claude_change']
    assert change['required'] == ['from', 'to']
    assert 'no pin' in change['description']
    expected = flat(schemas['Health']['properties']['claude_version_expected']['description'])
    assert 'Absent means no pin' in expected and 'never makes `ok` false' in expected


def test_every_claude_version_refusal_needs_a_pin() -> None:
    paths = load_yaml('runner-api.yaml')['paths']
    texts = [
        flat(op['responses']['422']['description'])
        for item in paths.values()
        for op in item.values()
        if isinstance(op, dict)
        and '422' in op.get('responses', {})
        and '`claude_version`' in op['responses']['422']['description']
    ]
    assert len(texts) == 4
    assert all('only when a pin is set' in text for text in texts)


def test_registration_is_rate_limited() -> None:
    """push-relay 0.1.1: a public relay caps registrations; the 429 says so and stores nothing."""
    reg = operations('push-relay.yaml')['registerPhone'][2]['responses']['429']
    assert (
        'too_many_registrations' in reg['description']
        and 'Nothing was stored' in reg['description']
    )
    assert 'Retry-After' in reg['headers']
    assert 'too_many_registrations' in load_yaml('push-relay.yaml')['info']['description']
