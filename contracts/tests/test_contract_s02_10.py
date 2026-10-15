"""S-02.10: a rollover's new brain may read before its hello (brain-api 0.1.4); what the session-end
hook does for a `silent` task and for a task that already ended (session-api 0.1.7, task-states.md);
a worker's role settings on spawn and resume (runner-api 0.1.11); the agent that wrote a message
(app-api 0.7.2). Answers contract requests S-06.8 (rollover reads), S-05.8 (board log), S-03.10 and
S-19.3."""

from __future__ import annotations

import re

from conftest import BASE, CONTRACTS, load_yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry

METHODS = {'get', 'post', 'put', 'patch', 'delete'}


def flat(text: str) -> str:
    return ' '.join(text.split())


def brain_header() -> str:
    return flat(load_yaml('brain-api.yaml')['info']['description'])


def read_before_hello() -> list[str]:
    """The routes named in the header's **Read before hello** paragraph, in order."""
    text = brain_header()
    match = re.search(r'\*\*Read before hello\*\* \(0\.1\.4\)\.(.*?)(?:\*\*|$)', text)
    assert match, 'the brain-api header has no "Read before hello" paragraph'
    return re.findall(r'`GET (/[^`]+)`', match.group(1))


def test_the_versions_were_bumped() -> None:
    assert load_yaml('brain-api.yaml')['info']['version'] == '0.1.5'  # 0.1.5 in S-02.13
    assert load_yaml('session-api.yaml')['info']['version'] == '0.1.7'
    assert load_yaml('runner-api.yaml')['info']['version'] == '0.1.13'  # 0.1.13 in S-02.12
    assert load_yaml('app-api.yaml')['info']['version'] == '0.8.0'  # 0.8.0 in S-02.13


# 1. Rollover reads -------------------------------------------------------------------------------


def test_the_session_rule_lets_a_rollovers_new_brain_read() -> None:
    text = brain_header()
    rule = text[text.index('`X-Marcel-Session`') : text.index('The checks run in this order')]
    assert (
        'for `GET` requests only, the new brain of a rollover that has not said hello yet' in rule
    )
    assert 'under the agent’s brain name' in rule or "under the agent's brain name" in rule
    assert 'not being retired' in rule
    assert 'any other method or route answers 401 `unknown_session`' in rule
    assert 'stays the old brain’s' in rule or "stays the old brain's" in rule


def test_the_check_order_puts_the_early_read_before_unknown_session() -> None:
    text = brain_header()
    order = text[text.index('The checks run in this order') : text.index('**Read before hello**')]
    steps = ['`no_secret`', '`bad_secret`', 'may only read', 'unknown session', '`not_brain`']
    places = [order.index(step) for step in steps]
    assert places == sorted(places), order


def test_the_read_before_hello_routes_are_the_hubs_list() -> None:
    assert read_before_hello() == [
        '/tasks',
        '/tasks/{task_id}',
        '/projects',
        '/memory',
        '/schedules',
        '/summary',
        '/usage',
    ]


def test_every_listed_route_is_a_get_in_the_contract() -> None:
    paths = load_yaml('brain-api.yaml')['paths']
    gets = {path for path, item in paths.items() if 'get' in item}
    # Every GET in brain-api is on the list today; a new one is added only once checked safe.
    assert set(read_before_hello()) == gets


def test_the_401_names_the_early_read() -> None:
    unauthorized = load_yaml('brain-api.yaml')['components']['responses']['Unauthorized']
    text = flat(unauthorized['description'])
    assert '`unknown_session`' in text and 'before its hello' in text


# 2. Session end: silent tasks and tasks that already ended ---------------------------------------


def session_end_text() -> str:
    op = load_yaml('session-api.yaml')['paths']['/channel/session-end']['post']
    return flat(op['description'])


def test_a_silent_task_fails_when_its_session_ends() -> None:
    text = session_end_text()
    assert 'a worker whose task is `silent`' in text
    assert '`report_owed` is false for it' in text
    assert 'starts `ended_without_report`' in text


def test_a_task_that_already_ended_only_logs_the_reason() -> None:
    text = session_end_text()
    assert 'already ended' in text
    assert 'only written to the hub’s log' in text or "only written to the hub's log" in text
    assert 'no task event' in text


def test_task_states_agrees() -> None:
    doc = (CONTRACTS / 'task-states.md').read_text()
    rows = [line for line in doc.splitlines() if 'ended_without_report' in line]
    assert len(rows) == 1
    cells = [c.strip() for c in rows[0].strip('|').split('|')]
    assert cells[0] == 'working, needs_you, silent' and cells[1] == 'failed'
    assert 'a `silent` task fails this way too' in cells[2]
    text = flat(doc)
    assert '**A session that ends after its task did.**' in text
    after = text[text.index('**A session that ends after its task did.**') :]
    assert 'the state does not change' in after and 'no task event' in after


# 3. The worker's role settings ------------------------------------------------------------------


def test_a_workers_spawn_passes_its_role_settings() -> None:
    text = flat(load_yaml('runner-api.yaml')['info']['description'])
    assert "A worker's spawn also passes `--settings`" in text
    assert '`role_settings/worker.json`' in text


def test_resume_refuses_saved_options_without_the_role_settings() -> None:
    header = flat(load_yaml('runner-api.yaml')['info']['description'])
    assert "or lack the role's `--settings` file" in header
    op = load_yaml('runner-api.yaml')['paths']['/sessions/{session_id}/resume']['post']
    text = flat(op['responses']['422']['description'])
    exited = text[text.index('`exited`') : text.index('`channel_skipped`')]
    assert "lack the role's `--settings` file" in exited
    assert '`role_settings/worker.json`' in exited and 'nothing was woken' in exited


# 4. The agent that wrote a message ---------------------------------------------------------------


def message(registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(
        {'$ref': f'{BASE}app-api.yaml#/components/schemas/Message'},
        registry=registry,
        format_checker=FormatChecker(),
    )


BASE_MESSAGE = {
    'id': 'msg_01',
    'conversation_id': 'conv_01',
    'role': 'agent',
    'kind': 'text',
    'text': 'Dinner is at six.',
    'created_at': '2026-10-14T18:00:00Z',
}


def test_a_message_may_name_the_agent_that_wrote_it(registry: Registry) -> None:
    schema = load_yaml('app-api.yaml')['components']['schemas']['Message']
    assert 'agent_id' not in schema['required']
    assert schema['properties']['agent_id']['type'] == 'string'
    text = flat(schema['properties']['agent_id']['description'])
    assert 'Absent on `user` and `system` messages' in text and 'New in 0.7.2' in text
    message(registry).validate({**BASE_MESSAGE, 'agent_id': 'agt_01'})
    message(registry).validate(BASE_MESSAGE)
    assert not message(registry).is_valid({**BASE_MESSAGE, 'agent_id': 7})


def test_approving_a_self_change_does_not_merge_it() -> None:
    schema = load_yaml('app-api.yaml')['components']['schemas']['Approval']
    text = flat(schema['properties']['artifact_id']['description'])
    assert 'approving will merge' not in text
    assert 'the owner merges the PR on GitHub' in text and 'the hub never merges' in text
    assert 'approved here and merged is redeployed' in text
