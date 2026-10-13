"""The delegation rules (S-21.1, ADR-009): eight requests, replayed by the fake `claude -p`
through the real tools server to the real hub, are answered, started on the NUC, started in the
cloud or scheduled, as the orchestrate skill's table says. No model and no network."""

from pathlib import Path

import pytest
from conftest import World
from fake_brain import NotFaked
from marcel_hub.models import Schedule, Task
from skills_eval import check_case, claude_command, load_cases
from sqlmodel import Session, select
from test_project_routing import Replay

CASES = [c['id'] for c in load_cases() if c['id'].startswith('delegate-')]
# What each request should become: nothing (an answer), a quest where, or a schedule.
EXPECTED = {
    'delegate-answer-fact': 'answer',
    'delegate-answer-thread': 'answer',
    'delegate-nuc-docker': 'nuc',
    'delegate-nuc-photos': 'nuc',
    'delegate-nuc-approve': 'nuc',
    'delegate-cloud-typo': 'cloud',
    'delegate-cloud-bump': 'cloud',
    'delegate-schedule': 'schedule',
}


def outcome(world: World) -> str:
    with Session(world.app.state.engine) as s:
        tasks = s.exec(select(Task)).all()
        schedules = s.exec(select(Schedule)).all()
    if schedules:
        assert not tasks
        return 'schedule'
    if not tasks:
        return 'answer'
    (task,) = tasks
    return task.location.value


def test_the_eight_cases_cover_every_row_of_the_table() -> None:
    assert sorted(CASES) == sorted(EXPECTED)
    assert set(EXPECTED.values()) == {'answer', 'nuc', 'cloud', 'schedule'}


@pytest.mark.parametrize('case_id', CASES)
def test_each_request_goes_where_the_table_says(case_id: str, world: World, tmp_path: Path) -> None:
    replay = Replay(world, tmp_path, case_id)
    result = replay.run()
    assert result['is_error'] is False, result['result']
    assert check_case(replay.case, replay.calls) == []
    assert outcome(world) == EXPECTED[case_id]
    assert replay.calls[-1]['tool'] == 'reply'


def test_the_brain_never_starts_a_subagent_or_a_workflow(world: World, tmp_path: Path) -> None:
    """The live eval runs the brain with Agent, Task and Workflow disallowed, and no case expects
    them."""
    replay = Replay(world, tmp_path, 'delegate-answer-fact')
    argv = claude_command(replay.case, replay.config)
    blocked = set(argv[argv.index('--disallowedTools') + 1].split())
    assert {'Agent', 'Task', 'Workflow'} <= blocked
    for case in load_cases():
        assert not {'Agent', 'Task', 'Workflow'} & set(case['answers'])


def test_the_fake_refuses_when_the_table_row_is_gone(world: World, tmp_path: Path) -> None:
    replay = Replay(world, tmp_path, 'delegate-schedule')
    argv = claude_command(replay.case, replay.config)
    at = argv.index('--append-system-prompt') + 1
    argv[at] = argv[at].replace('**A schedule**', '**A quest**')
    with pytest.raises(NotFaked, match='the skill no longer says'):
        replay.run(argv)
