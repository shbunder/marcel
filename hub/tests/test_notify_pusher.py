"""The pusher's controls: retries with a bound, quiet repeats, one "broke" per cause per hour, the
failed-push alert, who receives what, and that nothing it does is raised into the caller."""

import asyncio
import logging
import threading
from collections.abc import Callable

import pytest
from sqlalchemy.engine import Engine
from test_notify_fakes import (
    Clock,
    FakeNtfy,
    RecordingNotifier,
    Sleeps,
    add_device,
    pusher_for,
)
from test_tasks_fakes import add_agent, put_task, run

from marcel_hub.models import TaskState
from marcel_hub.notify import Pusher, PushFailed
from marcel_hub.notify.backends import Kind
from marcel_hub.notify.pusher import (
    BROKE_SECONDS,
    COLLAPSE_SECONDS,
    NOTE_LIMIT,
    NOTE_SECONDS,
    RETRY_DELAYS,
)


def drive(pusher: Pusher, action: Callable[[], object]) -> None:
    async def go() -> None:
        action()
        await pusher.wait()

    run(go())


@pytest.fixture
def task(engine: Engine) -> str:
    add_agent(engine, owner='usr_owner')
    add_device(engine)
    return put_task(engine, TaskState.done, summary='12 GB free')


# Retries ---------------------------------------------------------------------------------------


def test_a_failing_push_is_retried_with_backoff_then_sent(engine: Engine, task: str) -> None:
    ntfy = FakeNtfy(500)
    sleeps = Sleeps()
    pusher = pusher_for(engine, ntfy.notifier(), sleep=sleeps)

    async def go() -> None:
        async def recover(delay: float) -> None:
            sleeps.delays.append(delay)
            if len(sleeps.delays) == 2:
                ntfy.mode = 'ok'

        pusher.sleep = recover
        pusher.on_state(task, 'done')
        await pusher.wait()

    run(go())
    assert sleeps.delays == list(RETRY_DELAYS)
    assert len(ntfy.requests) == 3


def test_retries_are_bounded(engine: Engine, task: str, caplog: pytest.LogCaptureFixture) -> None:
    ntfy = FakeNtfy('down')
    pusher = pusher_for(engine, ntfy.notifier())
    drive(pusher, lambda: pusher.on_state(task, 'done'))
    assert len(ntfy.requests) == len(RETRY_DELAYS) + 1
    assert 'did not go out after 3 tries' in caplog.text


def test_a_refused_push_is_not_retried(engine: Engine, task: str) -> None:
    ntfy = FakeNtfy(400)
    sleeps = Sleeps()
    pusher = pusher_for(engine, ntfy.notifier(), sleep=sleeps)
    drive(pusher, lambda: pusher.on_state(task, 'done'))
    assert len(ntfy.requests) == 1 and sleeps.delays == []


def test_a_crashing_backend_is_logged_not_raised(
    engine: Engine, task: str, caplog: pytest.LogCaptureFixture
) -> None:
    notifier = RecordingNotifier(fail=RuntimeError('boom'))
    pusher = pusher_for(engine, notifier)
    drive(pusher, lambda: pusher.on_state(task, 'done'))
    assert notifier.tries == 1
    assert 'Sending "Done: ' in caplog.text


# Failed urgent pushes --------------------------------------------------------------------------


class Flaky(RecordingNotifier):
    """Fails for one device only."""

    def __init__(self, dead: str) -> None:
        super().__init__()
        self.dead = dead

    async def send(self, device, notification) -> None:  # type: ignore[override]
        self.tries += 1
        if device.id == self.dead:
            raise PushFailed('down', retry=False)
        self.sent.append((device.id, notification))


def test_a_failed_urgent_push_raises_one_broke_push_to_every_device(engine: Engine) -> None:
    add_agent(engine, owner='usr_owner')
    add_device(engine, 'dev_phone')
    add_device(engine, 'dev_ipad', ntfy_topic='other-topic')
    notifier = Flaky(dead='dev_phone')
    pusher = pusher_for(engine, notifier)
    first = put_task(engine, TaskState.needs_you)
    second = put_task(engine, TaskState.needs_you)

    def events() -> None:
        pusher.on_state(first, 'needs_you')
        pusher.on_state(second, 'needs_you')

    drive(pusher, events)
    run(pusher.wait())
    on_ipad = [(n.category, n.title) for d, n in notifier.sent if d == 'dev_ipad']
    assert on_ipad.count(('broke', 'A notification did not arrive')) == 1  # once per hour
    assert on_ipad.count(('task', 'Marcel needs you')) == 2


def test_a_failed_normal_push_raises_nothing_more(engine: Engine, task: str) -> None:
    notifier = RecordingNotifier(fail=PushFailed('down', retry=False))
    pusher = pusher_for(engine, notifier)
    drive(pusher, lambda: pusher.on_state(task, 'done'))
    assert notifier.tries == 1


def test_a_failed_broke_push_does_not_alert_about_itself(engine: Engine, task: str) -> None:
    notifier = RecordingNotifier(fail=PushFailed('down', retry=False))
    pusher = pusher_for(engine, notifier)
    drive(pusher, lambda: pusher.broke('runner_unreachable', 'Marcel cannot reach the NUC', 'x'))
    assert notifier.tries == 1


# Quiet repeats ---------------------------------------------------------------------------------


def test_one_broke_push_per_cause_per_hour(engine: Engine, task: str) -> None:
    notifier = RecordingNotifier()
    clock = Clock()
    pusher = pusher_for(engine, notifier, clock=clock)
    down = ('runner_unreachable', 'Marcel cannot reach the NUC', 'It keeps trying.')

    drive(pusher, lambda: pusher.broke(*down))
    drive(pusher, lambda: pusher.broke(*down))
    drive(pusher, lambda: pusher.broke('runner_warning', 'The NUC has a problem', 'Disk full.'))
    assert [n.collapse_key for _, n in notifier.sent] == ['runner_unreachable', 'runner_warning']
    clock.now += BROKE_SECONDS - 1
    drive(pusher, lambda: pusher.broke(*down))
    assert len(notifier.sent) == 2
    clock.now += 1
    drive(pusher, lambda: pusher.broke(*down))
    assert len(notifier.sent) == 3


def test_a_silent_task_pushes_once_per_hour(engine: Engine) -> None:
    add_agent(engine, owner='usr_owner')
    add_device(engine)
    notifier = RecordingNotifier()
    pusher = pusher_for(engine, notifier)
    task = put_task(engine, TaskState.silent)
    drive(pusher, lambda: pusher.on_state(task, 'silent'))
    drive(pusher, lambda: pusher.on_state(task, 'silent'))
    assert len(notifier.sent) == 1


def test_repeats_per_task_are_collapsed(engine: Engine, task: str) -> None:
    notifier = RecordingNotifier()
    clock = Clock()
    pusher = pusher_for(engine, notifier, clock=clock)
    other = put_task(engine, TaskState.done, summary='12 GB free')

    drive(pusher, lambda: pusher.on_state(task, 'done'))
    drive(pusher, lambda: pusher.on_state(task, 'done'))  # same push, same task: quiet
    drive(pusher, lambda: pusher.on_state(other, 'done'))  # another task: sent
    assert [n.task_id for _, n in notifier.sent] == [task, other]
    clock.now += COLLAPSE_SECONDS
    drive(pusher, lambda: pusher.on_state(task, 'done'))
    assert len(notifier.sent) == 3
    assert all(n.collapse_key == n.task_id for _, n in notifier.sent)


def test_a_new_event_on_the_same_task_is_not_a_repeat(engine: Engine, task: str) -> None:
    notifier = RecordingNotifier()
    pusher = pusher_for(engine, notifier)
    failed = put_task(engine, TaskState.failed)
    drive(pusher, lambda: pusher.on_state(failed, 'failed', 'one'))
    drive(pusher, lambda: pusher.on_state(task, 'done'))
    drive(pusher, lambda: pusher.on_state(failed, 'needs_you'))
    assert [n.category for _, n in notifier.sent] == ['task', 'task', 'task']


def test_the_collapse_memory_is_bounded(engine: Engine, task: str) -> None:
    notifier = RecordingNotifier()
    pusher = pusher_for(engine, notifier)
    import marcel_hub.notify.pusher as module

    old = module.REMEMBER_TASKS
    module.REMEMBER_TASKS = 2
    try:
        for i in range(4):
            drive(pusher, lambda i=i: pusher.digest(f'msg_{i}', 'Digest', 'x'))
            tid = put_task(engine, TaskState.done)
            drive(pusher, lambda tid=tid: pusher.on_state(tid, 'done'))
        assert len(pusher._last_task_push) == 2
    finally:
        module.REMEMBER_TASKS = old


# The all-clear ---------------------------------------------------------------------------------


def test_the_nuc_coming_back_replaces_the_alert(engine: Engine, task: str) -> None:
    notifier = RecordingNotifier()
    pusher = pusher_for(engine, notifier)
    drive(pusher, lambda: pusher.resolved('runner_unreachable'))  # nothing was raised: quiet
    drive(pusher, lambda: pusher.broke('runner_unreachable', 'Marcel cannot reach the NUC', 'x'))
    drive(pusher, lambda: pusher.resolved('runner_unreachable'))
    drive(pusher, lambda: pusher.resolved('runner_unreachable'))
    got = [(n.level, n.title, n.collapse_key) for _, n in notifier.sent]
    assert got == [
        ('urgent', 'Marcel cannot reach the NUC', 'runner_unreachable'),
        ('normal', 'The NUC is back', 'runner_unreachable'),
    ]


def test_a_second_outage_after_the_all_clear_is_pushed_again(engine: Engine, task: str) -> None:
    notifier = RecordingNotifier()
    clock = Clock()
    pusher = pusher_for(engine, notifier, clock=clock)
    down = ('runner_unreachable', 'Marcel cannot reach the NUC', 'It keeps trying.')
    drive(pusher, lambda: pusher.broke(*down))
    clock.now += 60
    drive(pusher, lambda: pusher.resolved('runner_unreachable'))
    clock.now += 300  # down again five minutes later, well inside the hour
    drive(pusher, lambda: pusher.broke(*down))
    drive(pusher, lambda: pusher.resolved('runner_unreachable'))
    assert [n.title for _, n in notifier.sent] == [
        'Marcel cannot reach the NUC',
        'The NUC is back',
        'Marcel cannot reach the NUC',
        'The NUC is back',
    ]


def test_a_cause_without_an_all_clear_is_quiet(engine: Engine, task: str) -> None:
    notifier = RecordingNotifier()
    pusher = pusher_for(engine, notifier)
    bad = ('channel_bad_secret', 'Marcel cannot talk to its sessions', 'Redeploy to fix it.')
    drive(pusher, lambda: pusher.broke(*bad))
    drive(pusher, lambda: pusher.resolved('channel_bad_secret'))
    assert len(notifier.sent) == 1


def test_a_cause_without_an_all_clear_is_pushed_again_once_resolved(
    engine: Engine, task: str
) -> None:
    """Claude Code drifts from the pin, comes back, drifts again within the hour (S-04.17)."""
    notifier = RecordingNotifier()
    clock = Clock()
    pusher = pusher_for(engine, notifier, clock=clock)
    away = ('claude_updated', 'Claude Code was updated', 'New work waits.')
    drive(pusher, lambda: pusher.broke(*away))
    clock.now += 60
    drive(pusher, lambda: pusher.resolved('claude_updated'))  # back on the pin: quiet
    clock.now += 300
    drive(pusher, lambda: pusher.broke(*away))
    got = [(n.level, n.collapse_key) for _, n in notifier.sent]
    assert got == [('urgent', 'claude_updated'), ('urgent', 'claude_updated')]


def test_a_flaky_brain_still_pushes_once_per_hour(engine: Engine, task: str) -> None:
    """`brain_down` clears on an accepted start, before hello: resolving it keeps its hour."""
    notifier = RecordingNotifier()
    clock = Clock()
    pusher = pusher_for(engine, notifier, clock=clock)
    down = ('brain_down', 'Marcel cannot start', 'It keeps trying.')
    for _ in range(3):
        drive(pusher, lambda: pusher.broke(*down))
        clock.now += 60
        drive(pusher, lambda: pusher.resolved('brain_down'))
    assert len(notifier.sent) == 1


def test_the_runner_recovering_from_a_warning_replaces_the_alert(engine: Engine, task: str) -> None:
    notifier = RecordingNotifier()
    clock = Clock()
    pusher = pusher_for(engine, notifier, clock=clock)
    warning = ('runner_warning', 'The NUC has a problem', 'Disk full.')
    drive(pusher, lambda: pusher.broke(*warning))
    drive(pusher, lambda: pusher.resolved('runner_warning'))
    clock.now += 60  # a new warning within the hour is pushed again: the last one was cleared
    drive(pusher, lambda: pusher.broke(*warning))
    got = [(n.level, n.title, n.collapse_key, n.deep_link) for _, n in notifier.sent]
    assert got == [
        ('urgent', 'The NUC has a problem', 'runner_warning', 'marcel://status'),
        ('normal', 'The NUC is fine again', 'runner_warning', 'marcel://status'),
        ('urgent', 'The NUC has a problem', 'runner_warning', 'marcel://status'),
    ]


# Marcel's own notices --------------------------------------------------------------------------


def test_notices_are_capped_at_six_an_hour_per_agent(
    engine: Engine, task: str, caplog: pytest.LogCaptureFixture
) -> None:
    notifier = RecordingNotifier()
    clock = Clock()
    pusher = pusher_for(engine, notifier, clock=clock)
    results: list[bool] = []

    def notices(agent: str, count: int) -> None:
        for i in range(count):
            results.append(pusher.note(agent, f'msg_{agent}_{i}', 'Marcel', f'Notice {i}.'))
            clock.now += 60

    drive(pusher, lambda: notices('agt_marcel', NOTE_LIMIT + 2))
    assert results == [True] * NOTE_LIMIT + [False, False]
    assert len(notifier.sent) == NOTE_LIMIT
    assert all(n.category == 'note' for _, n in notifier.sent)
    assert 'was posted in the chat but not pushed' in caplog.text
    drive(pusher, lambda: notices('agt_other', 1))  # another agent has its own budget
    assert results[-1] is True
    clock.now += NOTE_SECONDS  # an hour after the first, the oldest have aged out
    drive(pusher, lambda: notices('agt_marcel', 1))
    assert results[-1] is True and len(notifier.sent) == NOTE_LIMIT + 2


def test_a_notice_that_cannot_be_built_is_logged_not_raised(
    engine: Engine, task: str, caplog: pytest.LogCaptureFixture
) -> None:
    notifier = RecordingNotifier()
    pusher = pusher_for(engine, notifier)
    drive(pusher, lambda: pusher.note('agt_marcel', 'not a valid id!', 'Marcel', 'Hi.'))
    assert notifier.sent == [] and 'Could not build the push' in caplog.text


# Who receives what -----------------------------------------------------------------------------


def test_task_pushes_go_only_to_phones_that_take_pushes(engine: Engine, task: str) -> None:
    # Which buddy a phone shows is test_notify_routing.py's (S-22.27); here: no target, no push.
    add_device(engine, 'dev_ipad', ntfy_topic='ipad')
    add_device(engine, 'dev_nothing', ntfy_topic=None)
    notifier = RecordingNotifier()
    pusher = pusher_for(engine, notifier)
    drive(pusher, lambda: pusher.on_state(task, 'done'))
    assert [d for d, _ in notifier.sent] == ['dev_ipad', 'dev_phone']


class Apns(RecordingNotifier):
    kind: Kind = 'apns'  # type: ignore[misc]


def test_each_device_gets_its_own_backend(
    engine: Engine, task: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    add_device(engine, 'dev_iphone', ntfy_topic=None, apns_token='apns-secret')
    ntfy = RecordingNotifier()
    pusher = pusher_for(engine, ntfy)
    drive(pusher, lambda: pusher.on_state(task, 'done'))
    assert [d for d, _ in ntfy.sent] == ['dev_phone']
    assert 'Device dev_iphone takes apns pushes' in caplog.text
    assert 'apns-secret' not in caplog.text

    apns = Apns()
    pusher = pusher_for(engine, ntfy, apns)
    drive(pusher, lambda: pusher.on_state(task, 'done'))
    assert [d for d, _ in apns.sent] == ['dev_iphone']


def test_no_device_means_nothing_sent(engine: Engine, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    add_agent(engine, owner='usr_owner')
    pusher = pusher_for(engine, RecordingNotifier())
    task = put_task(engine, TaskState.done)
    drive(pusher, lambda: pusher.on_state(task, 'done'))
    assert 'No phone that takes pushes is to get' in caplog.text


# Never raising ---------------------------------------------------------------------------------


def test_bad_input_is_logged_not_raised(
    engine: Engine, task: str, caplog: pytest.LogCaptureFixture
) -> None:
    notifier = RecordingNotifier()
    pusher = pusher_for(engine, notifier)
    inner: list[str] = []

    class Inner:
        def publish(self, type: str, data: dict) -> None:
            inner.append(type)

    publisher = pusher.task_publisher(Inner())

    def events() -> None:
        pusher.on_state('tsk_missing', 'done')
        pusher.on_state(task, 'not-a-state')
        publisher.publish('task.event', {'type': 'state'})  # no task id, no data
        pusher.broke('x', 'T' * 500, 'B')  # too long for the model? clipped, so fine
        pusher.digest('bad id with spaces', 'Digest', 'x')  # not a valid deep link

    drive(pusher, events)
    assert inner == ['task.event']
    assert 'unexpected shape' in caplog.text
    assert 'Could not work out the push for task' in caplog.text
    assert 'Could not push the digest' in caplog.text
    assert [n.title[-1] for _, n in notifier.sent] == ['…']


def test_failures_inside_the_event_hooks_are_logged(
    engine: Engine, task: str, caplog: pytest.LogCaptureFixture
) -> None:
    pusher = pusher_for(engine, RecordingNotifier())
    pusher.engine = None  # type: ignore[assignment]  # every database read now fails
    drive(pusher, lambda: pusher.approval_opened('apr_1'))
    pusher._open['runner_unreachable'] = 'marcel://status'
    pusher.push = None  # type: ignore[assignment]  # and every push now fails
    drive(pusher, lambda: pusher.broke('c', 'Title', 'Body'))
    drive(pusher, lambda: pusher.resolved('runner_unreachable'))
    assert 'Could not push approval apr_1' in caplog.text
    assert 'Could not push "Title"' in caplog.text
    assert 'Could not push the all-clear' in caplog.text


def test_a_push_from_a_worker_thread_reaches_the_loop(engine: Engine, task: str) -> None:
    notifier = RecordingNotifier()
    pusher = pusher_for(engine, notifier)

    async def go() -> None:
        pusher.start()
        thread = threading.Thread(target=lambda: pusher.on_state(task, 'done'))
        thread.start()
        await asyncio.to_thread(thread.join)
        for _ in range(50):
            if notifier.sent:
                break
            await asyncio.sleep(0.01)

    run(go())
    assert len(notifier.sent) == 1


def test_a_push_without_a_running_hub_is_dropped_and_logged(
    engine: Engine, task: str, caplog: pytest.LogCaptureFixture
) -> None:
    notifier = RecordingNotifier()
    pusher = pusher_for(engine, notifier)
    pusher.on_state(task, 'done')  # no event loop at all
    assert notifier.sent == []
    assert 'A push was dropped' in caplog.text


def test_closing_cancels_pending_pushes_and_closes_the_backends(engine: Engine, task: str) -> None:
    notifier = RecordingNotifier()
    pusher = pusher_for(engine, notifier)

    async def go() -> None:
        async def forever(_: float) -> None:
            await asyncio.sleep(3600)

        notifier.fail = PushFailed('down')
        pusher.sleep = forever
        pusher.on_state(task, 'done')
        await asyncio.sleep(0.01)
        await pusher.aclose()

    run(go())
    assert notifier.closed and notifier.sent == []


def test_capabilities_report_the_backends(engine: Engine) -> None:
    assert Pusher(engine).capabilities() == {'apns': False, 'ntfy': False}
    on = Pusher(engine, [FakeNtfy().notifier()], ntfy_server='https://ntfy.test')
    assert on.capabilities() == {'apns': False, 'ntfy': True, 'ntfy_server': 'https://ntfy.test'}
