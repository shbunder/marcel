"""What the task registry needs from the rest of the hub, as small interfaces.

- `Runner`: start and stop sessions. The real client is S-04.5 (`runner_client.py`).
- `Outbox`: frames for a session or the brain, held until the session is connected. The real
  one is the channel gateway, S-04.6.
- `Publisher`: app WebSocket events (`task.updated`, `task.event`). The real one is S-04.3.

Until those exist the hub runs with the stand-ins below: `NoRunner` refuses every call with a
readable reason, `MemoryOutbox` keeps frames in memory, `NullPublisher` drops events.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

from marcel_hub.models import Task


class CapReached(Exception):
    """The runner's worker cap is full (`cap_reached`). Nothing was started."""


class StartFailed(Exception):
    """The session did not start. The message says why, in plain words."""


class RunnerUnavailable(Exception):
    """The runner cannot be reached. The message says why, in plain words."""


@dataclass(frozen=True)
class Started:
    session_id: str
    branch: str | None = None
    cloud_url: str | None = None  # a cloud session's claude.ai page (S-07.1)


class Runner(Protocol):
    async def start(self, task: Task, prompt: str) -> Started:
        """Start a session for `task`. Raises `CapReached`, `StartFailed` or `RunnerUnavailable`."""
        ...

    async def stop(self, session_id: str) -> None:
        """Stop a session. Raises `RunnerUnavailable`."""
        ...


class Outbox(Protocol):
    async def to_task(self, task_id: str, frame: dict[str, Any]) -> None:
        """Queue a `steer` frame (channel.schema.json, without `seq`) for a task's session."""
        ...

    async def to_brain(self, agent_id: str, frame: dict[str, Any]) -> None:
        """Queue a `system_event` frame (without `seq`) for the agent's brain."""
        ...


class Publisher(Protocol):
    def publish(self, type: str, data: dict[str, Any]) -> None:
        """Send an app event (events.schema.json); the publisher adds `id` and `at`."""
        ...


NO_RUNNER = 'The hub has no runner connection yet, so it cannot start or stop sessions.'


class NoRunner:
    async def start(self, task: Task, prompt: str) -> Started:
        raise RunnerUnavailable(NO_RUNNER)

    async def stop(self, session_id: str) -> None:
        raise RunnerUnavailable(NO_RUNNER)


@dataclass
class MemoryOutbox:
    tasks: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    brains: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    async def to_task(self, task_id: str, frame: dict[str, Any]) -> None:
        self.tasks.setdefault(task_id, []).append(frame)

    async def to_brain(self, agent_id: str, frame: dict[str, Any]) -> None:
        self.brains.setdefault(agent_id, []).append(frame)


class NullPublisher:
    def publish(self, type: str, data: dict[str, Any]) -> None:
        return None
