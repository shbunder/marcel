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
    ],
)
def test_enum_matches_contract(model_enum: type[StrEnum], path: tuple[str, ...]) -> None:
    assert [m.value for m in model_enum] == contract_enum(path)


def test_task_states_match_the_state_machine_doc() -> None:
    doc = (CONTRACT.parent / 'task-states.md').read_text()
    for state in models.TaskState:
        assert f'| `{state.value}` |' in doc


# Contract fields that are derived or joined at read time, not stored on the row.
DERIVED = {
    'Agent': {'state'},
    'Device': {'push', 'current', 'last_seen_at'},
    'Conversation': {'reply_count', 'last_message_at'},
    'Message': {'text', 'milestone', 'approval_id', 'quoted_message_id', 'thread'},
    'Task': {'approval_ids', 'artifact_count'},
    'TaskEvent': {'data'},
    'Artifact': {'pr'},
}


@pytest.mark.parametrize(
    ('schema', 'model'),
    [
        ('Agent', models.Agent), ('Device', models.Device), ('Conversation', models.Conversation),
        ('Message', models.Message), ('Task', models.Task), ('TaskEvent', models.TaskEvent),
        ('Approval', models.Approval), ('Artifact', models.Artifact),
        ('Schedule', models.Schedule),
    ],
)  # fmt: skip
def test_every_contract_field_is_stored_or_derived(schema: str, model: type) -> None:
    columns = set(model.model_fields)
    missing = set(SCHEMAS[schema]['properties']) - columns - DERIVED.get(schema, set())
    assert not missing
