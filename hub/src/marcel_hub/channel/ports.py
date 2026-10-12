"""What the channel gateway needs from stories that are not built yet, as small interfaces.

- `Push`: the approval push notification. The real one is the notifier, S-08.1; until then
  `LogPush` writes the push to the log.
- `Artifacts`: where a worker's reported artifacts are stored. The real store (blobs, dashboard
  checks, versions) is S-04.7 and S-04.11; until then `RowArtifacts` writes the `artifact` row.
"""

import logging
from typing import Any, Protocol

from sqlmodel import Session

from marcel_hub.models import Artifact, ArtifactKind, Task

log = logging.getLogger(__name__)


class Push(Protocol):
    def approval(self, approval: dict[str, Any], task: Task) -> None:
        """Send the `approval` push (`PushNotification`, category `approval`, level `urgent`)."""
        ...


class LogPush:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def approval(self, approval: dict[str, Any], task: Task) -> None:
        self.sent.append(approval['id'])
        log.info(
            'Push (not sent until the notifier exists): "%s" needs your approval: %s',
            task.title,
            approval['summary'],
        )


class Artifacts(Protocol):
    def store(self, engine: Any, task: Task, artifact: dict[str, Any]) -> Artifact:
        """Store one reported artifact (channel.schema.json `Artifact`) for `task`."""
        ...


class RowArtifacts:
    """Stores the artifact row only; a `path` is kept in `meta_json` for S-04.7 to copy."""

    def store(self, engine: Any, task: Task, artifact: dict[str, Any]) -> Artifact:
        meta = {k: artifact[k] for k in ('path', 'summary', 'artifact_id') if k in artifact}
        row = Artifact(
            task_id=task.id,
            kind=ArtifactKind(artifact['kind']),
            title=artifact['title'],
            url=artifact.get('url'),
            meta_json=meta,
        )
        with Session(engine) as s:
            s.add(row)
            s.commit()
            s.refresh(row)
        return row
