"""The ntfy backend: what it posts, how it fails, and that a topic never reaches a log."""

import logging

import pytest
from sqlalchemy.engine import Engine
from test_notify_fakes import NTFY, TOPIC, FakeNtfy, add_device, pusher_for
from test_tasks_fakes import add_agent, put_task, run

from marcel_hub.models import Device, TaskState
from marcel_hub.notify import NtfyNotifier, PushFailed, PushNotification

APPROVAL = PushNotification(
    title='Marcel needs your OK',
    body='Check disk usage: allow `docker system df -v`?',
    category='approval',
    level='urgent',
    deep_link='marcel://approval/apr_01J9ZR2',
    collapse_key='tsk_01J9ZQ6',
    task_id='tsk_01J9ZQ6',
    approval_id='apr_01J9ZR2',
)
DONE = PushNotification(
    title='Done: Check the disk',
    body='12 GB free on /',
    category='task',
    level='normal',
    deep_link='marcel://task/tsk_01J9ZQ6',
)
DEVICE = Device(id='dev_1', user_id='usr_1', name='Phone', token_hash='h', ntfy_topic=TOPIC)


def test_the_ntfy_backend_posts_the_contract_mapping() -> None:
    ntfy = FakeNtfy()

    async def go() -> None:
        notifier = ntfy.notifier()
        await notifier.send(DEVICE, APPROVAL)
        await notifier.send(DEVICE, DONE)
        await notifier.aclose()

    run(go())
    first = ntfy.requests[0]
    assert first.method == 'POST'
    assert str(first.url) == NTFY + '/'  # the topic is in the body, never in the URL
    assert 'authorization' not in first.headers
    assert ntfy.bodies == [
        {
            'topic': TOPIC,
            'title': 'Marcel needs your OK',
            'message': 'Check disk usage: allow `docker system df -v`?',
            'tags': ['approval'],
            'priority': 4,
            'click': 'marcel://approval/apr_01J9ZR2',
        },
        {
            'topic': TOPIC,
            'title': 'Done: Check the disk',
            'message': '12 GB free on /',
            'tags': ['task'],
            'priority': 3,
            'click': 'marcel://task/tsk_01J9ZQ6',
        },
    ]


@pytest.mark.parametrize(
    ('mode', 'retry'), [('down', True), (500, True), (429, True), (400, False), (403, False)]
)
def test_a_failed_post_raises_push_failed(mode: str | int, retry: bool) -> None:
    ntfy = FakeNtfy(mode)
    with pytest.raises(PushFailed) as err:
        run(ntfy.notifier().send(DEVICE, DONE))
    assert err.value.retry is retry
    assert TOPIC not in str(err.value)


def test_a_device_without_a_topic_is_not_posted() -> None:
    ntfy = FakeNtfy()
    phone = Device(id='dev_2', user_id='usr_1', name='Phone', token_hash='h')
    with pytest.raises(PushFailed) as err:
        run(ntfy.notifier().send(phone, DONE))
    assert err.value.retry is False and ntfy.requests == []


def test_the_server_address_may_end_in_a_slash() -> None:
    assert NtfyNotifier('https://ntfy.test/').server == 'https://ntfy.test'


@pytest.mark.parametrize('mode', ['ok', 'down', 500, 400])
def test_a_topic_is_never_logged(
    engine: Engine, caplog: pytest.LogCaptureFixture, mode: str | int
) -> None:
    caplog.set_level(logging.DEBUG)
    add_agent(engine)
    add_device(engine)
    ntfy = FakeNtfy(mode)
    pusher = pusher_for(engine, ntfy.notifier())
    task = put_task(engine, TaskState.needs_you)

    async def go() -> None:
        pusher.on_state(task, 'needs_you')
        await pusher.wait()
        await pusher.aclose()

    run(go())
    assert ntfy.requests, 'the push was attempted'
    assert caplog.records, 'the push was logged'
    assert all(TOPIC not in r.getMessage() for r in caplog.records)
    assert all(TOPIC not in str(r.exc_info) for r in caplog.records)
    assert 'dev_phone' in caplog.text  # logs name the device instead
