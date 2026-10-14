"""The models can represent the contract: enums match, and every contract field has a column."""

from enum import StrEnum
from pathlib import Path

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
        (models.UserRole, ('UserRole',)),
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
    'User': {'agent', 'device_count', 'quests_this_week'},
}


# Added to the contract by S-02.5 (projects and living artifacts) before the hub stores them.
# The owning story removes its entries when it adds the columns; the last test below fails as
# soon as an entry is stored, so this list can only shrink.
# S-04.11 stored the last of them. Contract 0.7 (S-02.7) added more; S-04.15 stored them all (the
# ones kept under another name are in DERIVED). Empty, until a contract adds a field again.
# Contract 0.7.2 (S-02.10, from S-19.3): the agent that wrote a message, until the hub stores it.
PENDING: dict[str, set[str]] = {'Message': {'agent_id'}}
PENDING_ENUM_VALUES: dict[str, set[str]] = {}
MODELS = {
    'Agent': models.Agent,
    'Project': models.Project,
    'Message': models.Message,
    'Task': models.Task,
    'Artifact': models.Artifact,
    'MessageKind': models.MessageKind,
    'ArtifactKind': models.ArtifactKind,
}


@pytest.mark.parametrize(
    ('schema', 'model'),
    [
        ('Agent', models.Agent), ('Device', models.Device), ('Conversation', models.Conversation),
        ('Message', models.Message), ('Task', models.Task), ('TaskEvent', models.TaskEvent),
        ('Approval', models.Approval), ('Artifact', models.Artifact),
        ('Schedule', models.Schedule), ('Project', models.Project), ('User', models.User),
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
