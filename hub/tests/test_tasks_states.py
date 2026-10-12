"""Every allowed and every forbidden transition, against the table in contracts/task-states.md.

The expected answer for each pair comes from the contract document itself, and each pair is run
through `TaskRegistry` on a real database. Remove the guard and every forbidden pair goes red.
"""

import logging
import re

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, select
from test_tasks_fakes import (  # noqa: F401  (fixtures)
    AGENT,
    CONTRACTS,
    outbox_fixture,
    publisher_fixture,
    put_task,
    registry_fixture,
    run,
    runner_fixture,
)

from marcel_hub.models import Task, TaskEvent, TaskEventType, TaskLocation, TaskOrigin, TaskState
from marcel_hub.tasks.registry import TaskRegistry
from marcel_hub.tasks.states import FINISHED, TRANSITIONS, IllegalTransition


def contract_transitions() -> set[tuple[TaskState | None, TaskState]]:
    doc = (CONTRACTS / 'task-states.md').read_text()
    section = doc.split('## Transitions', 1)[1].split('\n## ', 1)[0]
    pairs: set[tuple[TaskState | None, TaskState]] = set()
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) < 4 or cells[0] in ('from', '') or set(cells[0]) <= {'-', ' '}:
            continue
        sources = [None if s.strip() == '—' else TaskState(s.strip()) for s in cells[0].split(',')]
        targets = [TaskState(t.strip()) for t in cells[1].split(',')]
        pairs |= {(src, dst) for src in sources for dst in targets}
    return pairs


CONTRACT = contract_transitions()
SOURCES: list[TaskState | None] = [None, *TaskState]
PAIRS = [(src, dst) for src in SOURCES for dst in TaskState]


def test_the_contract_table_parses() -> None:
    # 18 rows, 36 distinct pairs (unknown → stopped is in two rows). A parser that finds
    # nothing would make every case "forbidden" and still pass.
    assert len(CONTRACT) == 36
    assert (TaskState.done, TaskState.working) in CONTRACT
    assert re.search(r'\| unknown \| starting, working', (CONTRACTS / 'task-states.md').read_text())


def test_the_code_table_is_the_contract_table() -> None:
    assert set(TRANSITIONS) == CONTRACT


def _label(pair: tuple[TaskState | None, TaskState]) -> str:
    src, dst = pair
    return f'{src.value if src else "new"}->{dst.value}'


@pytest.mark.parametrize('pair', PAIRS, ids=[_label(p) for p in PAIRS])
def test_transition(
    pair: tuple[TaskState | None, TaskState],
    registry: TaskRegistry,
    engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    src, dst = pair
    allowed = pair in CONTRACT
    if src is None:
        task = Task(agent_id=AGENT, title='New', location=TaskLocation.nuc, state=dst,
                    model='sonnet', origin=TaskOrigin.marcel)  # fmt: skip
        if allowed:
            assert registry.create(task, 'Do it', dst, 'Spawn').state == dst
        else:
            with caplog.at_level(logging.WARNING), pytest.raises(IllegalTransition):
                registry.create(task, 'Do it', dst, 'Spawn')
            with Session(engine) as s:
                assert s.get(Task, task.id) is None
            assert 'Refused a task transition' in caplog.text
        return

    task_id = put_task(engine, src)
    if allowed:
        moved = run(registry.transition(task_id, dst, 'test'))
        assert moved.state == dst
        events = run_events(engine, task_id)
        assert events[-1].type == TaskEventType.state
        assert events[-1].data_json == {'from': src.value, 'to': dst.value, 'reason': 'test'}
    else:
        with caplog.at_level(logging.WARNING), pytest.raises(IllegalTransition) as err:
            run(registry.transition(task_id, dst, 'test'))
        assert registry.get(task_id).state == src
        assert run_events(engine, task_id) == []
        assert src.value in str(err.value) and dst.value in str(err.value)
        assert 'Refused a task transition' in caplog.text


def run_events(engine: Engine, task_id: str) -> list[TaskEvent]:
    with Session(engine) as s:
        return list(s.exec(select(TaskEvent).where(TaskEvent.task_id == task_id)))


def test_stopped_is_final() -> None:
    assert not [dst for src, dst in CONTRACT if src == TaskState.stopped]
    assert {TaskState.done, TaskState.failed, TaskState.stopped} == FINISHED
