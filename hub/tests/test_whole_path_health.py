"""`GET /internal/health` (found live on the NUC: "process up" is not "Marcel answers").

The real app with the real `/channel` socket: a brain that is connected and pinging is ok; no
brain, a dropped socket, a silent one, a runner that is down or slow, and a request through the
tunnel are each told apart. Nothing here reaches a real runner, `claude` or the network."""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_channel_fakes import SECRET, ChannelHub, channel_hub

from marcel_hub import path_health as health
from marcel_hub.runner_client import RunnerUnavailable


class FakeRunnerHealth:
    def __init__(self) -> None:
        self.answer: Any = {'ok': True}
        self.delay = 0.0
        self.down = False

    async def health(self) -> Any:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.down:
            raise RunnerUnavailable('The runner is not answering on its socket.')
        return self.answer


@pytest.fixture
def runner() -> FakeRunnerHealth:
    return FakeRunnerHealth()


@pytest.fixture
def hub(tmp_path: Path, runner: FakeRunnerHealth) -> Iterator[ChannelHub]:
    with channel_hub(tmp_path) as hub:
        hub.app.state.runner_client = runner
        yield hub


def check(hub: ChannelHub, **headers: str) -> tuple[int, dict[str, Any]]:
    res = hub.client.get('/internal/health', headers={'X-Marcel-Secret': SECRET, **headers})
    return res.status_code, res.json()


def test_a_connected_pinging_brain_and_a_runner_are_ok(hub: ChannelHub) -> None:
    sid = hub.brain()
    with hub.connect() as channel:
        assert channel.hello(sid)['type'] == 'welcome'
        channel.until_pong()  # the channel server's 20 s ping
        status, body = check(hub)
    assert status == 200, body
    assert body['ok'] is True and body['hub'] == 'ok'
    assert body['runner']['state'] == 'ok' and body['runner']['latency_ms'] >= 0
    assert body['brains'] == [{'agent_id': hub.agent_id, 'state': 'ok', 'last_seen_s': 0}]


def test_no_brain_or_a_dropped_socket_is_down(hub: ChannelHub) -> None:
    status, body = check(hub)
    assert status == 503 and body['ok'] is False
    assert body['brains'][0]['reason'] == 'No brain session yet.'
    assert 'note' not in body
    sid = hub.brain()
    with hub.connect() as channel:
        channel.hello(sid)
        channel.until_pong()
    status, body = check(hub)  # the socket closed: a dead brain process
    assert status == 503
    assert body['brains'][0]['reason'] == 'The brain is not connected to the hub.'


def test_a_silent_brain_is_down(hub: ChannelHub, monkeypatch: pytest.MonkeyPatch) -> None:
    # Control: delete the "sent nothing lately" check and this goes red.
    sid = hub.brain()
    with hub.connect() as channel:
        channel.hello(sid)
        channel.until_pong()
        assert check(hub)[0] == 200
        monkeypatch.setattr(health, 'BRAIN_SILENT_S', -1.0)  # its last ping is now too old
        status, body = check(hub)
    assert status == 503
    assert body['brains'][0]['reason'].startswith('The brain has sent nothing for over')


@pytest.mark.parametrize(
    ('setup', 'reason'),
    [
        (lambda r: setattr(r, 'down', True), 'The runner is not answering on its socket.'),
        (lambda r: setattr(r, 'answer', {'ok': False}), 'The runner says it is not ok.'),
        (lambda r: setattr(r, 'delay', 0.3), 'No answer in 0.1 s.'),
    ],
)
def test_a_runner_problem_is_down(
    hub: ChannelHub,
    runner: FakeRunnerHealth,
    monkeypatch: pytest.MonkeyPatch,
    setup: Any,
    reason: str,
) -> None:
    monkeypatch.setattr(health, 'RUNNER_TIMEOUT_S', 0.1)
    setup(runner)
    sid = hub.brain()
    with hub.connect() as channel:
        channel.hello(sid)
        channel.until_pong()
        status, body = check(hub)
    assert status == 503
    assert body['runner']['state'] == 'down' and body['runner']['reason'] == reason
    assert body['brains'][0]['state'] == 'ok', 'each link is told apart'


def test_a_fresh_install_without_an_agent_is_not_a_failure(tmp_path: Path) -> None:
    # install.sh runs health.sh before the owner creates the agent (docs/operating.md).
    from fastapi.testclient import TestClient

    from marcel_hub.app import Settings, create_app

    (tmp_path / 'hub-secret').write_text(SECRET + '\n')
    app = create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db'))
    app.state.runner_client = FakeRunnerHealth()
    with TestClient(app) as client:
        res = client.get('/internal/health', headers={'X-Marcel-Secret': SECRET})
    assert res.status_code == 200
    assert res.json()['brains'] == [] and res.json()['note'].startswith('No agent yet')


def test_no_runner_set_up(hub: ChannelHub) -> None:
    hub.app.state.runner_client = None
    status, body = check(hub)
    assert status == 503
    assert body['runner'] == {'state': 'down', 'reason': 'The hub has no runner set up.'}


def test_only_with_the_hub_secret(hub: ChannelHub) -> None:
    # Control: without the secret check this goes red (and tests/test_auth_routes.py too).
    for secret in ('', 'hs_' + 'f' * 40):
        status, body = check(hub, **{'X-Marcel-Secret': secret})
        assert status == 401 and body['code'] == 'bad_secret'
    (hub.tmp_path / 'hub-secret').unlink()
    assert check(hub)[0] == 401, 'no secret on the hub: refused, never open'


def test_never_through_the_tunnel(hub: ChannelHub) -> None:
    status, body = check(hub, **{'cf-connecting-ip': '203.0.113.9'})
    assert status == 404 and body['code'] == 'not_found'


def test_a_broken_database_is_down(hub: ChannelHub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, '_db', lambda request: 'down')
    sid = hub.brain()
    with hub.connect() as channel:
        channel.hello(sid)
        channel.until_pong()
        status, body = check(hub)
    assert status == 503 and body['hub'] == 'down'


# Claude version drift (S-03.10's pin, runner-api 0.1.10) ---------------------------------------

UP = {'running': True, 'own_unit': True}


def drifted(**over: Any) -> dict[str, Any]:
    return {'ok': False, 'version': '0.1.10', 'claude_version': '2.1.301 (Claude Code)',
            'claude_version_expected': '2.1.296', 'supervisor': UP, 'cap': 4, 'active_workers': 0,
            **over}  # fmt: skip


def connected_check(hub: ChannelHub) -> tuple[int, dict[str, Any]]:
    sid = hub.brain()
    with hub.connect() as channel:
        channel.hello(sid)
        channel.until_pong()
        return check(hub)


def test_version_drift_is_its_own_state_with_the_runners_words(
    hub: ChannelHub, runner: FakeRunnerHealth
) -> None:
    runner.answer = drifted(reason='Claude Code 2.1.301 is installed, but Marcel needs 2.1.296.')
    status, body = connected_check(hub)
    assert status == 503 and body['ok'] is False
    assert body['runner']['state'] == 'version_drift'
    assert body['runner']['reason'] == 'Claude Code 2.1.301 is installed, but Marcel needs 2.1.296.'
    assert body['runner']['claude_version'] == '2.1.301 (Claude Code)'
    assert body['runner']['claude_version_expected'] == '2.1.296'


def test_drift_without_the_runners_reason_says_what_to_run(
    hub: ChannelHub, runner: FakeRunnerHealth
) -> None:
    runner.answer = drifted()
    _, body = connected_check(hub)
    assert body['runner']['reason'] == (
        'Claude Code 2.1.301 (Claude Code) is installed, but Marcel is tested with 2.1.296. '
        'Run deploy/install-claude.sh.'
    )


@pytest.mark.parametrize(
    'answer',
    [
        drifted(supervisor={'running': False, 'own_unit': True}),  # drift, and no supervisor
        drifted(claude_version=''),  # the binary is missing: not a drift
        drifted(claude_version='2.1.296 (Claude Code)', reason='Spawns are failing.'),  # pinned
        {'ok': False, 'claude_version': '2.1.296', 'supervisor': UP},  # no pin known
    ],
)
def test_anything_else_not_ok_is_down_with_the_runners_reason(
    hub: ChannelHub, runner: FakeRunnerHealth, answer: dict[str, Any]
) -> None:
    runner.answer = answer
    _, body = connected_check(hub)
    assert body['runner']['state'] == 'down'
    assert body['runner']['reason'] == answer.get('reason', 'The runner says it is not ok.')


def test_an_ok_runner_shows_its_versions(hub: ChannelHub, runner: FakeRunnerHealth) -> None:
    runner.answer = {'ok': True, 'claude_version': '2.1.296 (Claude Code)',
                     'claude_version_expected': '2.1.296'}  # fmt: skip
    status, body = connected_check(hub)
    assert status == 200
    assert body['runner']['claude_version_expected'] == '2.1.296'


LOST = {'state': 'lost', 'since': '2026-10-13T06:12:00Z',
        'message': 'Claude Code on the NUC is logged out. Log in again: on the NUC run '
                   '`claude auth login`.'}  # fmt: skip


def test_a_lost_login_is_shown_but_never_a_cause(hub: ChannelHub, runner: FakeRunnerHealth) -> None:
    runner.answer = {'ok': True, 'login': LOST}
    status, body = connected_check(hub)
    assert status == 200 and body['ok'] is True, 'a redeploy is never rolled back over a login'
    assert body['runner']['state'] == 'ok'
    assert body['runner']['login'] == LOST


@pytest.mark.parametrize('answer', [{'ok': True}, {'ok': True, 'login': {'state': 'ok'}}])
def test_a_good_or_missing_login_is_left_out(
    hub: ChannelHub, runner: FakeRunnerHealth, answer: dict[str, Any]
) -> None:
    runner.answer = answer
    _, body = connected_check(hub)
    assert 'login' not in body['runner']


def test_an_archived_agent_is_not_counted(hub: ChannelHub) -> None:
    """A removed person's agent is archived and has no brain (S-17.7): health must not call it
    down. Drop the archived filter in `_brains` and this goes red."""
    from datetime import UTC, datetime

    from sqlmodel import Session

    from marcel_hub.models import Agent

    sid = hub.brain()
    with Session(hub.engine) as s:
        s.add(Agent(owner_user_id='usr_emma', name='Pip', animal='rabbit', palette='natural',
                    seed='Pip', slug='pip', archived_at=datetime.now(UTC)))  # fmt: skip
        s.commit()
    with hub.connect() as channel:
        channel.hello(sid)
        channel.until_pong()
        status, body = check(hub)
    assert status == 200, body
    assert [b['agent_id'] for b in body['brains']] == [hub.agent_id]
