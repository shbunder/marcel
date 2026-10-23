"""Routing: each event maps to the right category, level and deep link, and every push matches the
contract's `PushNotification`. Quiet events send nothing. A buddy's push goes to the phones showing
that buddy; a push about no buddy goes to every phone (S-22.27)."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlmodel import Session
from test_auth import bearer, pair
from test_notify_fakes import Clock, RecordingNotifier, add_approval, add_device, pusher_for
from test_tasks_fakes import add_agent, put_task, run, validator

from marcel_hub.app import Settings, create_app
from marcel_hub.auth import tokens
from marcel_hub.channel.approvals import SELF_CHANGE_TOOL
from marcel_hub.conversations import ensure_main_conversation
from marcel_hub.models import (
    Agent,
    Approval,
    Conversation,
    ConversationKind,
    Device,
    Message,
    MessageKind,
    MessageRole,
    Task,
    TaskLocation,
    TaskOrigin,
    utcnow,
)
from marcel_hub.models import TaskState as S
from marcel_hub.notify import Pusher, PushNotification
from marcel_hub.notify.message import clip
from marcel_hub.notify.routing import for_note, for_state
from marcel_hub.users.store import ensure_owner_agent

PUSH = validator('app-api.yaml#/components/schemas/PushNotification')


def drive(pusher: Pusher, action: Any) -> None:
    async def go() -> None:
        action()
        await pusher.wait()

    run(go())


@pytest.fixture
def setup(engine: Engine) -> tuple[Pusher, RecordingNotifier]:
    add_agent(engine, owner='usr_owner')
    add_device(engine)
    notifier = RecordingNotifier()
    return pusher_for(engine, notifier), notifier


def test_the_routing_table(engine: Engine, setup: tuple[Pusher, RecordingNotifier]) -> None:
    pusher, notifier = setup
    waiting = put_task(engine, S.needs_you, waiting_for='Which branch should I use?')
    approving = put_task(engine, S.needs_you)
    approval = add_approval(engine, approving)
    done = put_task(engine, S.done, summary='12 GB free on /')
    failed = put_task(engine, S.failed)
    silent = put_task(engine, S.silent)
    with Session(engine) as s:
        s.add(Conversation(id='cnv_1', agent_id='agt_marcel', kind=ConversationKind.main))
        s.add(Message(id='msg_digest', conversation_id='cnv_1', role=MessageRole.agent,
                      kind=MessageKind.text, body_json={'text': 'Morning'}))  # fmt: skip
        s.commit()

    def events() -> None:
        pusher.on_state(approving, 'needs_you', 'A permission request')
        pusher.on_state(waiting, 'needs_you', 'A question')
        pusher.on_state(done, 'done', 'The runner reports done')
        pusher.on_state(failed, 'failed', 'Could not start: no repository')
        pusher.on_state(silent, 'silent', 'No activity')
        pusher.broke('runner_unreachable', 'Marcel cannot reach the NUC', 'It keeps trying.')
        pusher.digest('msg_digest', 'Your morning plan', '2 approvals wait.')

    drive(pusher, events)
    got = [(n.category, n.level, n.deep_link) for _, n in notifier.sent]
    assert got == [
        ('approval', 'urgent', f'marcel://approval/{approval}'),
        ('task', 'urgent', f'marcel://task/{waiting}'),
        ('task', 'normal', f'marcel://task/{done}'),
        ('task', 'normal', f'marcel://task/{failed}'),
        ('broke', 'urgent', f'marcel://task/{silent}'),
        # No task is about the runner: the push opens the app's status screen.
        ('broke', 'urgent', 'marcel://status'),
        ('digest', 'normal', 'marcel://message/msg_digest'),
    ]
    for _, n in notifier.sent:
        PUSH.validate(n.to_api())
    by_link = {n.deep_link: n for _, n in notifier.sent}
    apr = by_link[f'marcel://approval/{approval}']
    assert (apr.approval_id, apr.task_id, apr.collapse_key) == (approval, approving, approving)
    assert apr.title == 'Marcel needs your OK'
    assert by_link[f'marcel://task/{waiting}'].body.endswith('Which branch should I use?')
    assert by_link[f'marcel://task/{done}'].body == '12 GB free on /'
    assert 'no repository' in by_link[f'marcel://task/{failed}'].body


@pytest.mark.parametrize(
    'state', [S.queued, S.starting, S.working, S.unknown, S.stopped], ids=lambda s: s.value
)
def test_other_state_changes_are_quiet(
    engine: Engine, setup: tuple[Pusher, RecordingNotifier], state: S
) -> None:
    pusher, notifier = setup
    task = put_task(engine, state)
    drive(pusher, lambda: pusher.on_state(task, state.value, 'whatever'))
    assert notifier.sent == []


def test_progress_and_transcript_events_never_push(
    engine: Engine, setup: tuple[Pusher, RecordingNotifier]
) -> None:
    pusher, notifier = setup
    task = put_task(engine, S.working)
    publisher = pusher.task_publisher()

    def events() -> None:
        for type_ in ('progress', 'text', 'tool_call', 'note', 'artifact'):
            publisher.publish(
                'task.event', {'task_id': task, 'seq': 1, 'type': type_, 'data': {'pct': 50}}
            )
        publisher.publish('task.updated', {'id': task, 'state': 'done'})

    drive(pusher, events)
    assert notifier.sent == []


def test_an_approval_opened_later_pushes_once(
    engine: Engine, setup: tuple[Pusher, RecordingNotifier]
) -> None:
    pusher, notifier = setup
    task = put_task(engine, S.needs_you)
    approval = add_approval(engine, task)
    drive(pusher, lambda: pusher.approval_opened(approval))
    drive(pusher, lambda: pusher.approval_opened('apr_unknown'))
    assert [n.deep_link for _, n in notifier.sent] == [f'marcel://approval/{approval}']


def test_long_texts_are_clipped_and_still_valid(engine: Engine) -> None:
    add_agent(engine, owner='usr_owner')
    task = Task(agent_id='agt_marcel', title='T' * 200, location=TaskLocation.nuc, state=S.done,
                model='sonnet', origin=TaskOrigin.marcel, summary='word ' * 200)  # fmt: skip
    n = for_state(task, S.done)
    assert n is not None
    PUSH.validate(n.to_api())
    assert len(n.title) == 80 and n.title.endswith('…')
    assert len(n.body) <= 300 and n.body.endswith('…')
    assert clip('short\n  text', 80) == 'short text'


def test_the_model_refuses_what_the_contract_refuses() -> None:
    ok: dict[str, Any] = {'title': 'T', 'body': 'B', 'category': 'task', 'level': 'normal',
                          'deep_link': 'marcel://task/tsk_1'}  # fmt: skip
    PushNotification(**ok)
    with pytest.raises(ValidationError):
        PushNotification(**{**ok, 'deep_link': 'https://evil.example/x'})
    with pytest.raises(ValidationError):
        PushNotification(**{**ok, 'category': 'approval'})  # no approval and task id
    with pytest.raises(ValidationError):
        PushNotification(**{**ok, 'token': 'mrc_x'})


def test_a_runner_alert_opens_the_status_screen_not_a_task_or_message(engine: Engine) -> None:
    add_device(engine)
    notifier = RecordingNotifier()
    pusher = pusher_for(engine, notifier, clock=Clock())
    add_agent(engine, owner='usr_owner')
    put_task(engine, S.working)  # a task and a message exist: neither is what the push is about
    with Session(engine) as s:
        s.add(Conversation(id='cnv_1', agent_id='agt_marcel', kind=ConversationKind.main))
        s.add(Message(id='msg_1', conversation_id='cnv_1', role=MessageRole.user,
                      kind=MessageKind.text, body_json={'text': 'hi'}))  # fmt: skip
        s.commit()
    drive(pusher, lambda: pusher.broke('runner_warning', 'The NUC has a problem', 'Disk full.'))
    drive(pusher, lambda: pusher.resolved('runner_warning'))
    assert [(n.title, n.deep_link) for _, n in notifier.sent] == [
        ('The NUC has a problem', 'marcel://status'),
        ('The NUC is fine again', 'marcel://status'),
    ]
    for _, n in notifier.sent:
        PUSH.validate(n.to_api())


def test_a_runner_alert_on_an_empty_hub_still_goes_out(engine: Engine) -> None:
    add_device(engine)
    notifier = RecordingNotifier()
    pusher = pusher_for(engine, notifier)
    drive(pusher, lambda: pusher.broke('runner_unreachable', 'Marcel cannot reach the NUC', 'x'))
    assert [n.deep_link for _, n in notifier.sent] == ['marcel://status']


def test_a_notice_is_its_own_push_on_its_message() -> None:
    n = for_note('msg_1', 'Marcel', 'The NAS is almost full.', 'urgent')
    assert n.to_api() == {
        'title': 'Marcel',
        'body': 'The NAS is almost full.',
        'category': 'note',
        'level': 'urgent',
        'deep_link': 'marcel://message/msg_1',
        'collapse_key': 'msg_1',
    }
    PUSH.validate(n.to_api())


# Which phones get a push (S-22.27, B-49) -------------------------------------------------------


def two_buddies_and_phones(engine: Engine) -> None:
    """Marcel and Pip, each with its main conversation; `dev_a` shows Marcel, `dev_b` Pip."""
    for agent_id, name in (('agt_marcel', 'Marcel'), ('agt_pip', 'Pip')):
        add_agent(engine, agent_id, owner='usr_owner')
        with Session(engine) as s:
            agent = s.get(Agent, agent_id)
            assert agent is not None
            agent.name = name
            ensure_main_conversation(s, agent)
    add_device(engine, 'dev_a')
    add_device(engine, 'dev_b', ntfy_topic='b-topic')
    show(engine, 'dev_a', 'agt_marcel')
    show(engine, 'dev_b', 'agt_pip')


def show(engine: Engine, device_id: str, agent_id: str | None) -> None:
    with Session(engine) as s:
        device = s.get(Device, device_id)
        assert device is not None
        device.agent_id = agent_id
        s.add(device)
        s.commit()


def test_a_buddys_pushes_go_to_the_phones_showing_it(engine: Engine) -> None:
    # Control: delete the buddy filter in `Pusher._devices` and this goes red.
    two_buddies_and_phones(engine)
    notifier = RecordingNotifier()
    pusher = pusher_for(engine, notifier)
    marcels = put_task(engine, S.done, agent_id='agt_marcel')
    pips = put_task(engine, S.done, agent_id='agt_pip')
    pips_question = put_task(engine, S.needs_you, agent_id='agt_pip')
    approval = add_approval(engine, pips_question)

    def events() -> None:
        pusher.on_state(marcels, 'done')
        pusher.on_state(pips, 'done')
        pusher.approval_opened(approval)
        pusher.note('agt_pip', 'msg_n', 'Pip', 'The NAS is almost full.')
        pusher.digest('msg_d', 'Your morning plan', '2 approvals wait.', 'agt_marcel')

    drive(pusher, events)
    got = sorted((d, n.deep_link) for d, n in notifier.sent)
    assert got == sorted([
        ('dev_a', f'marcel://task/{marcels}'),
        ('dev_b', f'marcel://task/{pips}'),
        ('dev_b', f'marcel://approval/{approval}'),
        ('dev_b', 'marcel://message/msg_n'),
        ('dev_a', 'marcel://message/msg_d'),
    ])  # fmt: skip


def test_switching_a_phones_buddy_moves_that_buddys_next_push(tmp_path: Path) -> None:
    """Through the app: `PATCH /devices/{id}` on the iPad, then Pip's next push."""
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db')
    with TestClient(create_app(settings)) as client:
        engine: Engine = client.app.state.engine  # type: ignore[attr-defined]
        with Session(engine) as s:
            ensure_owner_agent(s, 'Marcel')
        phone, ipad = pair(client, 'phone'), pair(client, 'iPad')
        for paired, topic in ((phone, 'phone-topic'), (ipad, 'ipad-topic')):
            res = client.put(f'/api/devices/{paired["device_id"]}/push',
                             json={'kind': 'ntfy', 'ntfy_topic': topic},
                             headers=bearer(paired['token']))  # fmt: skip
            assert res.status_code == 200
        pip = client.post('/api/agents', json={'name': 'Pip'}, headers=bearer(phone['token']))
        pip_id = pip.json()['id']
        notifier = RecordingNotifier()
        pusher = pusher_for(engine, notifier)
        before = put_task(engine, S.done, agent_id=pip_id)
        drive(pusher, lambda: pusher.on_state(before, 'done'))
        assert notifier.sent == [], 'no phone shows Pip yet'
        res = client.patch(f'/api/devices/{ipad["device_id"]}', json={'agent_id': pip_id},
                           headers=bearer(phone['token']))  # fmt: skip
        assert res.status_code == 200
        after = put_task(engine, S.done, agent_id=pip_id)
        drive(pusher, lambda: pusher.on_state(after, 'done'))
        assert [d for d, _ in notifier.sent] == [ipad['device_id']], 'to the iPad, not the phone'


def test_a_notice_about_no_buddy_reaches_every_phone(engine: Engine) -> None:
    two_buddies_and_phones(engine)
    add_device(engine, 'dev_c', ntfy_topic='c-topic')
    show(engine, 'dev_c', None)
    notifier = RecordingNotifier()
    pusher = pusher_for(engine, notifier)
    every = ['dev_a', 'dev_b', 'dev_c']
    usage = PushNotification(title='Marcel is pausing new work', body='Until 18:00.',
                             category='note', level='normal', deep_link='marcel://status',
                             collapse_key='usage')  # fmt: skip
    drive(pusher, lambda: pusher.push(usage, None))  # usage.py
    assert sorted(d for d, _ in notifier.sent) == every
    notifier.sent.clear()
    redeploy = usage.model_copy(update={'collapse_key': 'redeploy', 'title': 'Marcel is updated'})
    drive(pusher, lambda: pusher.push(redeploy, tokens.OWNER_USER_ID))  # the watchdog's way
    assert sorted(d for d, _ in notifier.sent) == every
    notifier.sent.clear()
    drive(pusher, lambda: pusher.broke('runner_unreachable', 'Marcel cannot reach the NUC', 'x'))
    assert sorted(d for d, _ in notifier.sent) == every
    notifier.sent.clear()
    drive(pusher, lambda: pusher.note('agt_marcel', 'msg_u', 'Marcel', 'Claude Code 2.3 is here.',
                                      everyone=True))  # fmt: skip
    assert sorted(d for d, _ in notifier.sent) == every
    notifier.sent.clear()
    # A phone with no buddy of its own shows the first one (`buddy_of`), and gets its pushes.
    task = put_task(engine, S.done, agent_id='agt_marcel')
    drive(pusher, lambda: pusher.on_state(task, 'done'))
    assert sorted(d for d, _ in notifier.sent) == ['dev_a', 'dev_c']


def test_a_self_change_reaches_every_phone_whichever_buddy_opened_it(engine: Engine) -> None:
    two_buddies_and_phones(engine)
    notifier = RecordingNotifier()
    pusher = pusher_for(engine, notifier)
    task = put_task(engine, S.needs_you, agent_id='agt_pip')
    with Session(engine) as s:
        s.add(Approval(id='apr_self', task_id=task, tool=SELF_CHANGE_TOOL, request_id=None,
                       artifact_id='art_pr', summary='Merge "Fix the digest"?'))  # fmt: skip
        s.commit()
    drive(pusher, lambda: pusher.approval_opened('apr_self'))
    assert sorted(d for d, _ in notifier.sent) == ['dev_a', 'dev_b']


def test_a_phone_whose_buddy_was_archived_gets_the_first_buddys_pushes(engine: Engine) -> None:
    two_buddies_and_phones(engine)
    with Session(engine) as s:
        pip = s.get(Agent, 'agt_pip')
        assert pip is not None
        pip.archived_at = utcnow()
        s.add(pip)
        s.commit()
    notifier = RecordingNotifier()
    pusher = pusher_for(engine, notifier)
    task = put_task(engine, S.done, agent_id='agt_marcel')
    drive(pusher, lambda: pusher.on_state(task, 'done'))
    assert sorted(d for d, _ in notifier.sent) == ['dev_a', 'dev_b']


def test_an_approval_for_a_buddy_no_phone_shows_reaches_every_phone(engine: Engine) -> None:
    """An approval never waits silently (lead, S-22.15): with no phone on Pip, Pip's approval goes
    to every phone. Control: delete the fallback in `Pusher._devices` and this goes red."""
    two_buddies_and_phones(engine)
    show(engine, 'dev_b', 'agt_marcel')  # now no phone shows Pip
    notifier = RecordingNotifier()
    pusher = pusher_for(engine, notifier)
    done = put_task(engine, S.done, agent_id='agt_pip')
    drive(pusher, lambda: pusher.on_state(done, 'done'))
    assert notifier.sent == [], "Pip's other pushes still go only to the phones showing Pip"
    asking = put_task(engine, S.needs_you, agent_id='agt_pip')
    add_approval(engine, asking)
    drive(pusher, lambda: pusher.on_state(asking, 'needs_you'))  # the task event's way
    assert sorted((d, n.category) for d, n in notifier.sent) == [
        ('dev_a', 'approval'),
        ('dev_b', 'approval'),
    ]
    assert notifier.buddies[-2:] == ['agt_pip', 'agt_pip'], 'a tap still opens Pip'
    notifier.sent.clear()
    show(engine, 'dev_b', 'agt_pip')  # a phone shows Pip again: only it gets Pip's next approval
    later = add_approval(engine, put_task(engine, S.needs_you, agent_id='agt_pip'), 'apr_2')
    drive(pusher, lambda: pusher.approval_opened(later))  # the channel gateway's way
    assert [d for d, _ in notifier.sent] == ['dev_b']
