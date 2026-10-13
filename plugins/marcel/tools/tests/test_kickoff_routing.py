"""A new project with a repo gets one Kickoff quest (S-20.1, B-45): the recorded brain turn,
replayed by the fake `claude -p` through the real tools server to the real hub, makes the project
and starts the Kickoff in it."""

from pathlib import Path

from conftest import World
from marcel_hub.models import Project, Task
from skills_eval import check_case
from sqlmodel import Session, select
from test_project_routing import Replay


def test_a_new_project_with_a_repo_gets_a_kickoff_quest(world: World, tmp_path: Path) -> None:
    replay = Replay(world, tmp_path, 'kickoff-new-repo')
    result = replay.run()
    assert result['is_error'] is False, result['result']
    assert check_case(replay.case, replay.calls) == []
    with Session(world.app.state.engine) as s:
        project = s.exec(select(Project).where(Project.slug == 'garden-planner')).one()
        tasks = s.exec(select(Task)).all()
    assert project.repo == 'shbunder/garden-planner'
    assert [(t.title, t.project_id, t.repo) for t in tasks] == [
        ('Kickoff garden-planner', project.id, 'shbunder/garden-planner')
    ]
