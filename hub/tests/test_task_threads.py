"""Task threads (S-04.9, B-08, B-11): a thread opened on a task's milestone or approval steers
that task, never the brain, and shows the task's later milestones and approval cards.

The real app runs underneath (registry, channel gateway, event log, avatar); a fake channel server
plays the worker. Every message the hub sends is checked against the contract.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session
from test_channel_fakes import MESSAGE, ChannelHub, channel_hub, permission

from marcel_hub.models import Conversation, Message, MessageKind, MessageRole, TaskState
from marcel_hub.task_threads import MIRROR_PREFIX, TaskThreads


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[ChannelHub]:
    with channel_hub(tmp_path) as found:
        yield found


def threads(hub: ChannelHub) -> TaskThreads:
    return hub.app.state.task_threads


def thread_on_milestone(hub: ChannelHub, task_id: str, event: str = 'started') -> str:
    """Open the side thread on the task's milestone in the main chat, as the phone does."""
    hub.gateway.milestone(task_id, event)
    [milestone] = [
        m for m in hub.messages() if m.task_id == task_id and m.kind.value == 'milestone'
    ]
    res = hub.client.post(f'/api/messages/{milestone.id}/thread')
    assert res.status_code == 201, res.text
    assert res.json()['task_id'] == task_id
    return res.json()['id']


def post(hub: ChannelHub, thread_id: str, text: str, **kw: Any) -> Any:
    return hub.client.post(f'/api/conversations/{thread_id}/messages', json={'text': text, **kw})


def copies(hub: ChannelHub, thread_id: str, kind: MessageKind) -> list[Message]:
    return [m for m in hub.messages(thread_id) if m.kind == kind]


# Posting in a task's thread ---------------------------------------------------------------------


def test_a_thread_message_reaches_the_tasks_outbox_and_never_the_brains(hub: ChannelHub) -> None:
    """Control: a task thread never starts a brain turn. The thread and the main chat both have a
    session, so a message routed to the brain would be queued there; it must go to the task."""
    task_id, worker = hub.task()
    thread_id = thread_on_milestone(hub, task_id)
    hub.brain()
    hub.side(task_id=None)  # an ordinary side thread with its own session, left alone too
    with Session(hub.engine) as s:  # give the thread a session, so routing it would show
        conv = s.get(Conversation, thread_id)
        assert conv is not None
        conv.brain_session_id = 'sess-thread'
        s.add(conv)
        s.commit()

    res = post(hub, thread_id, 'Skip the Plex cache.', client_id='c1')
    assert res.status_code == 201, res.text
    MESSAGE.validate(res.json())
    assert res.json()['conversation_id'] == thread_id
    assert res.json()['role'] == 'user' and res.json()['task_id'] == task_id

    # The task got it: a note, and a steer frame for its session.
    [note] = [e.data_json for e in hub.task_events(task_id, 'note')]
    assert note['text'] == 'Skip the Plex cache.' and note['author'] == 'user'
    with hub.connect() as ch:
        ch.hello(worker)
        steer = ch.deliver()
    assert steer['type'] == 'steer' and steer['text'] == 'Skip the Plex cache.'
    # The brain got nothing: not the main chat's session, not the thread's.
    assert hub.gateway.outbox.pending(f'conversation:{hub.main_id}') == []
    assert hub.gateway.outbox.pending(f'conversation:{thread_id}') == []


def test_a_thread_message_does_not_make_the_avatar_think(hub: ChannelHub) -> None:
    task_id, _ = hub.task()
    thread_id = thread_on_milestone(hub, task_id)
    assert post(hub, thread_id, 'Also check /var.').status_code == 201
    assert hub.events('agent.state') == []


def test_a_retried_thread_message_steers_once_and_stores_once(hub: ChannelHub) -> None:
    task_id, _ = hub.task()
    thread_id = thread_on_milestone(hub, task_id)
    first = post(hub, thread_id, 'Skip the cache.', client_id='c1').json()
    second = post(hub, thread_id, 'Skip the cache.', client_id='c1').json()
    assert first == second
    assert len(hub.task_events(task_id, 'note')) == 1
    assert len(copies(hub, thread_id, MessageKind.text)) == 1


@pytest.mark.parametrize('state', [TaskState.stopped, TaskState.failed])
def test_posting_to_the_thread_of_a_finished_task_is_refused(
    hub: ChannelHub, state: TaskState
) -> None:
    """Control: a stopped or failed task takes no message, and nothing is stored."""
    task_id, _ = hub.task(state)
    thread_id = thread_on_milestone(hub, task_id)
    res = post(hub, thread_id, 'Are you still there?')
    assert res.status_code == 409
    body = res.json()
    assert body['code'] == 'task_finished'
    assert body['message'] == (
        f'This task has {state.value}, so it cannot take messages. Ask Marcel to retry it instead.'
    )
    assert hub.task_events(task_id, 'note') == []
    assert copies(hub, thread_id, MessageKind.text) == []
    assert hub.gateway.outbox.pending(f'task:{task_id}') == []


def test_a_done_task_takes_a_thread_message_as_a_follow_up(hub: ChannelHub) -> None:
    task_id, _ = hub.task(TaskState.done)
    thread_id = thread_on_milestone(hub, task_id, 'done')
    assert post(hub, thread_id, 'Now clean it up.').status_code == 201
    assert hub.registry.get(task_id).state == TaskState.working


def test_a_thread_whose_task_is_gone_says_so(hub: ChannelHub) -> None:
    thread_id, _ = hub.side(task_id='tsk_gone')
    res = post(hub, thread_id, 'Hello?')
    assert res.status_code == 404
    assert res.json() == {
        'code': 'task_not_found',
        'message': 'The task this thread belongs to is not on this hub.',
    }


def test_a_plain_side_thread_still_goes_to_its_session(hub: ChannelHub) -> None:
    side_id, _ = hub.side()
    res = post(hub, side_id, 'Which ORM?')
    assert res.status_code == 201
    [frame] = hub.gateway.outbox.pending(f'conversation:{side_id}')
    assert frame['type'] == 'user_message' and frame['text'] == 'Which ORM?'


# Mirroring milestones and approval cards ----------------------------------------------------


def test_a_done_milestone_shows_in_the_main_chat_and_the_task_thread(hub: ChannelHub) -> None:
    task_id, worker = hub.task()
    thread_id = thread_on_milestone(hub, task_id)
    with hub.connect() as ch:
        ch.hello(worker)
        assert ch.call('report', kind='done', summary='12 GB free.')['type'] == 'call_ack'

    def done(conversation_id: str) -> list[dict[str, Any]]:
        return [
            m.body_json['milestone']
            for m in hub.messages(conversation_id)
            if m.kind == MessageKind.milestone and m.body_json['milestone']['event'] == 'done'
        ]

    assert done(hub.main_id) == done(thread_id)
    [milestone] = done(thread_id)
    assert milestone['summary'] == '12 GB free.' and milestone['task_id'] == task_id
    created = [e['data'] for e in hub.events('message.created')]
    for data in created:
        MESSAGE.validate(data)
    in_thread = [d for d in created if d['conversation_id'] == thread_id]
    assert [d['kind'] for d in in_thread] == ['milestone']
    assert in_thread[0]['role'] == 'agent' and in_thread[0]['task_id'] == task_id


def test_an_approval_card_shows_in_the_thread_once_and_replays_once(hub: ChannelHub) -> None:
    """Control: one copy per original. Mirroring the same card again, or a phone reconnecting
    with `?since=`, never shows it twice."""
    task_id, worker = hub.task()
    thread_id = thread_on_milestone(hub, task_id)
    with hub.connect() as ch:
        ch.hello(worker)
        ch.send(**permission())
        assert ch.until_pong() == []
    [card] = copies(hub, thread_id, MessageKind.approval)
    [original] = [m for m in hub.messages() if m.kind == MessageKind.approval]
    assert card.body_json == original.body_json and card.task_id == task_id
    assert card.client_id == MIRROR_PREFIX + original.id

    again = threads(hub).mirror(original.id)
    assert [m.id for m in again] == [card.id]
    assert len(copies(hub, thread_id, MessageKind.approval)) == 1

    # A phone that reconnects from the start gets the card in the thread once.
    headers = {'Authorization': f'Bearer {hub.token}'}
    with hub.client.websocket_connect('/api/ws?since=0', headers=headers) as ws:
        hello = ws.receive_json()
        frames = [ws.receive_json() for _ in range(hello['data']['replayed'])]
    replayed = [
        f['data']
        for f in frames
        if f['type'] == 'message.created'
        and f['data']['conversation_id'] == thread_id
        and f['data']['kind'] == 'approval'
    ]
    assert len(replayed) == 1


def test_a_task_with_two_threads_gets_one_copy_in_each(hub: ChannelHub) -> None:
    task_id, _ = hub.task()
    first = thread_on_milestone(hub, task_id)
    second, _ = hub.side(task_id=task_id)
    hub.gateway.milestone(task_id, 'done', summary='Finished.')
    for thread_id in (first, second):
        assert len(copies(hub, thread_id, MessageKind.milestone)) == 1


def test_only_milestones_and_approvals_of_the_threads_own_task_are_mirrored(
    hub: ChannelHub,
) -> None:
    task_id, _ = hub.task()
    other, _ = hub.task()
    thread_id = thread_on_milestone(hub, task_id)
    hub.gateway.milestone(other, 'done')
    assert threads(hub).mirror('msg_nope') == []
    plain = hub.client.post(f'/api/conversations/{hub.main_id}/messages', json={'text': 'Hi'})
    assert threads(hub).mirror(plain.json()['id']) == []
    assert copies(hub, thread_id, MessageKind.milestone) == []
    assert [m.role for m in hub.messages(thread_id)] == []


def test_a_copy_in_a_thread_is_never_copied_again(hub: ChannelHub) -> None:
    """Control: only originals from the main chat are mirrored. Without it each copy would be
    copied into the thread again, without end. The observer is taken off the log here so that,
    with the control removed, this test fails at once instead of recursing."""
    hub.app.state.events.observers.remove(threads(hub).observe)
    task_id, _ = hub.task()
    first = thread_on_milestone(hub, task_id)
    second, _ = hub.side(task_id=task_id)
    hub.gateway.milestone(task_id, 'done')
    [original] = [
        m for m in hub.messages() if m.body_json.get('milestone', {}).get('event') == 'done'
    ]
    made = threads(hub).mirror(original.id)
    assert sorted(m.conversation_id for m in made) == sorted([first, second])
    for copy in made:
        assert copy.role == MessageRole.agent
        assert threads(hub).mirror(copy.id) == []
    for thread_id in (first, second):
        assert len(copies(hub, thread_id, MessageKind.milestone)) == 1


def test_a_failed_copy_never_loses_the_original(
    hub: ChannelHub, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    task_id, _ = hub.task()
    thread_id = thread_on_milestone(hub, task_id)

    def broken(_message_id: str) -> list[Message]:
        raise RuntimeError('disk full')

    monkeypatch.setattr(threads(hub), 'mirror', broken)
    hub.gateway.milestone(task_id, 'done')
    assert [
        m.body_json['milestone']['event'] for m in hub.messages() if m.kind.value == 'milestone'
    ] == [
        'started',
        'done',
    ]
    assert copies(hub, thread_id, MessageKind.milestone) == []
    assert 'Could not copy message' in caplog.text


def test_a_mirrored_milestone_does_not_end_the_brains_turn(hub: ChannelHub) -> None:
    """S-04.8: only the brain's own reply ends its turn; a task's milestone in the main chat, or
    its copy in the task's thread, leaves the avatar thinking."""
    task_id, _ = hub.task(TaskState.done)  # a working task would show working
    thread_id = thread_on_milestone(hub, task_id)
    res = hub.client.post(f'/api/conversations/{hub.main_id}/messages', json={'text': 'Status?'})
    assert res.status_code == 201
    hub.gateway.milestone(task_id, 'done', summary='12 GB free.')
    assert len(copies(hub, thread_id, MessageKind.milestone)) == 1
    states = [e['data']['state'] for e in hub.events('agent.state')]
    assert states == [{'kind': 'thinking'}]
