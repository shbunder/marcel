"""The task state machine from `contracts/task-states.md`.

Every allowed transition is in `TRANSITIONS`; anything else is illegal and refused. `None` as the
`from` state means "a new task". A test parses the contract's table and checks this one against it.
"""

import logging

from marcel_hub.models import TaskState

log = logging.getLogger(__name__)

S = TaskState

_ROWS: list[tuple[tuple[TaskState | None, ...], tuple[TaskState, ...]]] = [
    ((None,), (S.starting,)),
    ((None,), (S.queued,)),
    ((S.queued,), (S.starting,)),
    ((S.queued,), (S.stopped,)),
    ((S.starting,), (S.working,)),
    ((S.starting,), (S.needs_you,)),
    ((S.starting,), (S.failed,)),
    ((S.working, S.silent), (S.needs_you,)),
    ((S.needs_you,), (S.working,)),
    ((S.starting, S.working, S.needs_you, S.silent), (S.done,)),
    ((S.working, S.needs_you, S.silent), (S.failed,)),
    ((S.starting, S.working, S.needs_you, S.silent, S.unknown), (S.stopped,)),
    ((S.starting, S.working), (S.silent,)),
    ((S.silent,), (S.working,)),
    ((S.starting, S.working, S.needs_you, S.silent), (S.unknown,)),
    ((S.unknown,), (S.starting, S.working, S.needs_you, S.done, S.failed, S.stopped)),
    ((S.done,), (S.working,)),
    ((S.failed,), (S.starting,)),
]

TRANSITIONS: frozenset[tuple[TaskState | None, TaskState]] = frozenset(
    (src, dst) for sources, targets in _ROWS for src in sources for dst in targets
)

# States after which the task holds no worker slot and takes no steer.
FINISHED = frozenset({S.done, S.failed, S.stopped})


class IllegalTransition(Exception):
    """A transition the state machine does not allow. Nothing was changed."""

    def __init__(self, task_id: str, src: TaskState | None, dst: TaskState, reason: str) -> None:
        self.task_id, self.src, self.dst, self.reason = task_id, src, dst, reason
        before = src.value if src else 'new'
        super().__init__(
            f'Task {task_id} cannot go from {before} to {dst.value} ({reason}). '
            'See contracts/task-states.md.'
        )


def check(task_id: str, src: TaskState | None, dst: TaskState, reason: str) -> None:
    """Raise `IllegalTransition`, and log it, unless `src → dst` is in the table."""
    if (src, dst) not in TRANSITIONS:
        err = IllegalTransition(task_id, src, dst, reason)
        log.warning('Refused a task transition: %s', err)
        raise err
