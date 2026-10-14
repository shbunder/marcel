"""Claude Code updated under Marcel (found live on the NUC): the hub marks the tasks whose sessions
ended, tells the owner once, and shows it in `/internal/health`. The real app, a fake runner."""

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, select
from test_channel_fakes import SECRET, ChannelHub, channel_hub
from test_whole_path_health import FakeRunnerHealth

from marcel_hub import claude_updates
from marcel_hub.claude_updates import ClaudeUpdates
from marcel_hub.models import Message, MessageRole, Task, TaskEvent, TaskEventType, TaskState
from marcel_hub.runner_client import RunnerUnavailable

PINNED, NEWER = '2.1.296', '2.1.301'


class FakeRunner:
    def __init__(self) -> None:
        self.version = PINNED
        self.sessions_list: list[dict[str, Any]] = []
        self.down = False

    async def health(self) -> dict[str, Any]:
        if self.down:
            raise RunnerUnavailable('The runner is not answering.')
        return {'ok': self.version == PINNED, 'claude_version': f'{self.version} (Claude Code)',
                'claude_version_expected': PINNED}  # fmt: skip

    async def sessions(self) -> list[dict[str, Any]]:
        return self.sessions_list


class Pushes:
    def __init__(self) -> None:
        self.sent: list[tuple[str, ...]] = []

    def note(self, agent_id: str, message_id: str, title: str, body: str, level: str,
             user_id: str | None) -> bool:  # fmt: skip
        self.sent.append((agent_id, body))
        return True


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[ChannelHub]:
    with channel_hub(tmp_path) as hub:
        yield hub


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def pushes() -> Pushes:
    return Pushes()


def watcher(hub: ChannelHub, runner: FakeRunner, pushes: Pushes) -> ClaudeUpdates:
    return ClaudeUpdates(hub.engine, runner, hub.registry, hub.app.state.events, pushes,
                         hub.tmp_path)  # fmt: skip


def check(w: ClaudeUpdates) -> None:
    asyncio.run(w.check())


def notices(hub: ChannelHub) -> list[str]:
    with Session(hub.engine) as s:
        rows = s.exec(select(Message).where(Message.role == MessageRole.system)).all()
        return [m.body_json['text'] for m in rows]


def state(hub: ChannelHub, task_id: str) -> Task:
    with Session(hub.engine) as s:
        task = s.get(Task, task_id)
        assert task is not None
        return task


def test_an_update_marks_the_ended_tasks_and_tells_the_owner_once(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    w = watcher(hub, runner, pushes)
    check(w)  # on the pin: nothing to say
    assert notices(hub) == [] and pushes.sent == []
    ended, _ = hub.task(TaskState.working)
    asked, _ = hub.task(TaskState.needs_you)
    alive, alive_sid = hub.task(TaskState.working)
    finished, _ = hub.task(TaskState.done)
    runner.sessions_list = [{'id': alive_sid, 'state': 'working'}]
    runner.version = NEWER
    check(w)
    for task_id in (ended, asked):
        task = state(hub, task_id)
        assert task.state == TaskState.failed
        [moved] = [e for e in hub.task_events(task_id, 'state') if e.data_json['to'] == 'failed']
        assert moved.data_json['reason'].startswith(f'Claude Code was updated to {NEWER}')
    assert state(hub, alive).state == TaskState.working, 'a live session is left alone'
    assert state(hub, finished).state == TaskState.done
    said = (f'Claude Code was updated to {NEWER}; 2 sessions ended. New work waits until Marcel '
            f'is back on the pinned version {PINNED} — run deploy/install-claude.sh.')  # fmt: skip
    assert notices(hub) == [said]
    assert pushes.sent == [(hub.agent_id, said)]
    check(w)  # the same version again: said once
    assert len(notices(hub)) == 1 and len(pushes.sent) == 1


def test_a_session_that_finished_on_its_own_is_left_to_its_report(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    done, done_sid = hub.task(TaskState.working)
    stopped, stopped_sid = hub.task(TaskState.working)
    runner.sessions_list = [{'id': done_sid, 'state': 'done'},
                            {'id': stopped_sid, 'state': 'stopped'}]  # fmt: skip
    runner.version = NEWER
    check(watcher(hub, runner, pushes))
    assert state(hub, done).state == TaskState.working, 'its own done report comes next'
    assert state(hub, stopped).state == TaskState.failed


def test_sessions_unreadable_records_nothing_and_tries_again(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    task_id, _ = hub.task(TaskState.working)
    runner.version = NEWER
    w = watcher(hub, runner, pushes)
    real = runner.sessions

    async def away() -> list[dict[str, Any]]:
        raise RunnerUnavailable('The runner could not list its sessions.')

    runner.sessions = away  # type: ignore[method-assign]
    check(w)
    assert notices(hub) == [] and pushes.sent == [] and w.open_change() is None
    assert state(hub, task_id).state == TaskState.working
    runner.sessions = real  # type: ignore[method-assign]
    check(w)
    assert state(hub, task_id).state == TaskState.failed
    assert '; 1 session ended.' in notices(hub)[0] and len(pushes.sent) == 1


def test_a_hub_restart_does_not_repeat_it_and_a_change_while_down_is_seen(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    runner.version = NEWER
    check(watcher(hub, runner, pushes))  # first look after a hub start: the change is seen
    assert len(notices(hub)) == 1
    check(watcher(hub, runner, pushes))  # the hub restarted: it remembers
    assert len(notices(hub)) == 1 and len(pushes.sent) == 1
    saved = json.loads((hub.tmp_path / claude_updates.STATE_FILE).read_text())
    assert saved['change']['installed'] == NEWER and saved['seen'] == NEWER


def test_back_on_the_pin_says_so_without_a_push_and_a_new_drift_is_new(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    w = watcher(hub, runner, pushes)
    runner.version = NEWER
    check(w)
    runner.version = PINNED
    check(w)
    assert notices(hub)[-1].startswith(f'Claude Code is back on {PINNED}')
    assert len(pushes.sent) == 1 and w.open_change() is None
    runner.version = '2.1.302'
    check(w)
    assert '2.1.302' in notices(hub)[-1] and len(pushes.sent) == 2


def test_tasks_the_runner_just_failed_are_counted(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    task_id, _ = hub.task(TaskState.failed)
    with Session(hub.engine) as s:
        moved = {'from': 'working', 'to': 'failed', 'reason': 'runner'}
        at = datetime.now(UTC) - timedelta(minutes=2)
        s.add(TaskEvent(task_id=task_id, seq=1, type=TaskEventType.state, at=at, data_json=moved))
        s.commit()
    runner.version = NEWER
    check(watcher(hub, runner, pushes))
    assert '; 1 session ended.' in notices(hub)[0]


def test_a_runner_away_or_quiet_says_nothing(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    runner.down = True
    check(watcher(hub, runner, pushes))
    runner.down = False
    runner.version = NEWER
    w = watcher(hub, runner, pushes)
    check(w)
    assert '; no running session ended.' in notices(hub)[0]


def test_internal_health_shows_the_open_change(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    w = watcher(hub, runner, pushes)
    hub.app.state.claude_updates = w
    hub.app.state.runner_client = FakeRunnerHealth()
    headers = {'X-Marcel-Secret': SECRET}
    assert 'claude_update' not in hub.client.get('/internal/health', headers=headers).json()
    runner.version = NEWER
    check(w)
    body = hub.client.get('/internal/health', headers=headers).json()
    assert body['claude_update']['installed'] == NEWER
    assert body['claude_update']['expected'] == PINNED
    assert body['claude_update']['sessions_ended'] == 0


def test_the_loop_checks_and_stops(hub: ChannelHub, runner: FakeRunner, pushes: Pushes,
                                   monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    monkeypatch.setattr(claude_updates, 'POLL_S', 0.01)
    runner.version = NEWER
    w = watcher(hub, runner, pushes)

    async def run() -> None:
        w.start()
        for _ in range(100):
            if notices(hub):
                break
            await asyncio.sleep(0.01)
        await w.stop()

    asyncio.run(run())
    assert len(notices(hub)) == 1


def test_version_of() -> None:
    assert claude_updates.version_of('2.1.301 (Claude Code)') == '2.1.301'
    assert claude_updates.version_of('') == ''
