"""Push notifications (F08): the `Notifier` interface, the ntfy, APNs and relay backends and the
routing.

`install(app, ntfy_server, apns)` puts a `Pusher` on `app.state.pusher`. The relay backend is
always there: a phone brings its own relay target (S-22.15). The app factory hands its
`task_publisher()` to the task registry and the pusher itself to the runner watcher as its alerts.
"""

from fastapi import FastAPI

from marcel_hub.notify.apns import ApnsConfig, ApnsNotifier, ApnsSettingsError
from marcel_hub.notify.backends import Notifier, NtfyNotifier, PushFailed
from marcel_hub.notify.message import PushNotification
from marcel_hub.notify.pusher import Pusher, TaskPushes
from marcel_hub.notify.relay import RelayNotifier

__all__ = [
    'ApnsConfig',
    'ApnsNotifier',
    'ApnsSettingsError',
    'Notifier',
    'NtfyNotifier',
    'PushFailed',
    'PushNotification',
    'Pusher',
    'RelayNotifier',
    'TaskPushes',
    'install',
]


def install(app: FastAPI, ntfy_server: str | None = None, apns: ApnsConfig | None = None) -> Pusher:
    """Set up the relay backend, and the ones the settings name."""
    notifiers: list[Notifier] = [RelayNotifier(app.state.engine)]
    if ntfy_server:
        notifiers.append(NtfyNotifier(ntfy_server))
    if apns is not None:
        notifiers.append(ApnsNotifier(apns, app.state.engine))
    pusher = Pusher(app.state.engine, notifiers, ntfy_server=ntfy_server)
    app.state.pusher = pusher
    return pusher
