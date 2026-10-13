"""Rules added in app-api 0.5.0, runner-api 0.1.6, session-api 0.1.1 and brain-api 0.1.0."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import BASE, load_json, load_yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry


def validator(doc: str, pointer: str, registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(
        {'$ref': f'{BASE}{doc}#{pointer}'}, registry=registry, format_checker=FormatChecker()
    )


def schema(name: str) -> dict[str, Any]:
    return load_yaml('app-api.yaml')['components']['schemas'][name]


@pytest.mark.parametrize(
    ('link', 'ok'),
    [
        ('marcel://artifact/art_01JA0B1', True),
        ('marcel://project/energy-contracts', True),
        ('marcel://project/inbox', True),
        ('marcel://project/Energy', False),
        ('marcel://project/-flag', False),
        ('marcel://project/' + 'a' * 41, False),
        ('marcel://artifact/', False),
        ('marcel://board/x', False),
    ],
)
def test_a_push_may_open_an_item_or_a_project(registry: Registry, link: str, ok: bool) -> None:
    push = {**schema('PushNotification')['example'], 'deep_link': link}
    check = validator('app-api.yaml', '/components/schemas/PushNotification', registry)
    if ok:
        check.validate(push)
    else:
        with pytest.raises(ValidationError):
            check.validate(push)


def test_the_approval_push_actions_are_named() -> None:
    text = schema('PushNotification')['description']
    assert 'identifiers `approve` and `deny`' in text
    assert schema('ApprovalAnswer')['properties']['decision']['enum'] == ['approve', 'deny']


def test_note_covers_the_hubs_own_notices() -> None:
    text = ' '.join(schema('PushNotification')['properties']['category']['description'].split())
    assert "and the hub's own, such as the usage guard" in text


@pytest.mark.parametrize(
    ('path', 'operation'),
    [('/approvals/{approval_id}', 'getApproval'), ('/messages/{message_id}', 'getMessage')],
)
def test_one_approval_and_one_message_can_be_read_by_id(path: str, operation: str) -> None:
    op = load_yaml('app-api.yaml')['paths'][path]['get']
    assert op['operationId'] == operation
    assert set(op['responses']) == {'200', '401', '404'}


def test_usage_says_when_there_is_no_reading() -> None:
    op = load_yaml('app-api.yaml')['paths']['/usage']['get']
    assert '`no_reading`' in op['responses']['503']['description']
    assert '`no_reading`' in schema('Error')['properties']['code']['description']


def test_an_item_can_be_pinned_to_a_project(registry: Registry) -> None:
    op = load_yaml('app-api.yaml')['paths']['/artifacts/{artifact_id}/move']['post']
    assert op['operationId'] == 'moveArtifact'
    assert '`artifact_is_board`' in op['responses']['409']['description']
    check = validator('app-api.yaml', '/components/schemas/ArtifactMove', registry)
    check.validate({'project_id': 'prj_01'})
    for bad in ({}, {'project_id': 'prj_01', 'title': 'x'}):
        with pytest.raises(ValidationError):
            check.validate(bad)


def test_a_moved_item_has_its_event(registry: Registry) -> None:
    check = validator('events.schema.json', '/$defs/Event', registry)
    frame = {
        'id': 7,
        'type': 'artifact.moved',
        'at': '2026-10-12T08:00:00Z',
        'data': {'artifact_id': 'art_1', 'from_project_id': 'prj_a', 'to_project_id': 'prj_b'},
    }
    check.validate(frame)
    with pytest.raises(ValidationError):
        check.validate({**frame, 'data': {'artifact_id': 'art_1', 'to_project_id': 'prj_b'}})


def test_a_new_message_can_carry_an_item(registry: Registry) -> None:
    check = validator('app-api.yaml', '/components/schemas/NewMessage', registry)
    check.validate({'text': 'What does this say about Engie?', 'artifact_id': 'art_01JA0B1'})
    channel = validator('channel.schema.json', '/$defs/UserMessage', registry)
    example = load_json('channel.schema.json')['$defs']['UserMessage']['examples'][0]
    channel.validate({**example, 'artifact_id': 'art_01JA0B1'})


def test_a_task_thread_mirrors_approval_cards() -> None:
    assert 'approval cards' in schema('Conversation')['properties']['task_id']['description']


PROGRESS_SCHEMAS = [
    ('app-api.yaml', '/components/schemas/ProgressEventData', {'text': 'Writing the test.'}),
    ('session-api.yaml', '/components/schemas/Report', {'kind': 'progress', 'text': 'Writing.'}),
    (
        'channel.schema.json',
        '/$defs/Report',
        {'type': 'report', 'call_id': 'c1', 'kind': 'progress', 'text': 'Writing.'},
    ),
]


@pytest.mark.parametrize(('doc', 'pointer', 'base'), PROGRESS_SCHEMAS)
def test_a_progress_report_may_list_the_next_steps(
    registry: Registry, doc: str, pointer: str, base: dict[str, Any]
) -> None:
    check = validator(doc, pointer, registry)
    check.validate(base)
    check.validate({**base, 'next': ['Run the tests', 'Open the PR']})
    for bad in (['Run the tests', ''], 'Open the PR', ['x'] * 21, [1]):
        with pytest.raises(ValidationError):
            check.validate({**base, 'next': bad})


# brain-api.yaml -------------------------------------------------------------------------------

BRAIN_ROUTES = {
    ('post', '/tasks'),
    ('get', '/tasks'),
    ('get', '/tasks/{task_id}'),
    ('post', '/tasks/{task_id}/messages'),
    ('post', '/tasks/{task_id}/stop'),
    ('post', '/tasks/{task_id}/move'),
    ('get', '/projects'),
    ('post', '/projects'),
    ('patch', '/projects/{project}'),
    ('post', '/projects/{project}/notes'),
    ('post', '/artifacts'),
    ('get', '/memory'),
    ('put', '/memory'),
    ('post', '/schedules'),
    ('get', '/schedules'),
    ('delete', '/schedules/{schedule_id}'),
    ('post', '/notify'),
    ('get', '/usage'),
    ('get', '/summary'),  # S-02.9: read by the plugin's SessionStart hook, not a tool
}
METHODS = {'get', 'post', 'put', 'patch', 'delete'}


def brain_operations() -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (m, p, op)
        for p, item in load_yaml('brain-api.yaml')['paths'].items()
        for m, op in item.items()
        if m in METHODS
    ]


def test_the_brain_api_lists_one_operation_per_tool() -> None:
    assert {(m, p) for m, p, _ in brain_operations()} == BRAIN_ROUTES


def test_every_brain_route_needs_the_secret_and_a_session() -> None:
    api = load_yaml('brain-api.yaml')
    assert api['security'] == [{'secret': [], 'session': []}]
    schemes = api['components']['securitySchemes']
    assert {s['name'] for s in schemes.values()} == {'X-Marcel-Secret', 'X-Marcel-Session'}
    assert all('security' not in op for _, _, op in brain_operations())


@pytest.mark.parametrize(('method', 'path', 'op'), brain_operations())
def test_every_brain_route_documents_who_is_refused(method: str, path: str, op: Any) -> None:
    assert {'401', '403', '503'} <= set(op['responses']), op['operationId']
    if 'requestBody' in op:
        assert {'400', '413'} <= set(op['responses']), op['operationId']


@pytest.mark.parametrize(
    'code',
    ['bad_secret', 'unknown_session', 'not_brain', 'no_secret', 'not_set_up', 'too_large',
     'memory_conflict', 'schedule_not_found', 'schedule_builtin', 'cf-connecting-ip'],
)  # fmt: skip
def test_the_brain_api_names_its_codes(code: str) -> None:
    assert f'`{code}`' in load_yaml('brain-api.yaml')['info']['description']


def test_a_brain_task_must_say_where_it_runs(registry: Registry) -> None:
    check = validator('brain-api.yaml', '/components/schemas/NewTask', registry)
    task = {'title': 't', 'prompt': 'p', 'location': 'nuc'}
    check.validate(task)
    for bad in ({**task, 'location': 'auto'}, {**task, 'repo': '-x/y'}, {**task, 'model': '-m'}):
        with pytest.raises(ValidationError):
            check.validate(bad)


def test_a_pr_card_may_say_why_it_is_stale(registry: Registry) -> None:
    check = validator('app-api.yaml', '/components/schemas/Artifact', registry)
    card = dict(schema('Artifact')['example'])
    for note in ('No GitHub token is set (MARCEL_GITHUB_TOKEN).', None):
        check.validate({**card, 'pr': {**card['pr'], 'note': note}})
    with pytest.raises(ValidationError):
        check.validate({**card, 'pr': {**card['pr'], 'note': 'x' * 201}})


def test_runner_errors_from_resume_and_fork_are_listed() -> None:
    paths = load_yaml('runner-api.yaml')['paths']
    resume = paths['/sessions/{session_id}/resume']['post']['responses']
    fork = paths['/sessions/{session_id}/fork']['post']['responses']
    assert 'code: cap_reached' in resume['409']['description']
    assert {'422', '503'} <= set(resume)
    assert '`not_forkable`' in fork['409']['description']
    assert {'400', '422', '503'} <= set(fork)


@pytest.mark.parametrize(
    ('field', 'value', 'ok'),
    [
        ('task_id', 'tsk_01J9ZQ5', True),
        ('task_id', '-rf', False),
        ('base', 'release/1.2', True),
        ('base', '-main', False),
        ('base', 'a..b', False),
        ('base', 'main.lock', False),
        ('base', 'main/', False),
        ('url', 'https://github.com/shbunder/marcel', True),
        ('url', 'https://user:pw@github.com/x/y', False),
        ('url', 'ext::sh -c x', False),
    ],
)
def test_a_repo_spec_is_checked_like_the_runner_does(
    registry: Registry, field: str, value: str, ok: bool
) -> None:
    check = validator('runner-api.yaml', '/components/schemas/RepoSpec', registry)
    spec = {**load_yaml('runner-api.yaml')['components']['schemas']['RepoSpec']['example']}
    spec[field] = value
    if ok:
        check.validate(spec)
    else:
        with pytest.raises(ValidationError):
            check.validate(spec)
