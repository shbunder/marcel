"""The models can represent the contract: enums match, and every contract field has a column."""

import copy
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest
import yaml

from marcel_hub import models

CONTRACT = Path(__file__).resolve().parents[2] / 'contracts' / 'app-api.yaml'

SCHEMAS = yaml.safe_load(CONTRACT.read_text())['components']['schemas']


def contract_enum(path: tuple[str, ...]) -> list[str]:
    node = SCHEMAS
    for part in path:
        node = node[part]
    return node['enum']


@pytest.mark.parametrize(
    ('model_enum', 'path'),
    [
        (models.TaskState, ('TaskState',)),
        (models.TaskLocation, ('TaskLocation',)),
        (models.QueuedReason, ('QueuedReason',)),
        (models.TaskEventType, ('TaskEventType',)),
        (models.ApprovalState, ('ApprovalState',)),
        (models.ArtifactKind, ('ArtifactKind',)),
        (models.ScheduleLocation, ('ScheduleLocation',)),
        (models.MessageRole, ('MessageRole',)),
        (models.MessageKind, ('MessageKind',)),
        (models.ConversationKind, ('Conversation', 'properties', 'kind')),
        (models.TaskOrigin, ('Task', 'properties', 'origin')),
        (models.AnsweredVia, ('Approval', 'properties', 'answered_via')),
    ],
)
def test_enum_matches_contract(model_enum: type[StrEnum], path: tuple[str, ...]) -> None:
    pending = PENDING_ENUM_VALUES.get(path[0], set())
    assert [m.value for m in model_enum] == [v for v in contract_enum(path) if v not in pending]


def test_task_states_match_the_state_machine_doc() -> None:
    doc = (CONTRACT.parent / 'task-states.md').read_text()
    for state in models.TaskState:
        assert f'| `{state.value}` |' in doc


# Contract fields that are derived or joined at read time, not stored on the row.
DERIVED = {
    'Agent': {'state', 'look'},  # look: look_animal, look_color, look_personality (S-04.15)
    'Device': {'push', 'current', 'last_seen_at'},
    'Conversation': {'reply_count', 'last_message_at'},
    # mentions: stored as mentions_json (S-04.15)
    'Message': {'text', 'milestone', 'approval_id', 'quoted_message_id', 'thread', 'mentions'},
    'Task': {'approval_ids', 'artifact_count'},
    'TaskEvent': {'data'},
    'Artifact': {'pr'},
    'Project': {'counts'},
}


# Added to the contract by S-02.5 (projects and living artifacts) before the hub stores them.
# The owning story removes its entries when it adds the columns; the last test below fails as
# soon as an entry is stored, so this list can only shrink.
# S-04.11 stored the last of them. Contract 0.7 (S-02.7) added more; S-04.15 stored them all (the
# ones kept under another name are in DERIVED).
# Contract 0.7.2 (S-02.10, asked for by S-19.3) added `Message.agent_id`, stored since (0012).
# Contract 0.8.0 (S-02.13) added the fields below; each names the story that stores it.
PENDING: dict[str, set[str]] = {
    'Device': {'agent_id'},  # S-22.11 (the device.agent_id column and its migration)
}
# Fields app-api 0.8.0 (S-02.13, ADR-013) removed that the hub still sends, with the story that
# stops sending them. `served_contract` lets the response checks accept them until then;
# test_auth.py checks each one is still sent, so this list can only shrink.
RETIRED_FIELDS: dict[str, dict[str, str]] = {'Me': {'role': 'S-22.11'}}


def served_contract(api: dict[str, Any]) -> dict[str, Any]:
    """app-api.yaml as the hub serves it today: `PENDING` fields are not required yet, and
    `RETIRED_FIELDS` are still allowed. Each list only shrinks, so the checks only tighten."""
    api = copy.deepcopy(api)
    schemas = api['components']['schemas']
    for schema, fields in PENDING.items():
        node = schemas[schema]
        node['required'] = [f for f in node.get('required', []) if f not in fields]
    for schema, retired in RETIRED_FIELDS.items():
        for field in retired:
            assert field not in schemas[schema]['properties'], f'{schema}.{field} is back'
            schemas[schema]['properties'][field] = {}
    return api


# Contract 0.7.3's workflow event types are stored since S-04.16. Empty until a contract adds
# a value.
PENDING_ENUM_VALUES: dict[str, set[str]] = {}
MODELS = {
    'Agent': models.Agent,
    'Project': models.Project,
    'Message': models.Message,
    'Task': models.Task,
    'Artifact': models.Artifact,
    'MessageKind': models.MessageKind,
    'ArtifactKind': models.ArtifactKind,
    'TaskEventType': models.TaskEventType,
    'Device': models.Device,
}


@pytest.mark.parametrize(
    ('schema', 'model'),
    [
        ('Agent', models.Agent), ('Device', models.Device), ('Conversation', models.Conversation),
        ('Message', models.Message), ('Task', models.Task), ('TaskEvent', models.TaskEvent),
        ('Approval', models.Approval), ('Artifact', models.Artifact),
        ('Schedule', models.Schedule), ('Project', models.Project),
    ],
)  # fmt: skip
def test_every_contract_field_is_stored_or_derived(schema: str, model: type) -> None:
    columns = set(model.model_fields)
    missing = set(SCHEMAS[schema]['properties']) - columns - DERIVED.get(schema, set())
    assert not missing - PENDING.get(schema, set())


def test_pending_entries_are_still_pending() -> None:
    for schema, fields in PENDING.items():
        assert fields <= set(SCHEMAS[schema]['properties']), f'{schema}: not in the contract'
        stored = fields & set(MODELS[schema].model_fields)
        assert not stored, f'{schema} now stores {stored}: remove it from PENDING'
    for enum, values in PENDING_ENUM_VALUES.items():
        assert values <= set(SCHEMAS[enum]['enum'])
        stored = values & {m.value for m in MODELS[enum]}
        assert not stored, f'{enum} now has {stored}: remove it from PENDING_ENUM_VALUES'
