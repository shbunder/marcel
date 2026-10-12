"""Push notifications (F08): the `Notifier` interface, the ntfy backend and the routing policy.

`install(app, ntfy_server)` puts a `Pusher` on `app.state.pusher`. The app factory hands its
`task_publisher()` to the task registry and the pusher itself to the runner watcher as its alerts.
"""

from fastapi import FastAPI

from marcel_hub.notify.backends import Notifier, NtfyNotifier, PushFailed
from marcel_hub.notify.message import PushNotification
from marcel_hub.notify.pusher import Pusher, TaskPushes

__all__ = [
    'Notifier',
    'NtfyNotifier',
    'PushFailed',
    'PushNotification',
    'Pusher',
    'TaskPushes',
    'install',
]


def install(app: FastAPI, ntfy_server: str | None = None) -> Pusher:
    """Set up the backends the settings name. With none, events are routed and nothing is sent."""
    notifiers: list[Notifier] = [NtfyNotifier(ntfy_server)] if ntfy_server else []
    pusher = Pusher(app.state.engine, notifiers, ntfy_server=ntfy_server)
    app.state.pusher = pusher
    return pusher
