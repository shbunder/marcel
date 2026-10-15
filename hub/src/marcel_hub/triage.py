"""When the brain is woken (S-06.5, `plan/02-architecture.md` § Token economy).

Every brain turn spends plan usage, so the hub wakes the brain only when a decision needs it. This
module is the one place that decides. `wake_policy` is the table, row by row; `RulesTriage` is the
`Triage` the hub uses, and `record` logs each decision with its reason.

| Event | Brain turn? | Handled by |
|---|---|---|
| User message in main chat | yes | brain |
| User message in a side thread | yes | that side fork |
| Steer typed in a thread view, or "reply in thread" on a milestone card | no | the task |
| Approve / deny tap | no | hub → channel |
| Worker progress | no | hub stores it |
| Worker done | no | hub posts a milestone from the worker's own summary |
| Artifact published or updated; project board rebuilt; task moved to another project | no | hub |
| Worker failed, silent, or asked a question the user can't answer with a button | yes | brain |
| Digest due | yes | brain |
| "status?"-style message | yes in v1 | brain via `tasks_list` |

Three events are not rows of the table, and wake the brain because they are addressed to it: a task
handed back by the owner, a `brain` schedule firing, and the hand-over note a new brain gets at a
rollover.

The decisions are plain code: no model is called (Core principle: Claude Code does the thinking). A
decision model can replace `RulesTriage` later behind the same `Triage` protocol (S-16.2 measures
whether that would pay off).

**The decision log.** Every decision is one INFO line on the `marcel_hub.triage` logger: a plain
sentence, then ` | ` and the same decision as JSON (`at`, `event`, `decision`, `brain_turn`,
`target`, `reason`, `rule`, `agent_id`, `conversation_id`, `task_id`, `by`). `keep_log` also writes
these lines to `<data_dir>/triage.log` (rotated at 10 MB, five old files kept), whatever the
server's own logging is set to. S-16.2 counts them there: the JSON after the last ` | ` of each
line; `rule` names the table row (`status_question`, `worker_done`, …), so "status questions" is a
count of one value. A log file rather than a table: it needs no migration, and nothing in the hub
reads it back.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


class Kind(StrEnum):
    """What happened. One value per kind of event the hub may wake the brain for."""

    main_message = 'main_message'  # the owner wrote in the main conversation
    side_message = 'side_message'  # the owner wrote in a side thread (a fork of the brain)
    thread_message = 'thread_message'  # the owner wrote in a task's thread: a steer
    approval_answer = 'approval_answer'  # the owner tapped approve or deny
    worker_progress = 'worker_progress'  # a worker's started, progress or artifact report
    worker_done = 'worker_done'
    hub_update = 'hub_update'  # an artifact published or updated, a board rebuilt, a task moved
    worker_failed = 'worker_failed'
    worker_silent = 'worker_silent'
    worker_question = 'worker_question'  # asked something an approval button cannot answer
    digest_due = 'digest_due'
    handback = 'handback'  # the owner handed a task back to the brain
    schedule_fired = 'schedule_fired'  # a `brain` schedule
    rollover = 'rollover'  # what a new brain missed while it started
    agent_renamed = 'agent_renamed'  # the person renamed their helper (S-17.3)


@dataclass(frozen=True)
class Event:
    kind: Kind
    agent_id: str | None = None
    conversation_id: str | None = None  # where it was said; for task events, the main one if known
    task_id: str | None = None
    text: str = ''  # the owner's words, for a message


@dataclass(frozen=True)
class BrainTurn:
    """Wake a brain: the main brain, or the side fork of `conversation_id`. None: the agent's main
    conversation, which the caller looks up."""

    conversation_id: str | None
    reason: str
    rule: str


@dataclass(frozen=True)
class Direct:
    """No brain turn: the hub hands it straight to `target` (an Outbox target, `task:<id>`)."""

    target: str
    reason: str
    rule: str


@dataclass(frozen=True)
class Store:
    """No brain turn and nothing to send: the hub stores it and shows it in the app."""

    reason: str
    rule: str


Decision = BrainTurn | Direct | Store


class Triage(Protocol):
    name: str

    def decide(self, event: Event) -> Decision: ...


def task_target(task_id: str | None) -> str:
    """The Outbox target of a task (the same as `channel.outbox.task_target`)."""
    return f'task:{task_id}'


# A short message that only asks how things stand. Plain words, no model: S-16.2 counts these to
# see whether a cheaper decision could answer them without a brain turn.
# A friendly lead-in ("hey, …", "ok so …") may come first; "quick check …" is one on its own
# (both status questions missed on the NUC on 2026-10-14 began that way).
STATUS = re.compile(
    r'^\s*(?:(?:hey|hi|so|ok(?:ay)?)\b\W*)*'
    r"(?:status|any (?:news|updates?)|how(?:'s| is| are) (?:it|things|that|everything)"
    r"(?: going)?|what(?:'s| is) the status|where are we|(?:is it|are you) done|done yet"
    # "quick check" only as a question about how things stand, never "quick check the oven".
    r'|(?:a |just a )?quick (?:check|status|update)(?: (?:check|update))?(?:-in)?'
    r'(?=\s*(?:[:?,!.-]|$)|\s+(?:on|in)\b))\b',
    re.IGNORECASE,
)
STATUS_MAX = 60


def is_status_question(text: str) -> bool:
    return len(text) <= STATUS_MAX and STATUS.match(text) is not None


def wake_policy(event: Event) -> Decision:
    """The "When the brain is woken" table: one decision, with its reason, per event."""
    k, conv = event.kind, event.conversation_id
    if k == Kind.main_message:
        if is_status_question(event.text):
            return BrainTurn(
                conv,
                'A "status?" question: the brain answers it with tasks_list.',
                'status_question',
            )
        return BrainTurn(conv, 'The owner wrote in the main chat.', 'main_message')
    if k == Kind.side_message:
        return BrainTurn(conv, 'The owner wrote in a side thread: its fork answers.', 'side')
    if k == Kind.thread_message:
        return Direct(
            task_target(event.task_id),
            "The owner wrote in a task's thread: it goes straight to the task.",
            'thread_steer',
        )
    if k == Kind.approval_answer:
        return Direct(
            task_target(event.task_id),
            'The owner answered an approval card: the decision goes straight to the task.',
            'approval',
        )
    if k == Kind.worker_progress:
        return Store('A worker reported progress: the hub stores it for the task view.', 'progress')
    if k == Kind.worker_done:
        return Store(
            "A worker finished: the hub posts a milestone from the worker's own summary.",
            'worker_done',
        )
    if k == Kind.hub_update:
        return Store('The hub keeps artifacts, boards and projects up to date itself.', 'hub')
    if k in (Kind.worker_failed, Kind.worker_silent, Kind.worker_question):
        what = {
            Kind.worker_failed: 'failed',
            Kind.worker_silent: 'went silent',
            Kind.worker_question: 'asked a question a button cannot answer',
        }[k]
        return BrainTurn(
            conv, f'A worker {what}: the brain decides to retry, re-route or ask.', 'worker_trouble'
        )
    if k == Kind.digest_due:
        return BrainTurn(
            conv, "A digest is due: the brain writes it from the hub's facts.", 'digest'
        )
    # handback, schedule_fired, rollover, agent_renamed: not in the table; each is addressed to
    # the brain.
    return BrainTurn(conv, f'A {k.value.replace("_", " ")} is addressed to the brain.', k.value)


class RulesTriage:
    """The v1 `Triage`: the table, in plain code."""

    name = 'rules'

    def decide(self, event: Event) -> Decision:
        return wake_policy(event)


def record(triage: Triage, event: Event) -> Decision:
    """Decide with `triage` and log the decision (see "The decision log" above)."""
    decision = triage.decide(event)
    if isinstance(decision, BrainTurn):
        kind, target, said = 'brain_turn', decision.conversation_id, 'wakes the brain'
    elif isinstance(decision, Direct):
        kind, target, said = 'direct', decision.target, f'goes straight to {decision.target}'
    else:
        kind, target, said = 'store', None, 'is stored, no brain turn'
    line = {
        'at': datetime.now(UTC).isoformat(timespec='seconds'),
        'event': event.kind.value,
        'decision': kind,
        'brain_turn': kind == 'brain_turn',
        'target': target,
        'reason': decision.reason,
        'rule': decision.rule,
        'agent_id': event.agent_id,
        'conversation_id': event.conversation_id,
        'task_id': event.task_id,
        'by': triage.name,
    }
    log.info(
        '%s %s: %s | %s',
        event.kind.value.replace('_', ' ').capitalize(),
        said,
        decision.reason,
        json.dumps(line, sort_keys=True),
    )
    return decision


LOG_BYTES = 10 * 1024 * 1024
LOG_FILES = 5


class _DecisionFile(RotatingFileHandler):
    """The decision log's file; one per hub (a new app replaces the last one's)."""


def keep_log(path: Path) -> None:
    """Write every decision to `path` too, so S-16.2 can count them over weeks."""
    for old in [h for h in log.handlers if isinstance(h, _DecisionFile)]:
        log.removeHandler(old)
        old.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = _DecisionFile(path, maxBytes=LOG_BYTES, backupCount=LOG_FILES, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
