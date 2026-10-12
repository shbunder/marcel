"""The channel gateway through the real app: a fake channel server on `/channel`, the phone on
`/api`. Every frame the hub sends is checked against channel.schema.json."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, select
from test_channel_fakes import (
    MESSAGE,
    SECRET,
    ChannelHub,
    channel_hub,
    permission,
    session_id,
)

from marcel_hub.models import Artifact, ChannelCall, TaskOrigin, TaskState


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[ChannelHub]:
    with channel_hub(tmp_path) as hub:
        yield hub


# Hello --------------------------------------------------------------------------------------------


def test_a_worker_is_welcomed_with_its_task(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    with hub.connect() as ch:
        assert ch.hello(sid) == {
            'type': 'welcome',
            'role': 'worker',
            'agent_id': hub.agent_id,
            'task_id': task_id,
        }


def test_an_adopted_session_is_welcomed_as_adopted(hub: ChannelHub) -> None:
    task_id, sid = hub.task(origin=TaskOrigin.adopted)
    with hub.connect() as ch:
        assert ch.hello(sid)['role'] == 'adopted'


def test_brain_and_side_are_welcomed_with_their_conversation(hub: ChannelHub) -> None:
    brain = hub.brain()
    side_id, side = hub.side()
    with hub.connect() as ch:
        assert ch.hello(brain) == {
            'type': 'welcome',
            'role': 'brain',
            'agent_id': hub.agent_id,
            'conversation_id': hub.main_id,
        }
    with hub.connect() as ch:
        assert ch.hello(side)['conversation_id'] == side_id
        assert ch.welcome is not None and ch.welcome['role'] == 'side'


def test_an_unknown_session_is_rejected(hub: ChannelHub) -> None:
    hub.task()
    with hub.connect() as ch:
        frame = ch.hello(session_id())
    assert frame['type'] == 'rejected' and frame['reason'] == 'unknown_session'


def test_a_wrong_secret_is_rejected_and_raises_one_alert(
    hub: ChannelHub, caplog: pytest.LogCaptureFixture
) -> None:
    _, sid = hub.task()
    for _ in range(2):
        with hub.connect() as ch:
            frame = ch.hello(sid, secret='hs_' + 'f' * 40)
        assert frame == {
            'type': 'rejected',
            'reason': 'bad_secret',
            'message': 'The secret does not match this hub.',
        }
    assert hub.gateway.alerts.open.keys() == {'channel_bad_secret'}  # type: ignore[attr-defined]
    assert caplog.text.count('Something broke') == 1


def test_a_hub_without_a_secret_rejects_every_session(tmp_path: Path) -> None:
    with channel_hub(tmp_path, secret=None) as hub:
        _, sid = hub.task()
        with hub.connect() as ch:
            assert ch.hello(sid)['reason'] == 'bad_secret'
        # A secret that is too short is no secret either.
        (tmp_path / 'hub-secret').write_text('short')
        with hub.connect() as ch:
            assert ch.hello(sid, secret='short' + 'x' * 30)['reason'] == 'bad_secret'


@pytest.mark.parametrize('version', ['0.0.9', 'dev'])
def test_an_old_plugin_is_rejected(hub: ChannelHub, version: str) -> None:
    _, sid = hub.task()
    with hub.connect() as ch:
        assert ch.hello(sid, version=version)['reason'] == 'plugin_too_old'


def test_a_newer_plugin_is_welcomed(hub: ChannelHub) -> None:
    _, sid = hub.task()
    with hub.connect() as ch:
        assert ch.hello(sid, version='0.2.0-dev')['type'] == 'welcome'


def test_a_socket_that_starts_with_anything_but_hello_is_closed(hub: ChannelHub) -> None:
    from starlette.websockets import WebSocketDisconnect

    with hub.connect() as ch:
        ch.send(type='ping')
        with pytest.raises(WebSocketDisconnect) as closed:
            ch.ws.receive_json()
    assert closed.value.code == 1008


def test_a_socket_that_never_says_hello_is_closed(
    hub: ChannelHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    from starlette.websockets import WebSocketDisconnect

    from marcel_hub.channel import socket

    monkeypatch.setattr(socket, 'HELLO_TIMEOUT', 0.05)
    with hub.connect() as ch, pytest.raises(WebSocketDisconnect) as closed:
        ch.ws.receive_json()
    assert closed.value.code == 1008


def test_an_oversized_frame_closes_the_socket(hub: ChannelHub) -> None:
    from starlette.websockets import WebSocketDisconnect

    from marcel_hub.channel.socket import MAX_FRAME

    task_id, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        ch.send(type='report', call_id='c_big', kind='progress', text='x' * MAX_FRAME)
        with pytest.raises(WebSocketDisconnect) as closed:
            ch.ws.receive_json()
    assert closed.value.code == 1009
    assert hub.task_events(task_id, 'progress') == []


def test_an_oversized_hello_closes_the_socket(hub: ChannelHub) -> None:
    from starlette.websockets import WebSocketDisconnect

    from marcel_hub.channel.socket import MAX_FRAME

    with hub.connect() as ch:
        ch.ws.send_text('{"type": "hello", "pad": "' + 'x' * MAX_FRAME + '"}')
        with pytest.raises(WebSocketDisconnect) as closed:
            ch.ws.receive_json()
    assert closed.value.code == 1009


def test_a_second_socket_for_a_session_replaces_the_first(hub: ChannelHub) -> None:
    from starlette.websockets import WebSocketDisconnect

    task_id, sid = hub.task()
    with hub.connect() as first:
        first.hello(sid)
        with hub.connect() as second:
            second.hello(sid)
            with pytest.raises(WebSocketDisconnect) as closed:
                first.ws.receive_json()
            assert closed.value.code == 4000
            hub.client.post(f'/api/tasks/{task_id}/messages', json={'text': 'Still there?'})
            assert second.deliver()['text'] == 'Still there?'


def test_the_channel_is_outside_api_so_the_tunnel_never_forwards_it(hub: ChannelHub) -> None:
    from fastapi.routing import iter_route_contexts

    paths = {
        str(r.path or getattr(r.original_route, 'path', ''))
        for r in iter_route_contexts(hub.app.routes)
    }
    assert '/channel' in paths
    assert not any(p.startswith('/api') and 'channel' in p for p in paths)
    # Everything else the hub serves is under /api, the only prefix the tunnel forwards.
    assert {p for p in paths if not p.startswith('/api/')} == {'/channel'}


# Approvals ----------------------------------------------------------------------------------------


def test_approve_flow_end_to_end(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        ch.send(**permission())
        assert ch.until_pong() == []  # a permission request is not acked

        [card] = hub.approvals(state='open')
        assert card['task_id'] == task_id and card['tool'] == 'Bash'
        assert card['summary'] == 'Run docker system df -v'
        assert card['detail'] == '{ "command": "docker system df -v" }'
        task = hub.registry.get(task_id)
        assert task.state == TaskState.needs_you
        assert task.waiting_for == 'Your approval: Run docker system df -v'
        assert hub.registry.to_api(task)['approval_ids'] == [card['id']]
        assert [e.data_json for e in hub.task_events(task_id, 'permission')] == [
            {'approval_id': card['id'], 'tool': 'Bash', 'summary': 'Run docker system df -v'}
        ]
        # The card shows in the main chat, on the phone, and as a push.
        [created] = hub.events('approval.created')
        assert created['data'] == card
        [message] = hub.messages()
        assert message.body_json == {'approval_id': card['id']} and message.task_id == task_id
        assert hub.gateway.push.sent == [card['id']]  # type: ignore[attr-defined]

        res = hub.answer(card['id'], 'approve')
        assert res.status_code == 200, res.text
        assert res.json()['state'] == 'approved' and res.json()['answered_via'] == 'app'

        decision = ch.deliver()
        assert decision == {
            'type': 'permission_decision',
            'seq': 1,
            'request_id': 'qiwxo',
            'behavior': 'allow',
        }
        [resolved] = hub.events('approval.resolved')
        assert resolved['data']['state'] == 'approved'
        assert hub.registry.get(task_id).state == TaskState.working
        assert hub.approvals(state='open') == []
        ch.until_pong()
    assert hub.gateway.outbox.pending(f'task:{task_id}') == []


def test_a_repeated_permission_request_opens_no_second_card(hub: ChannelHub) -> None:
    _, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        ch.send(**permission())
        ch.send(**permission())
        ch.until_pong()
        [card] = hub.approvals()
        hub.answer(card['id'], 'approve')
        ch.deliver()
    # The server re-sends every prompt after a reconnect; an answered card stays answered.
    with hub.connect() as ch:
        ch.hello(sid, resume_after=1)
        ch.send(**permission())
        ch.until_pong()
    assert [a['state'] for a in hub.approvals()] == ['approved']
    assert len(hub.events('approval.created')) == 1
    assert len(hub.gateway.push.sent) == 1  # type: ignore[attr-defined]


def test_the_same_request_id_on_another_task_is_another_card(hub: ChannelHub) -> None:
    _, first = hub.task()
    _, second = hub.task()
    for sid in (first, second):
        with hub.connect() as ch:
            ch.hello(sid)
            ch.send(**permission())
            ch.until_pong()
    assert len(hub.approvals()) == 2


def test_a_denial_note_follows_the_decision_as_a_steer(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        ch.send(**permission())
        ch.until_pong()
        [card] = hub.approvals()
        res = hub.answer(card['id'], 'deny', note='Use docker volume ls instead.')
        assert res.json()['state'] == 'denied'
        decision, steer = ch.deliver(), ch.deliver()
    assert decision['behavior'] == 'deny' and decision['seq'] == 1
    assert steer['type'] == 'steer' and steer['seq'] == 2
    assert steer['text'] == 'Use docker volume ls instead.' and steer['author'] == 'user'
    assert hub.registry.get(task_id).state == TaskState.working


def test_a_denial_without_a_note_sends_only_the_decision(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        ch.send(**permission())
        ch.until_pong()
        [card] = hub.approvals()
        hub.answer(card['id'], 'deny')
        assert ch.deliver()['behavior'] == 'deny'
    assert hub.gateway.outbox.pending(f'task:{task_id}') == []


def test_a_task_waits_while_another_card_is_open(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        ch.send(**permission('aaaaa'))
        ch.send(**permission('bbbbb', tool_name='Write'))
        ch.until_pong()
        first, second = sorted(hub.approvals(), key=lambda a: a['tool'])
        hub.answer(first['id'], 'approve')
        assert hub.registry.get(task_id).state == TaskState.needs_you
        hub.answer(second['id'], 'approve')
        assert hub.registry.get(task_id).state == TaskState.working


def test_stopping_a_task_expires_its_open_card_and_answering_it_is_409(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        ch.send(**permission())
        ch.until_pong()
    [card] = hub.approvals()
    res = hub.client.post(f'/api/tasks/{task_id}/stop')
    assert res.status_code == 200, res.text
    [expired] = hub.approvals()
    assert expired['state'] == 'expired' and 'answered_via' not in expired
    assert [e['data']['state'] for e in hub.events('approval.resolved')] == ['expired']

    res = hub.answer(card['id'], 'approve')
    assert res.status_code == 409
    assert res.json() == expired
    assert hub.gateway.outbox.pending(f'task:{task_id}') == []  # nothing sent to the session


def test_a_failed_task_expires_its_open_card(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        ch.send(**permission())
        ch.until_pong()
        assert ch.call('report', kind='failed', summary='Docker is not running.')['type'] == (
            'call_ack'
        )
    assert [a['state'] for a in hub.approvals()] == ['expired']


def test_answering_twice_is_409_with_the_first_answer(hub: ChannelHub) -> None:
    _, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        ch.send(**permission())
        ch.until_pong()
        [card] = hub.approvals()
        assert hub.answer(card['id'], 'deny').status_code == 200
        res = hub.answer(card['id'], 'approve')
    assert res.status_code == 409 and res.json()['state'] == 'denied'


def test_only_the_owner_sees_or_answers_a_card(hub: ChannelHub) -> None:
    _, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        ch.send(**permission())
        ch.until_pong()
    [card] = hub.approvals()
    stranger = {'Authorization': f'Bearer {hub.phone("usr_stranger")}'}
    assert hub.client.get('/api/approvals', headers=stranger).json() == []
    res = hub.client.post(
        f'/api/approvals/{card["id"]}', json={'decision': 'approve'}, headers=stranger
    )
    assert res.status_code == 404 and res.json()['code'] == 'approval_not_found'
    assert hub.answer('apr_missing', 'approve').status_code == 404
    assert hub.approvals()[0]['state'] == 'open'


def test_a_bad_answer_is_a_400(hub: ChannelHub) -> None:
    _, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        ch.send(**permission())
        ch.until_pong()
    [card] = hub.approvals()
    assert hub.answer(card['id'], 'maybe').status_code == 400
    assert hub.client.post(f'/api/approvals/{card["id"]}', json={}).status_code == 400


def test_a_session_cannot_answer_its_own_prompt(hub: ChannelHub) -> None:
    _, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        ch.send(**permission())
        ch.send(type='permission_decision', seq=1, request_id='qiwxo', behavior='allow')
        assert ch.until_pong() == []
    assert [a['state'] for a in hub.approvals()] == ['open']


def test_the_brain_cannot_open_an_approval_card(
    hub: ChannelHub, caplog: pytest.LogCaptureFixture
) -> None:
    brain = hub.brain()
    with hub.connect() as ch:
        ch.hello(brain)
        ch.send(**permission())
        ch.until_pong()
    assert hub.approvals() == []
    assert 'Dropped a permission request from brain session' in caplog.text


def test_open_cards_are_listed_first(hub: ChannelHub) -> None:
    _, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        for request_id in ('aaaaa', 'bbbbb', 'ccccc'):
            ch.send(**permission(request_id))
        ch.until_pong()
        oldest = hub.approvals()[-1]
        hub.answer(oldest['id'], 'approve')
    cards = hub.approvals()
    assert [c['state'] for c in cards] == ['open', 'open', 'approved']
    assert [c['state'] for c in hub.approvals(state='approved')] == ['approved']


def test_a_failing_push_never_loses_the_card(hub: ChannelHub) -> None:
    class Broken:
        def approval(self, approval: object, task: object) -> None:
            raise RuntimeError('ntfy is down')

    hub.gateway.push = Broken()
    task_id, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        ch.send(**permission())
        ch.until_pong()
    assert len(hub.approvals(state='open')) == 1
    assert hub.registry.get(task_id).state == TaskState.needs_you


# Calls: reply and report --------------------------------------------------------------------------


def test_a_repeated_call_id_is_stored_once_and_acked_twice(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        first = ch.call('report', call_id='c_01', kind='progress', text='Found the volumes.')
        second = ch.call('report', call_id='c_01', kind='progress', text='Found the volumes.')
    # After a reconnect, the server re-sends every call it has no ack for.
    with hub.connect() as ch:
        ch.hello(sid)
        third = ch.call('report', call_id='c_01', kind='progress', text='Found the volumes.')
    assert first == {'type': 'call_ack', 'call_id': 'c_01'}
    assert second == third == {'type': 'call_ack', 'call_id': 'c_01', 'duplicate': True}
    assert [e.data_json for e in hub.task_events(task_id, 'progress')] == [
        {'text': 'Found the volumes.'}
    ]


def test_a_repeated_reply_is_one_message(hub: ChannelHub) -> None:
    brain = hub.brain()
    with hub.connect() as ch:
        ch.hello(brain)
        assert ch.call('reply', call_id='c_r', text='On it.') == {
            'type': 'call_ack',
            'call_id': 'c_r',
        }
        assert ch.call('reply', call_id='c_r', text='On it.')['duplicate'] is True
    [message] = hub.messages()
    assert message.role.value == 'agent' and message.body_json == {'text': 'On it.'}
    [created] = hub.events('message.created')
    MESSAGE.validate(created['data'])


def test_a_reply_goes_to_the_side_threads_own_conversation(hub: ChannelHub) -> None:
    side_id, side = hub.side()
    with hub.connect() as ch:
        ch.hello(side)
        ch.call('reply', text='From the fork.')
    assert [m.body_json['text'] for m in hub.messages(side_id)] == ['From the fork.']
    assert hub.messages() == []


def test_a_session_acts_only_for_its_own_role(
    hub: ChannelHub, caplog: pytest.LogCaptureFixture
) -> None:
    task_id, sid = hub.task()
    brain = hub.brain()
    with hub.connect() as ch:
        ch.hello(sid)
        ch.send(type='reply', call_id='c_w', text='I am the brain now.')
        assert ch.until_pong() == []  # dropped, and not acked
    with hub.connect() as ch:
        ch.hello(brain)
        ch.send(type='report', call_id='c_b', kind='done', summary='Pretending to be a worker.')
        assert ch.until_pong() == []
    assert hub.messages() == []
    assert hub.registry.get(task_id).state == TaskState.working
    with Session(hub.engine) as s:
        assert s.exec(select(ChannelCall)).all() == []
    assert 'Dropped a reply from worker session' in caplog.text
    assert 'Dropped a report from brain session' in caplog.text


def test_bad_frames_are_ignored_and_the_socket_stays(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        ch.ws.send_text('not json')
        ch.send(type='report', call_id='c_x', kind='done')  # no summary
        ch.send(type='hello', session_id=sid, secret=SECRET, plugin_version='0.1.0')
        assert ch.until_pong() == []
        assert ch.call('report', kind='progress', text='Still here.')['type'] == 'call_ack'
    assert hub.registry.get(task_id).state == TaskState.working


def test_a_report_that_cannot_be_stored_is_not_acked(
    hub: ChannelHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, sid = hub.task()

    def broken(*_a: object, **_k: object) -> None:
        raise RuntimeError('disk full')

    monkeypatch.setattr(hub.registry, 'append_event', broken)
    with hub.connect() as ch:
        ch.hello(sid)
        ch.send(type='report', call_id='c_lost', kind='progress', text='Hello?')
        assert ch.until_pong() == []
    with Session(hub.engine) as s:
        assert s.get(ChannelCall, 'c_lost') is None  # so the re-send is stored


def test_reports_become_task_events_artifacts_and_milestones(hub: ChannelHub) -> None:
    task_id, sid = hub.task(TaskState.starting)
    with hub.connect() as ch:
        ch.hello(sid)
        ch.call('report', kind='started', text='Looking at the volumes.')
        assert hub.registry.get(task_id).state == TaskState.working
        ch.call('report', kind='progress', text='Five volumes found.')
        ch.call(
            'report',
            kind='artifact',
            artifact={'kind': 'link', 'title': 'Docker docs', 'url': 'https://docs.docker.com/'},
        )
        ch.call(
            'report',
            kind='done',
            summary='plex_media_cache is 38.2 GB.',
            artifacts=[
                {
                    'kind': 'dashboard',
                    'title': 'Docker volumes',
                    'path': '/home/owner/marcel/workspaces/scratch/volumes.dashboard.json',
                }
            ],
        )
    task = hub.registry.get(task_id)
    assert task.state == TaskState.done and task.summary == 'plex_media_cache is 38.2 GB.'
    assert [e.data_json['text'] for e in hub.task_events(task_id, 'progress')] == [
        'Looking at the volumes.',
        'Five volumes found.',
    ]
    with Session(hub.engine) as s:
        artifacts = s.exec(select(Artifact).order_by(Artifact.created_at)).all()  # pyright: ignore[reportArgumentType]
    assert [(a.kind.value, a.title, a.task_id) for a in artifacts] == [
        ('link', 'Docker docs', task_id),
        ('dashboard', 'Docker volumes', task_id),
    ]
    assert artifacts[1].meta_json['path'].endswith('volumes.dashboard.json')
    assert [e.data_json['title'] for e in hub.task_events(task_id, 'artifact')] == [
        'Docker docs',
        'Docker volumes',
    ]
    milestones = [m.body_json['milestone'] for m in hub.messages()]
    assert [m['event'] for m in milestones] == ['started', 'done']
    assert milestones[1] == {
        'task_id': task_id,
        'task_title': 'Check disk usage',
        'event': 'done',
        'location': 'nuc',
        'summary': 'plex_media_cache is 38.2 GB.',
        'artifact_ids': [artifacts[1].id],
    }
    for event in hub.events('message.created'):
        MESSAGE.validate(event['data'])


def test_a_report_after_the_owner_stopped_the_task_keeps_it_stopped(hub: ChannelHub) -> None:
    task_id, sid = hub.task(TaskState.stopped)
    with hub.connect() as ch:
        ch.hello(sid)
        assert ch.call('report', kind='progress', text='Still going.')['type'] == 'call_ack'
        assert ch.call('report', kind='done', summary='Finished anyway.')['type'] == 'call_ack'
    assert hub.registry.get(task_id).state == TaskState.stopped


# Delivery -----------------------------------------------------------------------------------------


def test_a_steer_for_an_offline_session_is_delivered_after_it_connects(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    res = hub.client.post(f'/api/tasks/{task_id}/messages', json={'text': 'Include unused ones.'})
    assert res.status_code == 202, res.text
    with hub.connect() as ch:
        ch.hello(sid)
        steer = ch.deliver()
    assert steer['type'] == 'steer' and steer['seq'] == 1
    assert steer['task_id'] == task_id and steer['text'] == 'Include unused ones.'


def test_an_unacked_frame_is_sent_again_after_a_reconnect(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    hub.client.post(f'/api/tasks/{task_id}/messages', json={'text': 'First.'})
    with hub.connect() as ch:
        ch.hello(sid)
        assert ch.next()['seq'] == 1  # delivered, but the socket drops before the ack
    hub.client.post(f'/api/tasks/{task_id}/messages', json={'text': 'Second.'})
    with hub.connect() as ch:
        ch.hello(sid)
        assert [ch.deliver()['text'], ch.deliver()['text']] == ['First.', 'Second.']
        ch.until_pong()
    # Acked frames are not sent again; numbering carries on.
    hub.client.post(f'/api/tasks/{task_id}/messages', json={'text': 'Third.'})
    with hub.connect() as ch:
        ch.hello(sid)
        third = ch.deliver()
    assert (third['seq'], third['text']) == (3, 'Third.')


def test_resume_after_counts_as_an_ack(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    for text in ('One.', 'Two.'):
        hub.client.post(f'/api/tasks/{task_id}/messages', json={'text': text})
    with hub.connect() as ch:
        ch.hello(sid, resume_after=1)
        assert ch.deliver()['text'] == 'Two.'


def test_a_steer_for_a_queued_task_waits_for_its_session(hub: ChannelHub) -> None:
    task_id, _ = hub.task(TaskState.queued, sid=None)
    hub.client.post(f'/api/tasks/{task_id}/messages', json={'text': 'Use the big disk.'})
    assert len(hub.gateway.outbox.pending(f'task:{task_id}')) == 1


def test_a_long_backlog_is_delivered_in_order(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    for n in range(230):
        hub.gateway.outbox.put(
            f'task:{task_id}',
            {'type': 'steer', 'task_id': task_id, 'note_id': f'n{n}', 'author': 'user',
             'text': str(n)},
        )  # fmt: skip
    with hub.connect() as ch:
        ch.hello(sid)
        got = [ch.deliver()['seq'] for _ in range(230)]
    assert got == list(range(1, 231))


def test_the_owners_message_reaches_the_brain_and_its_reply_reaches_the_phone(
    hub: ChannelHub,
) -> None:
    brain = hub.brain()
    res = hub.client.post(
        f'/api/conversations/{hub.main_id}/messages', json={'text': 'How full is the disk?'}
    )
    assert res.status_code == 201
    with hub.connect() as ch:
        ch.hello(brain)
        message = ch.deliver()
        assert message == {
            'type': 'user_message',
            'seq': 1,
            'message_id': res.json()['id'],
            'conversation_id': hub.main_id,
            'text': 'How full is the disk?',
        }
        ch.call('reply', text='Checking now.', in_reply_to=message['message_id'])
    assert [m.body_json['text'] for m in hub.messages()] == [
        'How full is the disk?',
        'Checking now.',
    ]


def test_a_quote_sent_to_main_reaches_the_brain(hub: ChannelHub) -> None:
    brain = hub.brain()
    side_id, _ = hub.side()
    with Session(hub.engine) as s:
        from marcel_hub.models import Message, MessageKind, MessageRole

        msg = Message(
            conversation_id=side_id,
            role=MessageRole.agent,
            kind=MessageKind.text,
            body_json={'text': 'The answer is 42.'},
        )
        s.add(msg)
        s.commit()
        quoted = msg.id
    res = hub.client.post(f'/api/conversations/{side_id}/send-to-main', json={'message_id': quoted})
    assert res.status_code == 201, res.text
    with hub.connect() as ch:
        ch.hello(brain)
        frame = ch.deliver()
    assert frame['quoted_message_id'] == quoted and frame['text'] == 'The answer is 42.'


def test_a_task_threads_messages_never_reach_the_brain(hub: ChannelHub) -> None:
    task_id, _ = hub.task()
    thread_id, _ = hub.side(task_id=task_id)
    hub.brain()
    hub.client.post(f'/api/conversations/{thread_id}/messages', json={'text': 'For the task.'})
    assert hub.gateway.outbox.pending(f'conversation:{thread_id}') == []
    assert hub.gateway.outbox.pending(f'conversation:{hub.main_id}') == []


def test_a_handback_wakes_the_brain(hub: ChannelHub) -> None:
    task_id, _ = hub.task(TaskState.done)
    brain = hub.brain()
    res = hub.client.post(f'/api/tasks/{task_id}/handback', json={'note': 'Try the NAS.'})
    assert res.status_code == 202, res.text
    with hub.connect() as ch:
        ch.hello(brain)
        event = ch.deliver()
    assert event['type'] == 'system_event' and event['kind'] == 'handback'
    assert event['data']['note'] == 'Try the NAS.'


def test_a_message_that_cannot_be_queued_is_still_stored(
    hub: ChannelHub, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    hub.brain()

    def broken(*_a: object) -> None:
        raise RuntimeError('disk full')

    monkeypatch.setattr(hub.gateway.outbox, 'to_conversation', broken)
    res = hub.client.post(f'/api/conversations/{hub.main_id}/messages', json={'text': 'Hi'})
    assert res.status_code == 201
    assert 'Could not queue message' in caplog.text
