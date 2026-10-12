"""Routing: each event maps to the right category, level and deep link, and every push matches the
contract's `PushNotification`. Quiet events send nothing."""

from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlmodel import Session
from test_notify_fakes import Clock, RecordingNotifier, add_approval, add_device, pusher_for
from test_tasks_fakes import add_agent, put_task, run, validator

from marcel_hub.models import (
    Conversation,
    ConversationKind,
    Message,
    MessageKind,
    MessageRole,
    Task,
    TaskLocation,
    TaskOrigin,
)
from marcel_hub.models import TaskState as S
from marcel_hub.notify import Pusher, PushNotification
from marcel_hub.notify.message import clip
from marcel_hub.notify.routing import for_note, for_state

PUSH = validator('app-api.yaml#/components/schemas/PushNotification')


def drive(pusher: Pusher, action: Any) -> None:
    async def go() -> None:
        action()
        await pusher.wait()

    run(go())


@pytest.fixture
def setup(engine: Engine) -> tuple[Pusher, RecordingNotifier]:
    add_agent(engine)
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
    add_agent(engine)
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
    add_agent(engine)
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
