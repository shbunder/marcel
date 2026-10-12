"""The runner watcher against a fake runner: task states, task events, outages and recovery."""

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, col, select
from test_runner_client_fakes import (
    FakeRunnerServer,
    RecordingAlerts,
    Sleeps,
    settle,
    until,
    watch_for,
)
from test_tasks_fakes import (  # noqa: F401  (fixtures)
    AGENT,
    RecordingPublisher,
    outbox_fixture,
    publisher_fixture,
    put_task,
    registry_fixture,
    run,
    runner_fixture,
    validator,
)

from marcel_hub.models import TaskEvent, TaskState
from marcel_hub.runner_client import UNREACHABLE, OffsetStore, RunnerWatch
from marcel_hub.tasks.registry import TaskRegistry

TASK_EVENT = validator('app-api.yaml#/components/schemas/TaskEvent')
TASK = validator('app-api.yaml#/components/schemas/Task')


@pytest.fixture
def fake() -> FakeRunnerServer:
    return FakeRunnerServer()


@pytest.fixture
def alerts() -> RecordingAlerts:
    return RecordingAlerts()


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


def stored(registry: TaskRegistry, task_id: str, *types: str) -> list[TaskEvent]:
    with Session(registry.engine) as s:
        rows = s.exec(
            select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(col(TaskEvent.seq))
        )
        return [r for r in rows if not types or r.type.value in types]


def transcript(registry: TaskRegistry, task_id: str) -> list[str]:
    return [
        r.data_json.get('text') or r.data_json.get('summary') or r.type.value
        for r in stored(registry, task_id, 'text', 'tool_call', 'tool_result', 'error')
    ]


TEXT = 'text', {'role': 'assistant', 'text': 'Looking at the disk.'}
CALL = 'tool_call', {'tool_use_id': 'tu_1', 'tool': 'Bash', 'summary': 'df -h'}


def test_the_fake_runner_drives_a_task_from_start_to_done(
    registry: TaskRegistry,
    fake: FakeRunnerServer,
    alerts: RecordingAlerts,
    sleeps: Sleeps,
    publisher: RecordingPublisher,
) -> None:
    async def scenario() -> None:
        watch = watch_for(fake, registry, alerts, sleeps)
        watch.start()
        await until(lambda: watch.reachable is True)

        task = await registry.spawn(AGENT, 'Check the disk', prompt='Check disk usage.')
        assert task.state == TaskState.starting
        sid = task.session_id
        assert sid is not None and fake.spawned[0]['prompt'] == 'Check disk usage.'
        await until(lambda: registry.get(task.id).state == TaskState.working)

        fake.push_state(sid, status='busy', remote_url='https://claude.ai/code/session_01abc')
        fake.write(sid, (120, 0, *TEXT), (120, 1, *CALL))
        fake.write(sid, (180, 0, 'thinking', {'duration_ms': 40}))
        await until(lambda: len(transcript(registry, task.id)) == 2)
        assert registry.get(task.id).remote_url == 'https://claude.ai/code/session_01abc'

        fake.push_state(
            sid,
            state='blocked',
            status='waiting',
            waiting_for='permission prompt',
            needs='approve Bash: rm -rf /tmp/x',
        )
        await until(lambda: registry.get(task.id).state == TaskState.needs_you)
        assert registry.get(task.id).waiting_for == 'Your approval: approve Bash: rm -rf /tmp/x'

        fake.push_state(sid, state='working', status='busy', waiting_for=None, needs=None)
        await until(lambda: registry.get(task.id).state == TaskState.working)
        fake.write(sid, (260, 0, 'text', {'role': 'assistant', 'text': 'Done: 12 GB free.'}))
        fake.push_state(sid, state='done', status='idle', result='12 GB free on /')
        await until(lambda: registry.get(task.id).state == TaskState.done)
        await until(lambda: len(transcript(registry, task.id)) == 3)
        await watch.close()

        final = registry.get(task.id)
        assert final.summary == '12 GB free on /'
        assert transcript(registry, task.id) == [
            'Looking at the disk.',
            'df -h',
            'Done: 12 GB free.',
        ]
        states = [e.data_json['to'] for e in stored(registry, task.id, 'state')]
        assert states == ['starting', 'working', 'needs_you', 'working', 'done']
        assert alerts.raised == []
        # The app sees every stored event, and each one matches the contract.
        events = [d for kind, d in publisher.sent if kind == 'task.event']
        assert len(events) == len(stored(registry, task.id))
        for data in events:
            TASK_EVENT.validate(data)
        TASK.validate(registry.to_api(final))

    run(scenario())


def test_killing_the_runner_raises_exactly_one_alert_and_reconnecting_clears_it(
    registry: TaskRegistry, fake: FakeRunnerServer, alerts: RecordingAlerts, sleeps: Sleeps
) -> None:
    async def scenario() -> None:
        watch = watch_for(fake, registry, alerts, sleeps)
        watch.start()
        await until(lambda: watch.reachable is True)
        busy = await registry.spawn(AGENT, 'Busy', prompt='Work.')
        finished = await registry.spawn(AGENT, 'Finished', prompt='Work.')
        assert busy.session_id and finished.session_id
        fake.push_state(finished.session_id, state='done')
        await until(lambda: registry.get(finished.id).state == TaskState.done)
        await until(lambda: registry.get(busy.id).state == TaskState.working)

        fake.kill()
        await until(lambda: len(sleeps.delays) >= 5)  # several failed reconnects

        assert [cause for cause, _, _ in alerts.raised] == [UNREACHABLE]
        assert '1 task now show' in alerts.raised[0][2]
        assert registry.get(busy.id).state == TaskState.unknown
        assert registry.get(finished.id).state == TaskState.done
        assert watch.reachable is False and 'not answering' in watch.reason

        fake.revive()
        await until(lambda: watch.reachable is True)
        await until(lambda: registry.get(busy.id).state == TaskState.working)
        await watch.close()

        assert alerts.cleared == [UNREACHABLE]
        assert len(alerts.raised) == 1
        reasons = [e.data_json['reason'] for e in stored(registry, busy.id, 'state')]
        assert reasons[-2:] == ['The runner is unreachable', 'The runner reports working']

    run(scenario())


def test_tasks_queued_while_the_runner_was_down_start_once_it_comes_back(
    registry: TaskRegistry, fake: FakeRunnerServer, alerts: RecordingAlerts, sleeps: Sleeps
) -> None:
    async def scenario() -> None:
        fake.cap = 1
        watch = watch_for(fake, registry, alerts, sleeps)
        watch.start()
        await until(lambda: watch.reachable is True)
        first = await registry.spawn(AGENT, 'One', prompt='One.')
        second = await registry.spawn(AGENT, 'Two', prompt='Two.')
        assert second.state == TaskState.queued

        fake.kill()
        await until(lambda: watch.reachable is False)
        third = await registry.spawn(AGENT, 'Three', prompt='Three.')
        assert third.state == TaskState.queued  # it waits behind the queue, not failed

        fake.cap = 3  # slots are free when the runner comes back
        fake.revive()
        await until(lambda: watch.reachable is True)
        await settle()
        await watch.close()

        assert registry.get(first.id).state == TaskState.working
        assert registry.get(second.id).state != TaskState.queued
        assert registry.get(third.id).state != TaskState.queued
        assert [s['prompt'] for s in fake.spawned] == ['One.', 'Two.', 'Three.']

    run(scenario())


def test_reconnecting_backs_off_up_to_a_cap_and_starts_over_after_success(
    registry: TaskRegistry, fake: FakeRunnerServer, alerts: RecordingAlerts, sleeps: Sleeps
) -> None:
    async def scenario() -> None:
        fake.kill()
        watch = watch_for(fake, registry, alerts, sleeps, base_delay=0.5, max_delay=4.0)
        watch.start()
        await until(lambda: len(sleeps.delays) >= 6)
        assert sleeps.delays[:6] == [0.5, 1.0, 2.0, 4.0, 4.0, 4.0]

        fake.revive()
        await until(lambda: watch.reachable is True)
        count = len(sleeps.delays)
        fake.kill()  # the open stream breaks: the next wait starts small again
        await until(lambda: len(sleeps.delays) >= count + 2)
        await watch.close()
        assert sleeps.delays[count : count + 2] == [0.5, 1.0]

    run(scenario())


def test_a_broken_tail_resumes_from_its_offset_without_duplicates(
    registry: TaskRegistry,
    fake: FakeRunnerServer,
    alerts: RecordingAlerts,
    sleeps: Sleeps,
    tmp_path: Path,
) -> None:
    offsets = OffsetStore(tmp_path / 'runner-offsets.json')

    async def scenario() -> None:
        watch = watch_for(fake, registry, alerts, sleeps, offsets=offsets)
        watch.start()
        await until(lambda: watch.reachable is True)
        task = await registry.spawn(AGENT, 'Tail', prompt='Go.')
        sid = task.session_id
        assert sid is not None
        await until(lambda: registry.get(task.id).state == TaskState.working)

        line = [('text', {'role': 'assistant', 'text': f'line 100 event {i}'}) for i in range(3)]
        fake.tail_drop_after = 1  # the stream breaks in the middle of line 100
        fake.write(sid, *[(100, i, kind, data) for i, (kind, data) in enumerate(line)])
        fake.write(sid, (200, 0, 'text', {'role': 'assistant', 'text': 'line 200'}))
        await until(lambda: len(transcript(registry, task.id)) == 4)
        await settle()
        await watch.close()
        return sid, task.id

    sid, task_id = run(scenario())
    assert transcript(registry, task_id) == [
        'line 100 event 0',
        'line 100 event 1',
        'line 100 event 2',
        'line 200',
    ]
    # The second request re-read line 100 from its start (offset 0) and skipped event 0.
    assert fake.tail_offsets(sid)[:2] == [0, 0]
    assert json.loads((tmp_path / 'runner-offsets.json').read_text())[sid] == [100, 200, 0]

    async def after_restart() -> None:
        # A new hub process reads the stored offsets and asks only for what follows line 100.
        watch = watch_for(fake, registry, alerts, sleeps, offsets=OffsetStore(offsets.path))
        watch.start()
        fake.write(sid, (300, 0, 'text', {'role': 'assistant', 'text': 'line 300'}))
        await until(lambda: len(transcript(registry, task_id)) == 5)
        await settle()
        await watch.close()

    run(after_restart())
    assert transcript(registry, task_id)[-2:] == ['line 200', 'line 300']
    assert fake.tail_offsets(sid)[-1] == 100


def test_bad_events_are_dropped_and_the_stream_carries_on(
    registry: TaskRegistry,
    fake: FakeRunnerServer,
    alerts: RecordingAlerts,
    sleeps: Sleeps,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> str:
        watch = watch_for(fake, registry, alerts, sleeps)
        watch.start()
        await until(lambda: watch.reachable is True)
        task = await registry.spawn(AGENT, 'Bad', prompt='Go.')
        sid = task.session_id
        assert sid is not None
        await until(lambda: registry.get(task.id).state == TaskState.working)

        fake.push_raw('not json')
        fake.push_raw(json.dumps({'seq': fake.seq + 1, 'type': 'session.exploded', 'at': 'x'}))
        bad_session = {**fake.sessions[sid], 'state': 'confused'}
        fake.push_raw(
            json.dumps(
                {
                    'seq': fake.seq + 1,
                    'type': 'session.state',
                    'at': '2026-10-04T09:05:02Z',
                    'session': bad_session,
                }
            )
        )
        fake.write_raw(sid, 50, '{"broken')
        fake.write(sid, (60, 0, 'text', {'role': 'assistant'}))  # no `text`
        fake.write(sid, (70, 0, 'smoke_signal', {}))
        fake.write(sid, (80, 0, 'text', {'role': 'assistant', 'text': 'Still here.'}))
        fake.push_state(sid, state='done')
        await until(lambda: registry.get(task.id).state == TaskState.done)
        await until(lambda: transcript(registry, task.id) == ['Still here.'])
        await watch.close()
        return task.id

    with caplog.at_level(logging.WARNING, logger='marcel_hub.runner_client'):
        run(scenario())
    bad = [r.getMessage() for r in caplog.records if 'Ignored a bad' in r.getMessage()]
    assert len(bad) == 6
    assert any('confused' in m for m in bad)
    assert any('lacks text' in m for m in bad)


def test_a_session_the_hub_stopped_stays_stopped(
    registry: TaskRegistry, fake: FakeRunnerServer, alerts: RecordingAlerts, sleeps: Sleeps
) -> None:
    async def scenario() -> None:
        watch = watch_for(fake, registry, alerts, sleeps)
        watch.start()
        await until(lambda: watch.reachable is True)
        task = await registry.spawn(AGENT, 'Stop me', prompt='Go.')
        assert task.session_id
        await until(lambda: registry.get(task.id).state == TaskState.working)
        await registry.stop(task.id)
        fake.push_state(task.session_id, state='done')  # SP2: a stopped session can show as done
        fake.push_gone(task.session_id)
        await settle()
        await watch.close()
        assert fake.stopped == [task.session_id]
        assert registry.get(task.id).state == TaskState.stopped

    run(scenario())


def test_a_usage_limit_waits_on_you_and_names_the_reset_time(
    registry: TaskRegistry, fake: FakeRunnerServer, alerts: RecordingAlerts, sleeps: Sleeps
) -> None:
    fake.usage_reading = {
        'five_hour': {'used_pct': 100, 'resets_at': '2026-10-04T14:29:00Z'},
        'weekly': {'used_pct': 66, 'resets_at': '2026-10-09T01:59:00Z'},
        'source': 'cli_usage',
        'taken_at': '2026-10-04T10:30:12Z',
        'stale': False,
    }

    async def scenario() -> None:
        watch = watch_for(fake, registry, alerts, sleeps)
        watch.start()
        await until(lambda: watch.reachable is True)
        one = await registry.spawn(AGENT, 'One', prompt='Go.')
        two = await registry.spawn(AGENT, 'Two', prompt='Go.')
        assert one.session_id and two.session_id
        await until(lambda: registry.get(two.id).state == TaskState.working)
        fake.push_state(one.session_id, state='blocked', needs='usage limit reached — check plan')
        fake.write(two.session_id, (90, 0, 'error', {'message': 'Limit.', 'usage_limit': True}))
        await until(lambda: registry.get(one.id).state == TaskState.needs_you)
        await until(lambda: registry.get(two.id).state == TaskState.needs_you)
        await watch.close()
        for task_id in (one.id, two.id):
            assert registry.get(task_id).waiting_for == (
                'The usage limit is reached. It resets Sun 04 Oct, 14:29 UTC.'
            )

    run(scenario())


def test_a_runner_warning_is_raised_as_an_alert(
    registry: TaskRegistry, fake: FakeRunnerServer, alerts: RecordingAlerts, sleeps: Sleeps
) -> None:
    async def scenario() -> None:
        watch = watch_for(fake, registry, alerts, sleeps)
        watch.start()
        await until(lambda: watch.reachable is True)
        fake.push_raw(
            json.dumps(
                {
                    'seq': fake.seq + 1,
                    'type': 'runner.warning',
                    'at': '2026-10-04T09:05:02Z',
                    'message': 'The supervisor went away.',
                }
            )
        )
        await until(lambda: bool(alerts.raised))
        await watch.close()
        assert alerts.raised == [
            ('runner_warning', 'The NUC has a problem', 'The supervisor went away.')
        ]

    run(scenario())


def test_runner_recovered_clears_the_warning_alert(
    registry: TaskRegistry, fake: FakeRunnerServer, alerts: RecordingAlerts, sleeps: Sleeps
) -> None:
    def event(kind: str, message: str) -> str:
        at = '2026-10-04T09:05:02Z'
        return json.dumps({'seq': fake.seq + 1, 'type': kind, 'at': at, 'message': message})

    async def scenario() -> None:
        watch = watch_for(fake, registry, alerts, sleeps)
        watch.start()
        await until(lambda: watch.reachable is True)
        fake.push_raw(event('runner.warning', 'Listing sessions failed three times.'))
        await until(lambda: bool(alerts.raised))
        fake.push_raw(json.dumps({'seq': fake.seq + 1, 'type': 'runner.recovered', 'at': 'x'}))
        fake.push_raw(event('runner.rebooted', 'Not in the contract.'))  # dropped safely
        fake.push_raw(event('runner.recovered', 'The NUC can list its sessions again.'))
        await until(lambda: bool(alerts.cleared))
        await settle()
        await watch.close()
        assert watch.reachable is True

    run(scenario())
    assert [cause for cause, _, _ in alerts.raised] == ['runner_warning']
    assert alerts.cleared == ['runner_warning']


def test_a_short_outage_where_nothing_changed_brings_the_task_back(
    registry: TaskRegistry, fake: FakeRunnerServer, alerts: RecordingAlerts, sleeps: Sleeps
) -> None:
    """The runner resumes from the hub's Last-Event-ID: the missed events come first, then the
    fresh view. A task whose session did not change has no missed event, so only that closing
    view takes it back from `unknown` and restarts its transcript tail."""

    async def scenario() -> None:
        watch = watch_for(fake, registry, alerts, sleeps)
        watch.start()
        await until(lambda: watch.reachable is True)
        quiet = await registry.spawn(AGENT, 'Quiet', prompt='Work.')
        busy = await registry.spawn(AGENT, 'Busy', prompt='Work.')
        assert quiet.session_id and busy.session_id
        await until(lambda: registry.get(quiet.id).state == TaskState.working)
        fake.write(quiet.session_id, (100, 0, *TEXT))
        await until(lambda: len(transcript(registry, quiet.id)) == 1)
        resumed_from = watch.last_seq
        seen: list[dict[str, Any]] = []
        handle = watch._on_runner_event

        async def recording(raw: str) -> None:
            seen.append(json.loads(raw))
            await handle(raw)

        watch._on_runner_event = recording  # type: ignore[method-assign]
        tails_before = len(fake.tail_offsets(quiet.session_id))

        fake.kill()
        await until(lambda: registry.get(quiet.id).state == TaskState.unknown)
        fake.push_state(busy.session_id, state='done', result='Finished meanwhile.')  # missed
        fake.revive()
        await until(lambda: registry.get(quiet.id).state == TaskState.working)
        await until(lambda: registry.get(busy.id).state == TaskState.done)
        fake.write(quiet.session_id, (200, 0, *CALL))
        await until(lambda: len(transcript(registry, quiet.id)) == 2)
        await watch.close()

        events = [r for r in fake.requests if r.url.path == '/events']
        assert events[-1].headers['Last-Event-ID'] == str(resumed_from)
        assert watch.last_seq == fake.seq  # the fresh view carries the newest seq
        # On reconnect: the missed change first, then the fresh view (one state per session).
        after = [(e['session']['id'], 'previous_state' in e) for e in seen[:3]]
        assert after == [
            (busy.session_id, True),
            (quiet.session_id, False),
            (busy.session_id, False),
        ]
        assert len(fake.tail_offsets(quiet.session_id)) > tails_before  # the tail restarted
        assert transcript(registry, quiet.id) == ['Looking at the disk.', 'df -h']
        reasons = [e.data_json['reason'] for e in stored(registry, quiet.id, 'state')]
        assert reasons[-2:] == ['The runner is unreachable', 'The runner reports working']

    run(scenario())


def test_sessions_that_are_not_tasks_are_ignored(
    registry: TaskRegistry, fake: FakeRunnerServer, alerts: RecordingAlerts, sleeps: Sleeps
) -> None:
    brain = fake.add_session('working', role='brain', name='brain-marcel')

    async def scenario() -> RunnerWatch:
        watch = watch_for(fake, registry, alerts, sleeps)
        watch.start()
        await until(lambda: watch.reachable is True)
        fake.write(brain, (10, 0, *TEXT))
        await settle()
        await watch.close()
        return watch

    run(scenario())
    assert fake.tail_offsets(brain) == []


def test_a_task_back_from_unknown_takes_the_state_the_runner_reports(
    registry: TaskRegistry, fake: FakeRunnerServer, alerts: RecordingAlerts, sleeps: Sleeps
) -> None:
    sid = fake.add_session('blocked', status='waiting', waiting_for='permission prompt')
    task_id = put_task(registry.engine, TaskState.unknown, session_id=sid)

    async def scenario() -> None:
        watch = watch_for(fake, registry, alerts, sleeps)
        watch.start()
        await until(lambda: registry.get(task_id).state == TaskState.needs_you)
        # A repeat with a new ask updates what the task waits for.
        fake.push_state(sid, needs='approve Write: notes.md')
        await until(lambda: 'notes.md' in (registry.get(task_id).waiting_for or ''))
        await watch.close()

    run(scenario())
    assert registry.get(task_id).waiting_for == 'Your approval: approve Write: notes.md'


def test_an_unexpected_error_marks_the_runner_unreachable_and_it_recovers(
    registry: TaskRegistry,
    fake: FakeRunnerServer,
    alerts: RecordingAlerts,
    sleeps: Sleeps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drain = registry.drain
    failures = [RuntimeError('database is locked')] * 3

    async def flaky_drain() -> None:
        if failures:
            raise failures.pop()
        await drain()

    monkeypatch.setattr(registry, 'drain', flaky_drain)

    async def scenario() -> None:
        watch = watch_for(fake, registry, alerts, sleeps)
        watch.start()
        await until(lambda: watch.reachable is False)
        assert 'database is locked' in watch.reason
        assert [cause for cause, _, _ in alerts.raised] == [UNREACHABLE]
        await until(lambda: watch.reachable is True)  # the watcher kept going
        await watch.close()
        assert alerts.cleared == [UNREACHABLE]
        assert sleeps.delays[:3] == [0.5, 1.0, 2.0]

    run(scenario())


def test_a_transcript_feed_that_loses_the_runner_stops_quietly(
    registry: TaskRegistry, fake: FakeRunnerServer, alerts: RecordingAlerts, sleeps: Sleeps
) -> None:
    sid = fake.add_session('working')
    task_id = put_task(registry.engine, TaskState.working, session_id=sid)
    fake.kill()

    async def scenario() -> None:
        watch = watch_for(fake, registry, alerts, sleeps)
        # The event stream notices the outage; the tail itself just ends.
        await watch._read_tail(task_id, sid, follow=True)
        await watch.close()

    run(scenario())
    assert sleeps.delays == []
    assert registry.get(task_id).state == TaskState.working


def test_the_alert_goes_out_even_when_tasks_cannot_be_marked_unknown(
    registry: TaskRegistry,
    fake: FakeRunnerServer,
    alerts: RecordingAlerts,
    sleeps: Sleeps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake.kill()

    async def scenario() -> RunnerWatch:
        watch = watch_for(fake, registry, alerts, sleeps)

        def locked() -> list[str]:
            raise RuntimeError('database is locked')

        monkeypatch.setattr(watch, '_live_nuc_tasks', locked)
        watch.start()
        await until(lambda: bool(alerts.raised))
        await watch.close()
        return watch

    watch = run(scenario())
    assert 'its tasks may show an old state' in alerts.raised[0][2]
    assert watch.reachable is False


def test_a_failing_alert_still_leaves_the_runner_marked_unreachable(
    registry: TaskRegistry, fake: FakeRunnerServer, sleeps: Sleeps
) -> None:
    class BrokenAlerts(RecordingAlerts):
        def broke(self, cause: str, title: str, body: str) -> None:
            raise RuntimeError('notifier down')

    async def scenario() -> RunnerWatch:
        watch = watch_for(fake, registry, BrokenAlerts(), sleeps)
        watch.reachable = True
        await watch._fail('The hub could not follow the runner: boom.')
        return watch

    watch = run(scenario())
    assert (watch.reachable, watch.reason) == (False, 'The hub could not follow the runner: boom.')
