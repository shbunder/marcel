"""S-02.11: a workflow's agents in the transcript stream (runner-api 0.1.12, transcript.schema.json,
app-api 0.7.3; asked for by SP10), the brain's animal (runner-api 0.1.12, asked for by S-17.7),
and the runner saying when Claude Code changes or its supervisor restarts (runner-api 0.1.12,
asked for by claude-auto-update). The shapes come from the recording in
runner/tests/fake_claude/fixtures/transcripts/workflow-layout/ (Claude Code 2.1.296)."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import BASE, load_json, load_yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry

RUN = 'wf_f6861b61-537'
AGENT = 'a05c6304d69317a22'


def flat(text: str) -> str:
    return ' '.join(text.split())


def check(registry: Registry, ref: str) -> Draft202012Validator:
    return Draft202012Validator(
        {'$ref': f'{BASE}{ref}'}, registry=registry, format_checker=FormatChecker()
    )


def test_the_versions_were_bumped() -> None:
    assert load_yaml('runner-api.yaml')['info']['version'] == '0.1.14'  # 0.1.14 in S-02.13
    assert load_yaml('app-api.yaml')['info']['version'] == '0.8.2'  # 0.8.2 in S-02.16


# 1. Workflow transcripts -------------------------------------------------------------------------

WORKFLOW_TYPES = ['workflow_start', 'workflow_stop', 'workflow_agent_start', 'workflow_agent_stop']
GOOD_DATA: dict[str, dict[str, Any]] = {
    'workflow_start': {'tool_use_id': 'toolu_01', 'run_id': RUN, 'name': 'summarize-sp10-notes'},
    'workflow_stop': {'tool_use_id': 'toolu_01', 'status': 'completed'},
    'workflow_agent_start': {'run_id': RUN, 'agent_id': AGENT, 'label': 'summarize:note8.txt'},
    'workflow_agent_stop': {'run_id': RUN, 'agent_id': AGENT},
}
# One required field taken away, a field of the wrong shape, or a field the type does not have.
BAD_DATA: list[tuple[str, dict[str, Any]]] = [
    ('workflow_start', {'tool_use_id': 'toolu_01', 'name': 'x'}),
    ('workflow_start', {'run_id': RUN, 'name': 'x'}),
    ('workflow_start', {'tool_use_id': 'toolu_01', 'run_id': RUN}),
    ('workflow_start', {'tool_use_id': 'toolu_01', 'run_id': '../etc', 'name': 'x'}),
    ('workflow_start', {**GOOD_DATA['workflow_start'], 'script': 'export const meta = {}'}),
    ('workflow_stop', {'tool_use_id': 'toolu_01'}),
    ('workflow_stop', {'status': 'completed'}),
    ('workflow_stop', {'tool_use_id': 'toolu_01', 'status': 'done'}),
    ('workflow_agent_start', {'run_id': RUN, 'agent_id': AGENT}),
    ('workflow_agent_start', {'run_id': RUN, 'label': 'x'}),
    ('workflow_agent_start', {'agent_id': AGENT, 'label': 'x'}),
    ('workflow_agent_start', {'run_id': RUN, 'agent_id': '../a', 'label': 'x'}),
    ('workflow_agent_stop', {'run_id': RUN}),
    ('workflow_agent_stop', {'agent_id': AGENT}),
    ('workflow_agent_stop', {'run_id': RUN, 'agent_id': AGENT, 'status': 'completed'}),
]


def event(kind: str, data: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        'session_id': '632be746-d16f-4a4c-90ac-871472bdd573',
        'offset': 120,
        'at': '2026-10-14T00:19:01Z',
        'type': kind,
        'data': data,
        **extra,
    }


def test_the_four_workflow_types_are_transcript_events() -> None:
    kinds = load_json('transcript.schema.json')['$defs']['TranscriptEvent']['properties']['type']
    assert set(WORKFLOW_TYPES) <= set(kinds['enum'])


@pytest.mark.parametrize('kind', WORKFLOW_TYPES)
def test_a_good_workflow_event_is_accepted(kind: str, registry: Registry) -> None:
    check(registry, 'transcript.schema.json').validate(event(kind, GOOD_DATA[kind]))


@pytest.mark.parametrize(
    ('kind', 'data'), BAD_DATA, ids=[f'{k}-{i}' for i, (k, _) in enumerate(BAD_DATA)]
)
def test_a_bad_workflow_event_is_refused(
    kind: str, data: dict[str, Any], registry: Registry
) -> None:
    assert not check(registry, 'transcript.schema.json').is_valid(event(kind, data))


def test_the_runs_events_carry_its_id(registry: Registry) -> None:
    validator = check(registry, 'transcript.schema.json')
    text = event('text', {'role': 'assistant', 'text': 'Note 8 is about topic 8.'})
    validator.validate({**text, 'workflow_id': RUN, 'subagent_id': AGENT})
    validator.validate(
        {**event('workflow_agent_start', GOOD_DATA['workflow_agent_start']), 'workflow_id': RUN}
    )
    assert not validator.is_valid({**text, 'workflow_id': 'wf_../../x'})
    assert not validator.is_valid({**text, 'workflow_id': 'run_1'})


def test_the_app_shows_them_as_task_events(registry: Registry) -> None:
    schemas = load_yaml('app-api.yaml')['components']['schemas']
    assert set(WORKFLOW_TYPES) <= set(schemas['TaskEventType']['enum'])
    task_event = check(registry, 'app-api.yaml#/components/schemas/TaskEvent')
    for kind in WORKFLOW_TYPES:
        good = {'task_id': 'tsk_1', 'seq': 1, 'type': kind, 'at': '2026-10-14T00:19:01Z'}
        task_event.validate({**good, 'data': GOOD_DATA[kind]})
    for kind, data in BAD_DATA:
        bad = {
            'task_id': 'tsk_1',
            'seq': 1,
            'type': kind,
            'at': '2026-10-14T00:19:01Z',
            'data': data,
        }
        assert not task_event.is_valid(bad), (kind, data)


def tail() -> dict[str, Any]:
    return load_yaml('runner-api.yaml')['paths']['/sessions/{session_id}/tail']['get']


def test_the_tail_takes_a_run_id() -> None:
    params = {p['name']: p for p in tail()['parameters'] if 'name' in p}
    assert params['workflow']['in'] == 'query'
    assert params['workflow']['schema']['pattern'] == '^wf_[A-Za-z0-9_-]{1,64}$'
    assert params['subagent']['schema']['pattern'] == '^[0-9a-f]+$'
    # The run id pattern is the same everywhere it appears.
    defs = load_json('transcript.schema.json')['$defs']
    patterns = {
        defs['TranscriptEvent']['properties']['workflow_id']['pattern'],
        *(defs[name]['properties']['run_id']['pattern'] for name in
          ('WorkflowStart', 'WorkflowStop', 'WorkflowAgentStart', 'WorkflowAgentStop')),
    }  # fmt: skip
    assert patterns == {params['workflow']['schema']['pattern']}


def test_the_tail_says_where_a_runs_files_are_and_stays_in_the_session() -> None:
    text = flat(tail()['description'])
    assert '`<session-id>/subagents/workflows/<run_id>/journal.jsonl`' in text
    assert '`workflow=<run_id>&subagent=<agent_id>`' in text and '`agent-<agent_id>.jsonl`' in text
    assert 'never give a `subagent_start`' in text
    assert 'must stay inside the session' in text and '400 `bad_request`' in text
    assert 'same `workflow` or none, same `subagent` or none' in text
    refused = flat(tail()['responses']['400']['description'])
    assert '`workflow` is not a run id' in refused and "outside the session's folder" in refused


# 2. The brain's animal ---------------------------------------------------------------------------


def prepare(registry: Registry) -> Draft202012Validator:
    return check(registry, 'runner-api.yaml#/components/schemas/PrepareBrain')


def test_the_brain_may_be_told_its_animal(registry: Registry) -> None:
    schemas = load_yaml('runner-api.yaml')['components']['schemas']
    persona = schemas['PrepareBrain']['properties']['persona']
    assert 'animal' not in persona['required']
    animals = load_yaml('app-api.yaml')['components']['schemas']['LookAnimal']['enum']
    assert persona['properties']['animal']['enum'] == animals
    body = {'agent_slug': 'pip', 'name': 'Pip', 'persona': {'person_name': 'Emma'}}
    prepare(registry).validate(body)
    prepare(registry).validate({**body, 'persona': {'person_name': 'Emma', 'animal': 'rabbit'}})
    assert not prepare(registry).is_valid(
        {**body, 'persona': {'person_name': 'Emma', 'animal': 'unicorn'}}
    )
    assert 'a pink giraffe' in flat(persona['properties']['animal']['description'])


# 3. Claude Code changed, the supervisor restarted ------------------------------------------------


def runner_event(registry: Registry) -> Draft202012Validator:
    return check(registry, 'runner-api.yaml#/components/schemas/RunnerEvent')


CHANGED = {
    'seq': 1760000000001,
    'type': 'runner.claude_changed',
    'at': '2026-10-14T02:00:00Z',
    'message': 'Claude Code changed from 2.1.296 to 2.1.301; Marcel is tested with 2.1.296.',
    'claude_change': {'from': '2.1.296', 'to': '2.1.301', 'expected': '2.1.296'},
}
RESTARTED = {
    'seq': 1760000000002,
    'type': 'runner.supervisor_restarted',
    'at': '2026-10-14T02:00:02Z',
    'message': "Claude's supervisor restarted (upgrade); 2 running sessions ended.",
    'supervisor_restart': {
        'cause': 'upgrade',
        'live_workers': 5,
        'session_ids': [
            'b18d7c1c-0f0e-4c3a-8a35-3b8d2f1e9a10',
            '632be746-d16f-4a4c-90ac-871472bdd573',
        ],
    },
}


def test_the_two_new_runner_events_are_accepted(registry: Registry) -> None:
    runner_event(registry).validate(CHANGED)
    runner_event(registry).validate(RESTARTED)
    unread = {**RESTARTED, 'supervisor_restart': {'session_ids': []}}
    runner_event(registry).validate(unread)  # the daemon log could not be read


@pytest.mark.parametrize(
    'bad',
    [
        {k: v for k, v in CHANGED.items() if k != 'claude_change'},
        {k: v for k, v in CHANGED.items() if k != 'message'},
        # Without `expected` it is valid since runner-api 0.1.14 (no pin on a Mac, S-02.13).
        {**CHANGED, 'claude_change': {'from': '2.1.296', 'expected': '2.1.296'}},
        {k: v for k, v in RESTARTED.items() if k != 'supervisor_restart'},
        {k: v for k, v in RESTARTED.items() if k != 'message'},
        {**RESTARTED, 'supervisor_restart': {'cause': 'upgrade'}},
        {**RESTARTED, 'supervisor_restart': {'session_ids': [], 'live_workers': -1}},
        {**RESTARTED, 'supervisor_restart': {'session_ids': [], 'pid': 4242}},
    ],
)
def test_a_bad_runner_event_is_refused(bad: dict[str, Any], registry: Registry) -> None:
    assert not runner_event(registry).is_valid(bad)


def test_the_restart_names_the_daemon_log() -> None:
    schema = load_yaml('runner-api.yaml')['components']['schemas']['RunnerEvent']
    cause = flat(schema['properties']['supervisor_restart']['properties']['cause']['description'])
    assert '`~/.claude/daemon.log`' in cause and 'Absent when that line could not be read' in cause
    kinds = flat(schema['properties']['type']['description'])
    assert 'sent once per change' in kinds and 'sent once per restart' in kinds
