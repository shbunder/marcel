"""Claude Code updated under Marcel (found live on the NUC): on the runner's events (S-04.17) the
hub marks the tasks whose sessions the restart ended, tells the owner once, pushes "updated away"
as urgent, and shows the open change in `/internal/health`. The real app, a fake runner."""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, select
from test_channel_fakes import SECRET, ChannelHub, channel_hub
from test_whole_path_health import FakeRunnerHealth

from marcel_hub import claude_updates
from marcel_hub.app import Settings, create_app
from marcel_hub.claude_updates import ClaudeUpdates
from marcel_hub.models import Message, MessageRole, Task, TaskState
from marcel_hub.runner_client import RunnerUnavailable, check_runner_event

PINNED, NEWER = '2.1.296', '2.1.301'


class FakeRunner:
    def __init__(self) -> None:
        self.version = PINNED
        self.sessions_list: list[dict[str, Any]] = []
        self.down = False
        self.login: dict[str, Any] | None = None  # runner-api 0.1.13; None: an older runner
        self.reading: dict[str, Any] | None = None  # `GET /usage`
        self.usage_fails = False

    async def health(self) -> dict[str, Any]:
        if self.down:
            raise RunnerUnavailable('The runner is not answering.')
        installed = f'{self.version} (Claude Code)' if self.version else ''
        answer = {'ok': self.version == PINNED, 'claude_version': installed,
                  'claude_version_expected': PINNED}  # fmt: skip
        if self.login is not None:
            answer['login'] = self.login
        return answer

    async def usage(self) -> dict[str, Any] | None:
        if self.usage_fails:
            raise RuntimeError('boom')
        return self.reading

    async def sessions(self) -> list[dict[str, Any]]:
        return self.sessions_list


class Pushes:
    def __init__(self) -> None:
        self.sent: list[tuple[str, ...]] = []  # notes
        self.levels: list[str] = []
        self.urgent: list[tuple[str, ...]] = []  # broke
        self.cleared: list[str] = []

    def note(self, agent_id: str, message_id: str, title: str, body: str, level: str,
             user_id: str | None) -> bool:  # fmt: skip
        self.sent.append((agent_id, body))
        self.levels.append(level)
        return True

    def broke(self, cause: str, title: str, body: str, task_id: str | None = None) -> None:
        self.urgent.append((cause, title, body))

    def resolved(self, cause: str) -> None:
        self.cleared.append(cause)


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


def changed(to: str, seq: int = 1) -> dict[str, Any]:
    return check_runner_event(json.dumps({
        'seq': seq, 'type': 'runner.claude_changed', 'at': '2026-10-14T03:00:00Z',
        'message': 'Claude Code changed.',
        'claude_change': {'from': '2.1.290', 'to': f'{to} (Claude Code)',
                          'expected': PINNED}}))  # fmt: skip


def restarted(session_ids: list[str], cause: str | None = None) -> dict[str, Any]:
    restart: dict[str, Any] = {'session_ids': session_ids}
    if cause is not None:
        restart['cause'] = cause
    return check_runner_event(json.dumps({
        'seq': 2, 'type': 'runner.supervisor_restarted', 'at': '2026-10-14T03:00:01Z',
        'message': 'The supervisor restarted.', 'supervisor_restart': restart}))  # fmt: skip


def on(w: ClaudeUpdates, event: dict[str, Any]) -> None:
    asyncio.run(w.on_runner_event(event))


def notices(hub: ChannelHub) -> list[str]:
    with Session(hub.engine) as s:
        rows = s.exec(select(Message).where(Message.role == MessageRole.system)).all()
        return [m.body_json['text'] for m in rows]


def state(hub: ChannelHub, task_id: str) -> Task:
    with Session(hub.engine) as s:
        task = s.get(Task, task_id)
        assert task is not None
        return task


AWAY = (f'Claude Code was updated to {NEWER}. New work waits until Marcel is back on the pinned '
        f'version {PINNED} — run deploy/install-claude.sh.')  # fmt: skip


def test_a_restart_marks_exactly_its_sessions_and_names_the_cause(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    first, first_sid = hub.task(TaskState.working)
    second, second_sid = hub.task(TaskState.needs_you)
    third, _ = hub.task(TaskState.working)
    on(watcher(hub, runner, pushes), restarted([first_sid, second_sid, 'a-brain'], 'upgrade'))
    for task_id in (first, second):
        assert state(hub, task_id).state == TaskState.failed
        [moved] = [e for e in hub.task_events(task_id, 'state') if e.data_json['to'] == 'failed']
        assert moved.data_json['reason'].startswith('Claude Code updated itself and restarted')
    assert state(hub, third).state == TaskState.working, 'a session the restart kept is left alone'
    said = 'Claude Code updated itself and restarted; 2 quests stopped.'
    assert notices(hub) == [said]
    assert pushes.sent == [(hub.agent_id, said)] and pushes.urgent == []


@pytest.mark.parametrize(
    ('cause', 'said'),
    [
        (None, "Claude's supervisor restarted; 1 quest stopped."),
        ('crash', "Claude's supervisor restarted (crash); 1 quest stopped."),
    ],
)
def test_a_restart_without_a_known_cause_says_so_plainly(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes, cause: str | None, said: str
) -> None:
    task_id, sid = hub.task(TaskState.working)
    on(watcher(hub, runner, pushes), restarted([sid], cause))
    assert notices(hub) == [said]
    [moved] = [e for e in hub.task_events(task_id, 'state') if e.data_json['to'] == 'failed']
    assert moved.data_json['reason'].startswith("Claude's supervisor restarted")


def test_a_restart_that_ended_no_session_says_nothing(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    task_id, _ = hub.task(TaskState.working)
    on(watcher(hub, runner, pushes), restarted([], 'upgrade'))
    assert notices(hub) == [] and pushes.sent == []
    assert state(hub, task_id).state == TaskState.working


def test_a_finished_task_is_not_marked_again(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    task_id, sid = hub.task(TaskState.done)
    on(watcher(hub, runner, pushes), restarted([sid], 'upgrade'))
    assert state(hub, task_id).state == TaskState.done
    assert notices(hub) == ['Claude Code updated itself and restarted; no quest stopped.']
    assert pushes.sent == [], 'no quest stopped: told in the chat, not pushed'


def test_away_and_back_give_one_notice_each_and_away_is_urgent(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    w = watcher(hub, runner, pushes)
    on(w, changed(NEWER))
    assert notices(hub) == [AWAY]
    assert pushes.urgent == [(claude_updates.CAUSE, 'Claude Code was updated', AWAY)]
    on(w, changed(NEWER, seq=3))  # the same version again: said once
    assert len(notices(hub)) == 1 and len(pushes.urgent) == 1
    on(w, changed(PINNED, seq=4))
    assert notices(hub) == [AWAY, f'Claude Code is back on {PINNED}, the version Marcel is tested '
                                  'with. New work starts again.']  # fmt: skip
    assert pushes.sent == [] and len(pushes.urgent) == 1, 'back is not pushed'
    assert pushes.cleared == [claude_updates.CAUSE] and w.open_change() is None
    on(w, changed('2.1.302', seq=5))  # a new drift is new
    assert '2.1.302' in notices(hub)[-1] and len(pushes.urgent) == 2


def test_a_restart_during_a_change_counts_its_sessions(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    w = watcher(hub, runner, pushes)
    _, sid = hub.task(TaskState.working)
    on(w, changed(NEWER))
    on(w, restarted([sid], 'upgrade'))
    change = w.open_change()
    assert change is not None and change['sessions_ended'] == 1


def test_start_up_sees_a_change_made_while_the_hub_was_down_once(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    runner.version = NEWER
    check(watcher(hub, runner, pushes))  # first look after a hub start: the change is seen
    assert notices(hub) == [AWAY] and len(pushes.urgent) == 1
    check(watcher(hub, runner, pushes))  # the hub restarted: it remembers
    on(watcher(hub, runner, pushes), changed(NEWER))  # and the runner's event agrees
    assert len(notices(hub)) == 1 and len(pushes.urgent) == 1
    saved = json.loads((hub.tmp_path / claude_updates.STATE_FILE).read_text())
    assert saved['change']['installed'] == NEWER and saved['seen'] == NEWER


def test_start_up_with_the_runner_away_says_nothing(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    runner.down = True
    check(watcher(hub, runner, pushes))
    assert notices(hub) == [] and pushes.urgent == []


def test_internal_health_shows_the_open_change(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    w = watcher(hub, runner, pushes)
    hub.app.state.claude_updates = w
    hub.app.state.runner_client = FakeRunnerHealth()
    headers = {'X-Marcel-Secret': SECRET}
    assert 'claude_update' not in hub.client.get('/internal/health', headers=headers).json()
    on(w, changed(NEWER))
    body = hub.client.get('/internal/health', headers=headers).json()
    assert body['claude_update']['installed'] == NEWER
    assert body['claude_update']['expected'] == PINNED
    assert body['claude_update']['sessions_ended'] == 0


def test_start_looks_once_and_stops(hub: ChannelHub, runner: FakeRunner, pushes: Pushes) -> None:
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


def test_the_app_hands_the_runners_events_to_it(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db',
                              runner_sock=tmp_path / 'runner.sock'))  # fmt: skip
    updates = app.state.claude_updates
    assert updates.on_runner_event in app.state.runner_watch.runner_listeners


# The login and why usage is stale (runner-api 0.1.13, S-04.18) ---------------------------------

LOGGED_OUT = ('Claude Code on the NUC is logged out. Log in again: on the NUC run '
              '`claude auth login`.')  # fmt: skip


def lost(since: str = '2026-10-13T06:12:00Z') -> dict[str, Any]:
    return {'state': 'lost', 'since': since, 'message': LOGGED_OUT}


def look(w: ClaudeUpdates) -> None:
    asyncio.run(w.look())


def test_a_lost_login_is_told_once_and_urgently(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    runner.login = lost()
    w = watcher(hub, runner, pushes)
    for _ in range(3):
        look(w)
    assert notices(hub) == [LOGGED_OUT]
    assert pushes.sent == [(hub.agent_id, LOGGED_OUT)] and pushes.levels == ['urgent']


def test_a_lost_login_is_read_even_when_claude_cannot_say_its_version(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    runner.version, runner.login = '', lost()
    check(watcher(hub, runner, pushes))
    assert notices(hub) == [LOGGED_OUT]


def test_a_hub_restart_in_the_same_loss_says_nothing_and_a_new_loss_is_told(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    runner.login = lost()
    look(watcher(hub, runner, pushes))
    look(watcher(hub, runner, pushes))  # the hub restarted: it remembers this `since`
    assert len(notices(hub)) == 1 and len(pushes.sent) == 1
    runner.login = lost('2026-10-14T09:00:00Z')
    look(watcher(hub, runner, pushes))
    assert len(notices(hub)) == 2 and len(pushes.sent) == 2


def test_logged_in_again_after_a_told_loss_is_said_once_without_a_push(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    w = watcher(hub, runner, pushes)
    runner.login = {'state': 'ok', 'since': '2026-10-13T05:00:00Z'}
    look(w)
    assert notices(hub) == [], 'no loss told: nothing to say'
    runner.login = lost()
    look(w)
    runner.login = {'state': 'ok', 'since': '2026-10-13T08:15:00Z'}
    look(w)
    look(w)
    assert notices(hub) == [LOGGED_OUT, claude_updates.LOGGED_IN]
    assert len(pushes.sent) == 1, 'logged in again is not pushed'


@pytest.mark.parametrize(
    'login', [None, {'state': 'unknown', 'message': 'Could not run claude auth status.'}]
)
def test_no_login_or_unknown_says_nothing(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes, login: dict[str, Any] | None
) -> None:
    runner.login = login
    look(watcher(hub, runner, pushes))
    assert notices(hub) == [] and pushes.sent == []


def test_a_runner_away_skips_the_look_quietly(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    runner.down, runner.usage_fails = True, True
    w = watcher(hub, runner, pushes)
    assert asyncio.run(w.look()) is None
    assert notices(hub) == []


STALE = {'five_hour': None, 'weekly': None, 'stale': True,
         'reason': 'The /usage screen could not be read.'}  # fmt: skip
GOOD = {'five_hour': None, 'weekly': None, 'stale': False}


def test_why_usage_is_stale_is_told_once_per_reason(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    w = watcher(hub, runner, pushes)
    said = f'Marcel cannot read plan usage right now: {STALE["reason"]}'
    runner.reading = STALE
    look(w)
    look(w)  # the same reason again
    assert notices(hub) == [said] and pushes.sent == []
    runner.reading = {**STALE, 'reason': None}
    del runner.reading['reason']  # stale with no reason: not a good reading
    look(w)
    runner.reading = STALE
    look(w)
    assert notices(hub) == [said]
    runner.reading = GOOD  # a good reading in between
    look(w)
    runner.reading = STALE
    look(w)
    assert notices(hub) == [said, said]


def test_a_reason_that_is_the_told_login_loss_is_not_said_twice(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    runner.login, runner.reading = lost(), {**STALE, 'reason': LOGGED_OUT}
    look(watcher(hub, runner, pushes))
    assert notices(hub) == [LOGGED_OUT]


def test_no_reading_or_a_failed_one_is_skipped(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes
) -> None:
    w = watcher(hub, runner, pushes)
    look(w)  # no reading
    runner.usage_fails = True
    look(w)
    assert notices(hub) == []


def test_version_of() -> None:
    assert claude_updates.version_of('2.1.301 (Claude Code)') == '2.1.301'
    assert claude_updates.version_of('') == ''


def test_the_loop_keeps_looking_at_the_login(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude_updates, 'LOOK_S', 0.01)
    w = watcher(hub, runner, pushes)

    async def run() -> None:
        w.start()
        await asyncio.sleep(0.05)  # a few looks with nothing to say
        runner.login = lost()
        for _ in range(100):
            if notices(hub):
                break
            await asyncio.sleep(0.01)
        await w.stop()

    asyncio.run(run())
    assert notices(hub) == [LOGGED_OUT]


def test_a_runner_away_at_start_up_has_its_version_read_on_a_later_look(
    hub: ChannelHub, runner: FakeRunner, pushes: Pushes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude_updates, 'LOOK_S', 0.01)
    runner.down, runner.version = True, NEWER  # changed while the hub was down
    w = watcher(hub, runner, pushes)

    async def run() -> None:
        w.start()
        await asyncio.sleep(0.05)  # the first looks find no runner
        runner.down = False
        for _ in range(100):
            if notices(hub):
                break
            await asyncio.sleep(0.01)
        await w.stop()

    asyncio.run(run())
    assert notices(hub) == [AWAY]
