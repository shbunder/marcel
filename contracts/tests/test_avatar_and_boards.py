"""S-02.5: the avatar sheet, project boards and the project/version shapes of the app API."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from conftest import CONTRACTS
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from test_app_api import validator_for


def sheet_validator() -> Draft202012Validator:
    schema = json.loads((CONTRACTS / 'avatar-sheet.schema.json').read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def sheet_example() -> dict[str, Any]:
    return copy.deepcopy(sheet_validator().schema['examples'][0])


def test_avatar_sheet_example_is_valid():
    sheet_validator().validate(sheet_example())


@pytest.mark.parametrize('state', ['idle', 'thinking', 'working3', 'needs_you', 'done'])
def test_avatar_sheet_needs_every_state(state: str):
    sheet = sheet_example()
    del sheet['loops'][state]
    with pytest.raises(ValidationError, match=state):
        sheet_validator().validate(sheet)


@pytest.mark.parametrize('step', [[0], [0, 5], [0, 100, 1], [-1, 100]])
def test_avatar_loop_steps_are_frame_and_milliseconds(step: list[int]):
    sheet = sheet_example()
    sheet['loops']['idle'] = [step]
    with pytest.raises(ValidationError):
        sheet_validator().validate(sheet)


def test_avatar_example_frames_exist():
    sheet = sheet_example()
    used = {frame for loop in sheet['loops'].values() for frame, _ in loop}
    assert max(used) < sheet['frame_count']


def dashboard_validator() -> Draft202012Validator:
    schema = json.loads((CONTRACTS / 'dashboard.schema.json').read_text())
    return Draft202012Validator(schema, format_checker=FormatChecker())


def board_example() -> dict[str, Any]:
    examples = dashboard_validator().schema['examples']
    return copy.deepcopy(next(e for e in examples if e['widgets'][0]['type'] == 'section'))


def test_a_project_board_is_a_valid_dashboard():
    dashboard_validator().validate(board_example())


def test_sections_do_not_nest():
    board = board_example()
    board['widgets'][0]['widgets'].append({'type': 'section', 'title': 'x', 'widgets': []})
    with pytest.raises(ValidationError):
        dashboard_validator().validate(board)


def test_task_without_project_is_refused(registry, api):
    task = {k: v for k, v in api['components']['schemas']['Task']['example'].items()}
    del task['project_id']
    with pytest.raises(ValidationError, match='project_id'):
        validator_for('/components/schemas/Task', registry).validate(task)


def test_artifact_message_names_its_artifact(registry):
    message = {
        'id': 'msg_01JA0C1',
        'conversation_id': 'cnv_main_marcel',
        'role': 'agent',
        'kind': 'artifact',
        'artifact_id': 'art_01JA0B1',
        'project_id': 'prj_01JA0A1',
        'task_id': 'tsk_01JA0A9',
        'created_at': '2026-10-09T20:00:00Z',
    }
    validator = validator_for('/components/schemas/Message', registry)
    validator.validate(message)
    with pytest.raises(ValidationError, match='artifact_id'):
        validator.validate({k: v for k, v in message.items() if k != 'artifact_id'})


def test_board_artifact_needs_its_content_type(registry, api):
    board = api['components']['schemas']['ArtifactPage']['example']['items'][1]
    assert board['kind'] == 'board'
    validator = validator_for('/components/schemas/Artifact', registry)
    validator.validate(board)
    with pytest.raises(ValidationError, match='content_type'):
        validator.validate({k: v for k, v in board.items() if k != 'content_type'})


@pytest.mark.parametrize('slug', ['Energy', '-energy', 'a' * 41, 'en ergy'])
def test_project_slug_is_safe_for_a_file_name(registry, api, slug: str):
    project = {**api['components']['schemas']['Project']['example'], 'slug': slug}
    with pytest.raises(ValidationError):
        validator_for('/components/schemas/Project', registry).validate(project)
