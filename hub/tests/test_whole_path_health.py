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
        (lambda r: setattr(r, 'delay', 0.3), 'No answer in 0 s.'),
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
