"""Which events push, and what each push says (B-16).

| Event | Category | Level | Deep link |
|---|---|---|---|
| an open approval | approval | urgent | `marcel://approval/<id>` |
| a task `needs_you` without an approval (a question, a login) | task | urgent | the task |
| a task `done` or `failed` | task | normal | the task |
| a task `silent` | broke | urgent | the task |
| the runner down or warning | broke | urgent | `marcel://status` |
| an urgent push lost | broke | urgent | what it was about |
| a digest | digest | normal | the digest's message |
| Marcel's own notice (the brain's `notify` tool) | note | normal or urgent | its message |

A push about no one task (the runner) opens the app's status screen, `marcel://status`.

Who gets it is `pusher.Pusher._devices`'s: a buddy's task, approval, notice or digest goes to the
phones showing that buddy; the runner, a lost push, usage, a redeploy or backup, a logged-out
Claude and a self-change go to every phone (S-22.27, B-49).

Anything else (progress, `working`, `queued`, `unknown`, `stopped`) sends nothing. Texts are read on
a lock screen: the outcome first, in short plain words.
"""

from marcel_hub.models import Approval, Task, TaskState
from marcel_hub.notify.message import (
    BODY_MAX,
    TITLE_MAX,
    PushNotification,
    approval_link,
    clip,
    message_link,
    task_link,
)

# Task states that push; every other state change is quiet.
PUSHING_STATES = frozenset(
    {TaskState.needs_you, TaskState.done, TaskState.failed, TaskState.silent}
)


def _push(title: str, body: str, **fields: str | None) -> PushNotification:
    return PushNotification(title=clip(title, TITLE_MAX), body=clip(body, BODY_MAX), **fields)  # type: ignore[arg-type]


def for_approval(task: Task, approval: Approval, agent: str = 'Marcel') -> PushNotification:
    return _push(
        f'{agent} needs your OK',
        f'{task.title}: {approval.summary}',
        category='approval',
        level='urgent',
        deep_link=approval_link(approval.id),
        collapse_key=task.id,
        task_id=task.id,
        approval_id=approval.id,
    )


def for_state(
    task: Task, to: TaskState, reason: str = '', agent: str = 'Marcel'
) -> PushNotification | None:
    """The push for a task that just moved to `to`, or None when that change is quiet.

    An open approval is not handled here: see `for_approval`.
    """
    common = {'deep_link': task_link(task.id), 'collapse_key': task.id, 'task_id': task.id}
    if to == TaskState.needs_you:
        waiting = task.waiting_for or 'It is waiting for your answer.'
        return _push(
            f'{agent} needs you',
            f'{task.title}: {waiting}',
            category='task',
            level='urgent',
            **common,
        )
    if to == TaskState.done:
        result = task.summary or 'Finished. Tap to see what it did.'
        return _push(f'Done: {task.title}', result, category='task', level='normal', **common)
    if to == TaskState.failed:
        why = task.summary or reason or 'It stopped with an error.'
        return _push(f'Failed: {task.title}', why, category='task', level='normal', **common)
    if to == TaskState.silent:
        return _push(
            'A task went quiet',
            f'"{task.title}" has not moved for a while. Tap to check on it.',
            category='broke',
            level='urgent',
            **common,
        )
    return None


def for_broke(
    cause: str, title: str, body: str, deep_link: str, task_id: str | None = None
) -> PushNotification:
    """Something broke that is not one task's state: the runner, a failed push."""
    return _push(
        title,
        body,
        category='broke',
        level='urgent',
        deep_link=deep_link,
        collapse_key=cause,
        task_id=task_id,
    )


def for_fixed(cause: str, title: str, body: str, deep_link: str) -> PushNotification:
    """The all-clear for an earlier "broke" push. It replaces it on the phone (same key)."""
    return _push(
        title, body, category='broke', level='normal', deep_link=deep_link, collapse_key=cause
    )


def for_failed_push(failed: PushNotification) -> PushNotification:
    """An urgent push did not arrive: say so, pointing at what it was about."""
    return _push(
        'A notification did not arrive',
        f'Marcel could not send "{failed.title}". Open the app to see it.',
        category='broke',
        level='urgent',
        deep_link=failed.deep_link,
        collapse_key='push_failed',
        task_id=failed.task_id,
    )


def for_note(message_id: str, title: str, body: str, level: str) -> PushNotification:
    """Marcel's own notice (the brain's `notify` tool), posted as a message: the push opens it.
    Each notice is its own (keyed by its message), so none replaces another on the phone."""
    return _push(
        title,
        body,
        category='note',
        level=level,
        deep_link=message_link(message_id),
        collapse_key=message_id,
    )


def for_digest(message_id: str, title: str, body: str) -> PushNotification:
    """A scheduled digest, posted as a message: the push opens that message."""
    return _push(
        title,
        body,
        category='digest',
        level='normal',
        deep_link=message_link(message_id),
        collapse_key='digest',
    )
