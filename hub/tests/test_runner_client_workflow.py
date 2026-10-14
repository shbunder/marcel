"""A workflow's steps in a quest (S-04.16): the hub opens the run's journal when the main transcript
says a workflow started, stores its agents' starts and stops as task events, resumes after a hub
restart without storing one twice, and closes the journal when the run stops or the task ends.
Against the fake runner; nothing reaches a real runner or `claude`."""

import json
import logging
from pathlib import Path

import pytest
from sqlmodel import Session, col, select
from test_runner_client_fakes import FakeRunnerServer, RecordingAlerts, Sleeps, until, watch_for
from test_tasks_fakes import (  # noqa: F401  (fixtures)
    AGENT,
    outbox_fixture,
    publisher_fixture,
    registry_fixture,
    run,
    runner_fixture,
    validator,
)

from marcel_hub.models import TaskEvent, TaskState
from marcel_hub.runner_client import OffsetStore, RunnerWatch, tail_key
from marcel_hub.tasks.registry import TaskRegistry

TASK_EVENT = validator('app-api.yaml#/components/schemas/TaskEvent')
RUN = 'wf_f6861b61-537'
START = 'workflow_start', {'tool_use_id': 'tu_w', 'run_id': RUN, 'name': 'summarize-notes'}
STOP = 'workflow_stop', {'tool_use_id': 'tu_w', 'run_id': RUN, 'status': 'completed'}


def agent_start(agent: str, label: str) -> tuple[str, dict]:
    return 'workflow_agent_start', {'run_id': RUN, 'agent_id': agent, 'label': label}


def agent_stop(agent: str) -> tuple[str, dict]:
    return 'workflow_agent_stop', {'run_id': RUN, 'agent_id': agent, 'summary': f'{agent} done'}


@pytest.fixture
def fake() -> FakeRunnerServer:
    return FakeRunnerServer()


@pytest.fixture
def alerts() -> RecordingAlerts:
    return RecordingAlerts()


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


def steps(registry: TaskRegistry, task_id: str) -> list[tuple[str, str]]:
    """(type, agent or run) of the task's workflow events, in order."""
    with Session(registry.engine) as s:
        rows = s.exec(
            select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(col(TaskEvent.seq))
        )
        out = []
        for r in rows:
            if r.type.value.startswith('workflow_'):
                TASK_EVENT.validate({'task_id': task_id, 'seq': r.seq, 'type': r.type.value,
                                     'at': r.at.isoformat(), 'data': r.data_json})  # fmt: skip
                out.append((r.type.value, r.data_json.get('agent_id') or r.data_json['run_id']))
        return out


def journal_open(watch: RunnerWatch, sid: str) -> bool:
    tail = watch._tails.get(tail_key(sid, RUN))
    return tail is not None and not tail.job.done()


async def started(
    registry: TaskRegistry, fake: FakeRunnerServer, watch: RunnerWatch
) -> tuple[str, str]:
    watch.start()
    await until(lambda: watch.reachable is True)
    task = await registry.spawn(AGENT, 'Summarize notes', prompt='Summarize the notes.')
    assert task.session_id is not None
    await until(lambda: registry.get(task.id).state == TaskState.working)
    return task.id, task.session_id


def test_a_workflow_start_opens_the_journal_and_its_steps_land_in_order(
    registry: TaskRegistry, fake: FakeRunnerServer, alerts: RecordingAlerts, sleeps: Sleeps
) -> None:
    async def scenario() -> None:
        watch = watch_for(fake, registry, alerts, sleeps)
        task_id, sid = await started(registry, fake, watch)
        fake.write(sid, (100, 0, *START))
        await until(lambda: journal_open(watch, sid))
        fake.write_journal(
            sid, RUN, (10, 0, *agent_start('a1', 'note1')), (20, 0, *agent_start('a2', 'note2'))
        )
        fake.write_journal(sid, RUN, (30, 0, *agent_stop('a1')))
        await until(lambda: len(steps(registry, task_id)) == 4)
        assert steps(registry, task_id) == [
            ('workflow_start', RUN), ('workflow_agent_start', 'a1'),
            ('workflow_agent_start', 'a2'), ('workflow_agent_stop', 'a1'),
        ]  # fmt: skip
        assert 'workflow=wf_f6861b61-537' in str(fake.requests[-1].url) or any(
            r.url.params.get('workflow') == RUN for r in fake.requests
        )
        await watch.close()

    run(scenario())


def test_the_journal_closes_after_the_run_stops(
    registry: TaskRegistry, fake: FakeRunnerServer, alerts: RecordingAlerts, sleeps: Sleeps
) -> None:
    async def scenario() -> None:
        watch = watch_for(fake, registry, alerts, sleeps)
        task_id, sid = await started(registry, fake, watch)
        fake.write(sid, (100, 0, *START))
        await until(lambda: journal_open(watch, sid))
        fake.write_journal(sid, RUN, (10, 0, *agent_stop('a1')))
        fake.write(sid, (200, 0, *STOP))
        await until(lambda: not journal_open(watch, sid))
        assert 10.0 in sleeps.delays, 'it lingers 10 s for the last journal lines'
        assert ('workflow_stop', RUN) in steps(registry, task_id)
        assert ('workflow_agent_stop', 'a1') in steps(registry, task_id)
        await watch.close()

    run(scenario())


def test_the_journal_closes_when_the_task_ends(
    registry: TaskRegistry, fake: FakeRunnerServer, alerts: RecordingAlerts, sleeps: Sleeps
) -> None:
    async def scenario() -> None:
        watch = watch_for(fake, registry, alerts, sleeps)
        task_id, sid = await started(registry, fake, watch)
        fake.write(sid, (100, 0, *START))
        await until(lambda: journal_open(watch, sid))
        fake.write_journal(sid, RUN, (10, 0, *agent_start('a1', 'note1')))
        fake.push_state(sid, state='done', status='idle', result='Summaries written.')
        await until(lambda: registry.get(task_id).state == TaskState.done)
        await until(lambda: not journal_open(watch, sid))  # read to its end once, then closed
        assert ('workflow_agent_start', 'a1') in steps(registry, task_id)
        await watch.close()

    run(scenario())


def test_a_hub_restart_resumes_the_journal_without_storing_twice(
    registry: TaskRegistry,
    fake: FakeRunnerServer,
    alerts: RecordingAlerts,
    sleeps: Sleeps,
    tmp_path: Path,
) -> None:
    offsets = tmp_path / 'runner-offsets.json'

    async def scenario() -> None:
        first = watch_for(fake, registry, alerts, sleeps, offsets=OffsetStore(offsets))
        task_id, sid = await started(registry, fake, first)
        fake.write(sid, (100, 0, *START))
        fake.write_journal(sid, RUN, (10, 0, *agent_start('a1', 'note1')))
        await until(lambda: len(steps(registry, task_id)) == 2)
        await first.close()  # the hub stops
        saved = json.loads(offsets.read_text())
        assert saved[tail_key(sid, RUN)][1] == 10, 'the journal position is kept'

        fake.write_journal(sid, RUN, (20, 0, *agent_stop('a1')))
        second = watch_for(fake, registry, alerts, sleeps, offsets=OffsetStore(offsets))
        second.start()
        await until(lambda: second.reachable is True)
        fake.push_state(sid, status='busy')  # the main tail opens again, and the open run's journal
        await until(lambda: len(steps(registry, task_id)) == 3)
        assert steps(registry, task_id) == [
            ('workflow_start', RUN), ('workflow_agent_start', 'a1'), ('workflow_agent_stop', 'a1'),
        ]  # fmt: skip
        await second.close()

    run(scenario())


def test_a_bad_workflow_event_is_logged_and_dropped(
    registry: TaskRegistry,
    fake: FakeRunnerServer,
    alerts: RecordingAlerts,
    sleeps: Sleeps,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        watch = watch_for(fake, registry, alerts, sleeps)
        task_id, sid = await started(registry, fake, watch)
        fake.write(sid, (100, 0, *START))
        await until(lambda: journal_open(watch, sid))
        key = tail_key(sid, RUN)
        fake.transcripts.setdefault(key, [])
        good = agent_start('a1', 'note1')
        fake.transcripts[key].append(  # a journal event naming another run
            {'session_id': sid, 'offset': 10, 'index': 0, 'at': '2026-10-14T03:30:00Z',
             'workflow_id': 'wf_other', 'type': good[0], 'data': good[1]})  # fmt: skip
        fake.transcripts[key].append(  # a bad workflow_id
            {'session_id': sid, 'offset': 20, 'index': 0, 'at': '2026-10-14T03:30:00Z',
             'workflow_id': '../x', 'type': good[0], 'data': good[1]})  # fmt: skip
        fake.transcripts[key].append(  # an unknown field
            {'session_id': sid, 'offset': 30, 'index': 0, 'at': '2026-10-14T03:30:00Z',
             'workflow_id': RUN, 'type': good[0], 'data': good[1], 'secret': 1})  # fmt: skip
        fake.write(
            sid, (200, 0, 'workflow_agent_start', good[1])
        )  # a journal event on the main tail
        other = {**good[1], 'run_id': 'wf_other'}
        fake.write_journal(sid, RUN, (32, 0, 'workflow_agent_start', other))  # another run's step
        fake.write_journal(sid, RUN, (34, 0, 'text', {'role': 'assistant', 'text': 'hi'}))
        fake.write_journal(sid, RUN, (36, 0, 'raw', {'entry_type': 'progress'}))  # kept
        fake.write_journal(sid, RUN, (40, 0, *agent_stop('a1')))
        with caplog.at_level(logging.WARNING):
            await until(lambda: ('workflow_agent_stop', 'a1') in steps(registry, task_id))
        assert steps(registry, task_id) == [('workflow_start', RUN), ('workflow_agent_stop', 'a1')]
        assert caplog.text.count('Ignored a bad transcript event') == 6
        with Session(registry.engine) as s:
            kinds = [r.type.value for r in s.exec(select(TaskEvent).where(
                TaskEvent.task_id == task_id))]  # fmt: skip
        assert 'raw' in kinds and 'text' not in kinds
        await watch.close()

    run(scenario())


def test_tail_keys_never_meet_a_session_id() -> None:
    assert tail_key('s-1', None) == 's-1'
    assert tail_key('s-1', RUN) == f's-1?workflow={RUN}'
