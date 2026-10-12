"""The brain keeper in the real app (S-06.2): a fake runner, the real `/channel` socket and the
phone on `/api`. Covers the story's two "done when" tests:

- kill the brain → a new one within 10 s, and messages sent during the gap are delivered;
- a rollover never leaves zero brains, and never two answering at once.

Nothing here reaches a real `claude`, a runner or the network."""

import asyncio
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from starlette.websockets import WebSocketDisconnect
from test_brain import FakeBrains
from test_channel_fakes import SECRET, ChannelHub

from marcel_hub import brain
from marcel_hub.app import Settings, create_app, load_settings
from marcel_hub.brain import BrainKeeper
from marcel_hub.channel.gateway import Peer
from marcel_hub.channel.outbox import conversation_target
from marcel_hub.channel.socket import REPLACED
from marcel_hub.models import Conversation

CWD = '/home/owner/marcel/brain'


@contextmanager
def brain_hub(tmp_path: Path, fake: FakeBrains) -> Iterator[ChannelHub]:
    (tmp_path / 'hub-secret').write_text(SECRET + '\n')
    app = create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db', brain_cwd=CWD))
    keeper: BrainKeeper = app.state.brain
    keeper.runner = fake  # the app has no runner socket in tests
    # Even while the usage guard holds new work, the brain starts: it must always answer.
    app.state.tasks.hold_new_work = lambda: 'usage'
    with TestClient(app) as client:
        yield ChannelHub(client, tmp_path)


@pytest.fixture(autouse=True)
def _fast_ticks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brain, 'TICK_S', 0.05)


@pytest.fixture
def fake() -> FakeBrains:
    return FakeBrains()


@pytest.fixture
def hub(tmp_path: Path, fake: FakeBrains) -> Iterator[ChannelHub]:
    with brain_hub(tmp_path, fake) as hub:
        yield hub


def wait(condition: Callable[[], Any], timeout: float = 10.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError('Timed out waiting for the condition.')


def current(hub: ChannelHub) -> str | None:
    with Session(hub.engine) as s:
        conv = s.get(Conversation, hub.main_id)
        assert conv is not None
        return conv.brain_session_id


def keeper_of(hub: ChannelHub) -> BrainKeeper:
    return hub.app.state.brain


def say(hub: ChannelHub, text: str) -> None:
    res = hub.client.post(f'/api/conversations/{hub.main_id}/messages', json={'text': text})
    assert res.status_code == 201, res.text


def answering(hub: ChannelHub) -> str | None:
    """The session the main conversation's frames go to right now, if one is connected."""
    link = hub.gateway.links.get(conversation_target(hub.main_id))
    return link.peer.session_id if link is not None else None


def test_brain_cwd_is_a_setting(tmp_path: Path) -> None:
    env = {'MARCEL_CONFIG': str(tmp_path / 'none.toml'), 'MARCEL_BRAIN_CWD': CWD}
    assert load_settings(env).brain_cwd == CWD
    assert load_settings({'MARCEL_CONFIG': str(tmp_path / 'none.toml')}).brain_cwd is None
    app = create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db'))
    assert app.state.brain is None  # no workspace: no keeper


def test_a_killed_brain_is_back_within_ten_seconds_with_the_messages_of_the_gap(
    hub: ChannelHub, fake: FakeBrains
) -> None:
    first = wait(lambda: current(hub))
    assert fake.spawned[0]['cwd'] == CWD, 'the brain started although the usage guard holds'
    with hub.connect() as ch:
        assert ch.hello(first)['role'] == 'brain'
        say(hub, 'Hello Marcel')
        assert ch.deliver()['text'] == 'Hello Marcel'
    # The brain dies: its socket goes, and the runner reports it failed.
    fake.die(first)
    killed = time.monotonic()
    say(hub, 'Are you there?')  # sent during the gap
    second = wait(lambda: (sid := current(hub)) != first and sid)
    assert time.monotonic() - killed < 10
    with hub.connect() as ch:
        assert ch.hello(second)['role'] == 'brain'
        frame = ch.deliver()
        assert frame['type'] == 'user_message' and frame['text'] == 'Are you there?'
    assert [e['data']['reason'] for e in hub.events('brain.rollover')] == ['crashed']


def test_a_rollover_never_leaves_zero_brains_and_never_two_answering(
    hub: ChannelHub, fake: FakeBrains
) -> None:
    old = wait(lambda: current(hub))
    with hub.connect() as old_ch:
        assert old_ch.hello(old)['role'] == 'brain'
        keeper_of(hub).roll_over(hub.agent_id)
        wait(lambda: len(fake.spawned) == 2)
        new = next(sid for sid in fake.by_id if sid != old)
        # Started, not handed over: the old brain still gets every message.
        assert current(hub) == old and answering(hub) == old
        say(hub, 'Still you?')
        assert old_ch.deliver()['text'] == 'Still you?'
        # The old brain is in a turn: the new one's hello is refused for now (the channel
        # server says hello again in 5 s).
        fake.busy(old)
        wait(lambda: keeper_of(hub)._seen[old]['state'] == 'working')
        with hub.connect() as new_ch:
            refused = new_ch.hello(new)
        assert refused['type'] == 'rejected' and refused['reason'] == 'unknown_session'
        assert current(hub) == old and answering(hub) == old
        # Between turns: the hello is the hand-over.
        fake.busy(old, False)
        wait(lambda: keeper_of(hub)._seen[old]['state'] == 'blocked')
        with hub.connect() as new_ch:
            assert new_ch.hello(new) == {
                'type': 'welcome',
                'role': 'brain',
                'agent_id': hub.agent_id,
                'conversation_id': hub.main_id,
            }
            assert current(hub) == new and answering(hub) == new
            # The old brain's socket is closed: it gets no more frames.
            with pytest.raises(WebSocketDisconnect) as closed:
                old_ch.next()
            assert closed.value.code == REPLACED
            say(hub, 'And now?')
            assert new_ch.deliver()['text'] == 'And now?'
            # A late reply from the old session is refused, not shown: one brain answers.
            ack = asyncio.run(
                hub.gateway.handle(
                    Peer(old, 'brain', hub.agent_id, conversation_id=hub.main_id),
                    {'type': 'reply', 'call_id': 'c_late', 'text': 'I am the old one'},
                )
            )
            assert ack[0]['error']['code'] == 'replaced'
            assert all(m.body_json.get('text') != 'I am the old one' for m in hub.messages())
            reply = new_ch.call('reply', text='Fresh and here.')
            assert reply == {'type': 'call_ack', 'call_id': 'c_0001'}
            wait(lambda: old in fake.stopped)
    assert fake.stopped == [old]
    rollovers = hub.events('brain.rollover')
    assert [e['data']['reason'] for e in rollovers] == ['manual']
    assert rollovers[0]['data']['kept'].startswith('The last ')
