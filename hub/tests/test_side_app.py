"""Side threads in the real app (S-06.3): a fake runner, the real `/channel` socket and the phone on
`/api`. Covers the story's two "done when" tests:

- a message in the side conversation never reaches the main brain;
- "send to main" does reach it.

Nothing here reaches a real `claude`, a runner or the network."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from test_brain_app import current, wait
from test_channel_fakes import SECRET, ChannelHub
from test_side import FakeSides

from marcel_hub import brain, side
from marcel_hub.app import Settings, create_app
from marcel_hub.channel.outbox import conversation_target
from marcel_hub.models import Conversation, ConversationKind, MessageKind

CWD = '/home/owner/marcel/brain'


@pytest.fixture(autouse=True)
def _fast_ticks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brain, 'TICK_S', 0.05)
    monkeypatch.setattr(side, 'TICK_S', 0.05)


@pytest.fixture
def fake() -> FakeSides:
    return FakeSides()


@pytest.fixture
def hub(tmp_path: Path, fake: FakeSides) -> Iterator[ChannelHub]:
    (tmp_path / 'hub-secret').write_text(SECRET + '\n')
    app = create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db', brain_cwd=CWD))
    app.state.brain.runner = fake  # the app has no runner socket in tests
    app.state.sides.runner = fake
    with TestClient(app) as client:
        yield ChannelHub(client, tmp_path)


def say(hub: ChannelHub, conversation_id: str, text: str) -> str:
    res = hub.client.post(f'/api/conversations/{conversation_id}/messages', json={'text': text})
    assert res.status_code == 201, res.text
    return res.json()['id']


def side_session(hub: ChannelHub, conversation_id: str) -> str | None:
    with Session(hub.engine) as s:
        conv = s.get(Conversation, conversation_id)
        assert conv is not None
        return conv.brain_session_id


def open_thread(hub: ChannelHub, message_id: str) -> str:
    res = hub.client.post(f'/api/messages/{message_id}/thread')
    assert res.status_code == 201, res.text
    return res.json()['id']


def test_a_message_in_a_side_thread_never_reaches_the_main_brain(
    hub: ChannelHub, fake: FakeSides
) -> None:
    main_sid = wait(lambda: current(hub))
    with hub.connect() as main_ch:
        assert main_ch.hello(main_sid)['role'] == 'brain'
        asked = say(hub, hub.main_id, 'What should we cook tonight?')
        assert main_ch.deliver()['text'] == 'What should we cook tonight?'
        side_id = open_thread(hub, asked)
        fork = wait(lambda: side_session(hub, side_id))
        assert fake.forked[0]['from'] == main_sid
        assert current(hub) == main_sid, 'the fork replaced the main brain'
        with hub.connect() as side_ch:
            welcome = side_ch.hello(fork)
            assert welcome['role'] == 'side' and welcome['conversation_id'] == side_id
            say(hub, side_id, 'Something with leeks, only for me')
            frame = side_ch.deliver()
            assert frame['type'] == 'user_message'
            assert frame['text'] == 'Something with leeks, only for me'
            assert frame['conversation_id'] == side_id
            # The main brain got nothing, and nothing waits for it.
            assert main_ch.until_pong() == []
            assert hub.gateway.outbox.pending(conversation_target(hub.main_id)) == []
            # The fork's reply lands in the side thread only.
            assert side_ch.call('reply', text='Leek and potato soup.')['type'] == 'call_ack'
    assert [m.body_json.get('text') for m in hub.messages()] == ['What should we cook tonight?']
    side_texts = [m.body_json.get('text') for m in hub.messages(side_id)]
    assert side_texts == ['Something with leeks, only for me', 'Leek and potato soup.']


def test_send_to_main_reaches_the_main_brain(hub: ChannelHub, fake: FakeSides) -> None:
    main_sid = wait(lambda: current(hub))
    asked = say(hub, hub.main_id, 'Plan the weekend')
    side_id = open_thread(hub, asked)
    fork = wait(lambda: side_session(hub, side_id))
    with hub.connect() as side_ch:
        side_ch.hello(fork)
        side_ch.call('reply', text='Saturday: the zoo. Sunday: grandma.')
    answer = next(m for m in hub.messages(side_id) if m.body_json.get('text', '').startswith('Sat'))
    with hub.connect() as main_ch:
        assert main_ch.hello(main_sid)['role'] == 'brain'
        assert main_ch.deliver()['text'] == 'Plan the weekend'
        res = hub.client.post(
            f'/api/conversations/{side_id}/send-to-main', json={'message_id': answer.id}
        )
        assert res.status_code == 201, res.text
        frame = main_ch.deliver()
        assert frame['type'] == 'user_message'
        assert frame['conversation_id'] == hub.main_id
        assert frame['text'] == 'Saturday: the zoo. Sunday: grandma.'
        assert frame['quoted_message_id'] == answer.id
        res = hub.client.post(
            f'/api/conversations/{side_id}/send-to-main', json={'text': 'Book the zoo'}
        )
        assert res.status_code == 201
        assert main_ch.deliver()['text'] == 'Book the zoo'
    kinds = [m.kind for m in hub.messages()]
    assert kinds == [MessageKind.text, MessageKind.quote, MessageKind.text]


def test_a_message_written_before_the_fork_runs_waits_for_it(
    hub: ChannelHub, fake: FakeSides, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(side, 'BACKOFF_S', (0.1, 0.1))
    wait(lambda: current(hub))
    fake.fail_forks = 1  # the first fork fails; the thread says so and tries again
    side_id = open_thread(hub, say(hub, hub.main_id, 'Holiday ideas'))
    say(hub, side_id, 'Somewhere warm in May')
    waiting = hub.gateway.outbox.pending(conversation_target(side_id))
    assert [f['text'] for f in waiting] == ['Somewhere warm in May'], 'the message was dropped'
    fork = wait(lambda: side_session(hub, side_id))
    notes = [m.body_json['text'] for m in hub.messages(side_id) if m.role.value == 'system']
    assert notes[0].startswith('Marcel could not open this side thread yet')
    with hub.connect() as side_ch:
        side_ch.hello(fork)
        frame = side_ch.deliver()
        assert frame['text'] == 'Somewhere warm in May'


def test_a_task_thread_still_goes_to_its_task(hub: ChannelHub, fake: FakeSides) -> None:
    wait(lambda: current(hub))
    task_id, _ = hub.task()
    thread_id, _ = hub.side(task_id=task_id)
    say(hub, thread_id, 'Use df')
    hub.app.state.sides.want(thread_id)
    wait(lambda: not hub.app.state.sides.wanted())
    assert fake.forked == []
    with Session(hub.engine) as s:
        conv = s.get(Conversation, thread_id)
        assert conv is not None and conv.kind == ConversationKind.side
