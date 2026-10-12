"""Rules added in app-api 0.4.0, runner-api 0.1.2 and the channel's call_ack error."""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import BASE, CONTRACTS, load_yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry


def validator(doc: str, pointer: str, registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(
        {'$ref': f'{BASE}{doc}#{pointer}'}, registry=registry, format_checker=FormatChecker()
    )


@pytest.mark.parametrize('schema', ['SpawnRequest', 'ForkRequest'])
@pytest.mark.parametrize('field', ['name', 'model'])
def test_a_runner_name_or_model_never_starts_with_a_dash(
    registry: Registry, schema: str, field: str
) -> None:
    example: dict[str, Any] = dict(
        load_yaml('runner-api.yaml')['components']['schemas'][schema]['example']
    )
    check = validator('runner-api.yaml', f'/components/schemas/{schema}', registry)
    example[field] = 'opus[1m]' if field == 'model' else 'fix-login.test'
    check.validate(example)
    example[field] = '--dangerously-skip-permissions'
    with pytest.raises(ValidationError):
        check.validate(example)


@pytest.mark.parametrize(
    ('link', 'ok'),
    [
        ('marcel://status', True),
        ('marcel://task/tsk_01', True),
        ('marcel://status/x', False),
        ('marcel://settings', False),
    ],
)
def test_a_push_may_open_the_status_screen(registry: Registry, link: str, ok: bool) -> None:
    api = load_yaml('app-api.yaml')
    push = {**api['components']['schemas']['PushNotification']['example'], 'deep_link': link}
    check = validator('app-api.yaml', '/components/schemas/PushNotification', registry)
    if ok:
        check.validate(push)
    else:
        with pytest.raises(ValidationError):
            check.validate(push)


def test_a_call_ack_can_carry_a_refusal(registry: Registry) -> None:
    check = validator('channel.schema.json', '/$defs/CallAck', registry)
    check.validate(
        {
            'type': 'call_ack',
            'call_id': 'c1',
            'error': {'code': 'unknown_artifact', 'message': 'No such item.'},
        }
    )
    with pytest.raises(ValidationError):
        check.validate({'type': 'call_ack', 'call_id': 'c1', 'error': {'code': 'x'}})


def test_the_versions_were_bumped() -> None:
    assert load_yaml('app-api.yaml')['info']['version'] == '0.5.0'
    assert load_yaml('runner-api.yaml')['info']['version'] == '0.1.6'
    assert load_yaml('session-api.yaml')['info']['version'] == '0.1.1'
    assert load_yaml('brain-api.yaml')['info']['version'] == '0.1.0'
    assert json.loads((CONTRACTS / 'channel.schema.json').read_text())


def test_a_brain_notice_has_its_own_category(registry: Registry) -> None:
    api = load_yaml('app-api.yaml')
    push = {**api['components']['schemas']['PushNotification']['example'], 'category': 'note'}
    push.pop('approval_id', None)
    push['deep_link'] = 'marcel://message/msg_01'
    validator('app-api.yaml', '/components/schemas/PushNotification', registry).validate(push)


def test_brain_tools_are_allowed_by_name_not_by_wildcard() -> None:
    text = (CONTRACTS / 'runner-api.yaml').read_text()
    assert 'mcp__plugin_marcel_tools__tasks_spawn' in text
    assert 'mcp__plugin_marcel_tools__*' not in text


def test_a_recovered_event_needs_its_message(registry: Registry) -> None:
    check = validator('runner-api.yaml', '/components/schemas/RunnerEvent', registry)
    event = {'seq': 1760000000000, 'type': 'runner.recovered', 'at': '2026-10-13T17:00:00Z'}
    with pytest.raises(ValidationError):
        check.validate(event)
    check.validate({**event, 'message': 'The NUC can list its sessions again.'})
