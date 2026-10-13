"""An `@` quest mention routes the message to that quest (S-18.2, B-42): the recorded brain turn,
replayed by the fake `claude -p` through the real tools server to the real hub, sends it to the
mentioned quest with `tasks_send` and starts nothing new."""

from pathlib import Path

from conftest import World
from marcel_hub.models import Task, TaskEvent, TaskLocation, TaskOrigin, TaskState
from skills_eval import check_case
from sqlmodel import Session, select
from test_project_routing import Replay

CASE = 'mention-quest'


def login_quest(world: World) -> None:
    """The quest the eval's message mentions, made for real with the eval's id."""
    with Session(world.app.state.engine) as s:
        s.add(
            Task(
                id='tsk_login',
                agent_id=world.agent_id,
                title='Fix flaky login test',
                location=TaskLocation.cloud,
                repo='shbunder/marcel',
                state=TaskState.working,
                model='sonnet',
                origin=TaskOrigin.marcel,
            )
        )
        s.commit()


def test_an_at_quest_mention_is_sent_to_that_quest(world: World, tmp_path: Path) -> None:
    login_quest(world)
    replay = Replay(world, tmp_path, CASE)
    result = replay.run()
    assert result['is_error'] is False, result['result']
    assert check_case(replay.case, replay.calls) == []
    assert [c['tool'] for c in replay.calls] == ['tasks_send', 'reply']
    with Session(world.app.state.engine) as s:
        tasks = s.exec(select(Task)).all()
        notes = s.exec(select(TaskEvent).where(TaskEvent.task_id == 'tsk_login')).all()
    assert [t.id for t in tasks] == ['tsk_login']  # nothing new was started
    assert any('Safari' in str(n.data_json.get('text', '')) for n in notes)


def test_the_mention_case_carries_the_mention_the_way_the_channel_does() -> None:
    from test_project_routing import case_named

    request = case_named(CASE)['request']
    assert 'kind="user_message"' in request
    assert '"id":"tsk_login"' in request  # the channel's meta.mentions, a JSON string
