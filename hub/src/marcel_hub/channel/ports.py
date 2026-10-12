"""What the channel gateway needs from other parts of the hub, as small interfaces.

- `Push`: the approval push. The real one is the notifier's `Pusher` (S-08.1); `LogPush` only
  records and logs, for tests and a hub built without it.
- `Artifacts`: where a worker's reported artifacts are stored. The real one is S-04.7's
  `ArtifactStore`, which copies files from the workers' folder.
- `Brains`: a new brain session that is not the conversation's brain yet (a rollover, S-06.2).
  The real one is `brain.BrainKeeper`.
"""

import logging
from typing import Any, Protocol

from marcel_hub.models import Artifact

log = logging.getLogger(__name__)


class Push(Protocol):
    def approval_opened(self, approval_id: str) -> None:
        """Push the new approval card (category `approval`, level `urgent`)."""
        ...


class LogPush:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def approval_opened(self, approval_id: str) -> None:
        self.sent.append(approval_id)
        log.info('Approval %s needs the owner. (No push is set up.)', approval_id)


class Artifacts(Protocol):
    def publish_reported(self, spec: dict[str, Any], task_id: str | None = None) -> Artifact:
        """Store one reported artifact (channel.schema.json `Artifact`) for the task, and record
        it on the task. Raises `ArtifactRefused` with a readable reason."""
        ...

    def version_target(self, artifact_id: str, task_id: str | None) -> Artifact:
        """The artifact a task may hand a new version of (B-34). Raises `UnknownArtifact` for an
        unknown id, one from another project, or a board."""
        ...


class Brains(Protocol):
    def said_hello(self, session_id: str) -> tuple[str, str] | None:
        """A session the conversation does not know said hello. If it is a new brain waiting to
        take over, and it may take over now, make it the conversation's brain and return
        (agent id, main conversation id); otherwise None (the hello is refused, and the channel
        server tries again in a few seconds)."""
        ...
