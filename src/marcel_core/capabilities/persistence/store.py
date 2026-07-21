"""MarcelStepStore — the harness StepStore over Marcel's conversation layout.

One process-wide store instance backs the ``StepPersistence`` capability for
every agent Marcel builds. It routes records by ``conversation_id``, which
Marcel formats as ``'{user_slug}:{channel}'`` (:func:`conversation_key`):

- **Turn runs** (a real conversation id) persist to the existing
  human-readable per-user tree: tool entries land in the segment JSONL via
  the same conversion the pre-harness runner used, and a small append-only
  ledger (``runs.jsonl`` in the conversation directory) records run
  lifecycle, model-request, and tool-effect lines — the crash-visibility
  record the old runner never had (Core principle: Recoverable).
- **Non-conversation runs** (jobs, subagents, the explain tier — no
  conversation id) are ignored entirely, matching their pre-harness
  behavior of not touching conversation storage.

Behavior parity with the pre-harness runner is deliberate: snapshot deltas
are **buffered per run and flushed only on ``run_completed``**, so a failed
tier attempt persists nothing and the succeeding attempt's entries are the
turn's record — exactly the old post-run extraction semantics. The runner
still owns the user-message and final-text appends (error tails and
explain-tier text never appear in agent messages).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    RunRecord,
    StepEvent,
    ToolEffectRecord,
)

from marcel_core.capabilities.persistence.extract import extract_tool_history, messages_to_model
from marcel_core.config import settings
from marcel_core.memory.summarizer import summarize_if_idle
from marcel_core.storage.conversation import (
    MAX_SUMMARY_CHARS,
    _conversation_dir,
    append_to_segment,
    load_latest_summary,
    read_active_segment,
)

log = logging.getLogger(__name__)

_LEDGER_FILENAME = 'runs.jsonl'


def conversation_key(user_slug: str, channel: str) -> str:
    """The conversation id Marcel passes to ``Agent.run_stream``."""
    return f'{user_slug}:{channel}'


def _parse_conversation(conversation_id: str | None) -> tuple[str, str] | None:
    """Split a Marcel conversation id into (user_slug, channel), else None."""
    if not conversation_id or ':' not in conversation_id:
        return None
    user_slug, _, channel = conversation_id.partition(':')
    if not user_slug or not channel:
        return None
    return user_slug, channel


def _ledger_path(user_slug: str, channel: str) -> Path:
    return _conversation_dir(user_slug, channel) / _LEDGER_FILENAME


def _append_ledger(user_slug: str, channel: str, obj: dict) -> None:
    path = _ledger_path(user_slug, channel)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(obj, ensure_ascii=False, default=str) + '\n')


def _read_ledger(user_slug: str, channel: str) -> list[dict]:
    path = _ledger_path(user_slug, channel)
    if not path.exists():
        return []
    lines: list[dict] = []
    for raw in path.read_text(encoding='utf-8').splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError:  # a torn write must not poison the ledger
            log.warning('skipping malformed ledger line in %s', path)
    return lines


@dataclass
class MarcelStepStore:
    """Implements the harness ``StepStore`` protocol over Marcel's tree."""

    # conversation_id -> number of ModelMessages served as history this turn
    _served: dict[str, int] = field(default_factory=dict)
    # run_id -> buffered tool-history entries awaiting run_completed
    _pending: dict[str, list] = field(default_factory=dict)
    # run_id -> conversation_id (for records that don't carry one)
    _run_conv: dict[str, str] = field(default_factory=dict)

    # -- Marcel-side context loading -------------------------------------

    async def load_context(self, user_slug: str, channel: str) -> list[ModelMessage]:
        """Build the model history for a turn (the old ``build_context``).

        1. Check for idle summarization (seals segment if idle)
        2. Load latest rolling summary from sealed segments
        3. Load + convert active segment messages
        4. Record the served length so snapshot deltas can be computed
        """
        # Reconcile any segments rotation queued but never summarized, so a
        # rotated segment folds into the rolling summary instead of vanishing
        # from context (FEAT-260707-89a886).
        from marcel_core.memory.summarizer import summarize_pending_segments

        folded = await summarize_pending_segments(user_slug, channel)
        if folded:
            log.info('%s-%s: folded %d rotated segment(s) into the rolling summary', user_slug, channel, folded)

        # Age fold (FEAT-260721-a59c21): week-stale active content drops to
        # gist + tail before the idle check even runs.
        from marcel_core.memory.summarizer import summarize_if_stale

        aged = await summarize_if_stale(user_slug, channel, settings.marcel_context_max_age_days)
        if aged:
            log.info('%s-%s: age folding completed before turn', user_slug, channel)

        idle_minutes = settings.marcel_idle_summarize_minutes
        from marcel_core.storage.conversation import has_active_content, is_idle

        # Idle is the session boundary regardless of whether a fold happens —
        # a short conversation whose messages all fit in the verbatim tail
        # skips the fold (FEAT-260721-a59c21) but must still re-classify its
        # tier from scratch (ISSUE-e0db47).
        was_idle = is_idle(user_slug, channel, idle_minutes) and has_active_content(user_slug, channel)
        summarized = await summarize_if_idle(user_slug, channel, idle_minutes)
        if summarized:
            log.info('%s-%s: idle summarization completed before turn', user_slug, channel)
        if was_idle:
            from marcel_core.storage.settings import clear_channel_tier

            clear_channel_tier(user_slug, channel)

        model_messages = self._conversation_state(user_slug, channel)
        self._served[conversation_key(user_slug, channel)] = len(model_messages)
        return model_messages

    def _conversation_state(self, user_slug: str, channel: str) -> list[ModelMessage]:
        """Summary-prefixed provider-shaped view of the conversation.

        Any segments still queued for summary (the summarizer failed to fold
        them — circuit breaker open or a transient error) are replayed **raw**
        ahead of the active segment, so their content is never absent from
        context even during a summarizer outage (FEAT-260707-89a886). Once the
        summarizer recovers, load_context folds them into the summary and this
        raw replay stops.
        """
        from marcel_core.storage.conversation import load_channel_meta, read_segment

        pending: list[ModelMessage] = []
        meta = load_channel_meta(user_slug, channel)
        if meta is not None and meta.pending_summary_segments:
            for segment_id in meta.pending_summary_segments:
                pending.extend(messages_to_model(read_segment(user_slug, channel, segment_id)))
            log.info(
                '%s-%s: replaying %d unfolded pending segment(s) raw (summarizer stalled)',
                user_slug,
                channel,
                len(meta.pending_summary_segments),
            )

        model_messages = pending + messages_to_model(read_active_segment(user_slug, channel))
        latest_summary = load_latest_summary(user_slug, channel)
        if latest_summary:
            summary_text = latest_summary.summary
            if len(summary_text) > MAX_SUMMARY_CHARS:
                summary_text = summary_text[:MAX_SUMMARY_CHARS] + '\n... (summary truncated)'
            model_messages.insert(
                0,
                ModelRequest(parts=[UserPromptPart(content=f'[Previous conversation summary: {summary_text}]')]),
            )
        return model_messages

    def reset(self) -> None:
        """Drop in-memory turn state (Terrarium seal / tests)."""
        self._served.clear()
        self._pending.clear()
        self._run_conv.clear()

    # -- StepStore protocol: write side ----------------------------------

    async def register_run(self, record: RunRecord) -> None:
        parsed = _parse_conversation(record.conversation_id)
        if parsed is None:
            return
        assert record.conversation_id is not None
        self._run_conv[record.run_id] = record.conversation_id
        entry = asdict(record)
        entry['record'] = 'run'
        _append_ledger(*parsed, entry)

    async def append_event(self, event: StepEvent) -> None:
        conversation_id = event.conversation_id or self._run_conv.get(event.run_id)
        parsed = _parse_conversation(conversation_id)
        if parsed is None:
            return
        entry = asdict(event)
        entry['record'] = 'event'
        _append_ledger(*parsed, entry)
        if event.kind == 'run_completed':
            self._flush(event.run_id, *parsed)
        elif event.kind == 'run_failed':
            dropped = self._pending.pop(event.run_id, None)
            if dropped:
                log.info('run %s failed — %d buffered tool entries dropped', event.run_id, len(dropped))

    async def record_tool_effect(self, record: ToolEffectRecord) -> None:
        parsed = _parse_conversation(self._run_conv.get(record.run_id))
        if parsed is None:
            return
        entry = asdict(record)
        entry['record'] = 'tool_effect'
        _append_ledger(*parsed, entry)

    async def save_snapshot(self, snapshot: ContinuableSnapshot) -> None:
        parsed = _parse_conversation(snapshot.conversation_id)
        if parsed is None:
            return
        assert snapshot.conversation_id is not None
        user_slug, _channel = parsed
        served = self._served.get(snapshot.conversation_id, 0)
        if len(snapshot.messages) < served:
            # Compaction may edit history in place but never drops messages
            # (ClearToolResults/Clamp preserve count). A shrunk list means an
            # assumption broke — persist nothing rather than something wrong.
            log.warning(
                'snapshot for %s has %d messages < %d served — skipping delta',
                snapshot.run_id,
                len(snapshot.messages),
                served,
            )
            return
        delta = list(snapshot.messages[served:])
        # Cumulative per run: the latest snapshot's delta supersedes earlier
        # ones (each snapshot carries the full history so far).
        self._pending[snapshot.run_id] = extract_tool_history(delta, user_slug, snapshot.conversation_id)

    def _flush(self, run_id: str, user_slug: str, channel: str) -> None:
        for entry in self._pending.pop(run_id, []):
            append_to_segment(user_slug, channel, entry)

    # -- StepStore protocol: read side -----------------------------------

    async def get_run(self, *, run_id: str) -> RunRecord | None:
        conversation_id = self._run_conv.get(run_id)
        parsed = _parse_conversation(conversation_id)
        if parsed is None:
            return None
        for line in _read_ledger(*parsed):
            if line.get('record') == 'run' and line.get('run_id') == run_id:
                return _run_from_line(line)
        return None

    async def list_runs(
        self,
        *,
        parent_run_id: str | None = None,
        conversation_id: str | None = None,
    ) -> list[RunRecord]:
        parsed = _parse_conversation(conversation_id)
        if parsed is None:
            # Without a conversation to scope the scan, Marcel has nothing
            # to enumerate (runs live per-conversation on disk).
            return []
        runs = [_run_from_line(line) for line in _read_ledger(*parsed) if line.get('record') == 'run']
        if parent_run_id is not None:
            runs = [r for r in runs if r.parent_run_id == parent_run_id]
        return runs

    async def list_events(self, *, run_id: str) -> list[StepEvent]:
        parsed = _parse_conversation(self._run_conv.get(run_id))
        if parsed is None:
            return []
        return [
            _event_from_line(line)
            for line in _read_ledger(*parsed)
            if line.get('record') == 'event' and line.get('run_id') == run_id
        ]

    async def latest_snapshot(self, *, run_id: str) -> ContinuableSnapshot | None:
        """Reconstruct the conversation's current provider-valid state.

        Marcel persists message *deltas* into segments rather than whole
        snapshots per boundary, so the latest continuable state is simply
        the conversation as stored — summary prefix plus active segment.
        """
        conversation_id = self._run_conv.get(run_id)
        parsed = _parse_conversation(conversation_id)
        if parsed is None:
            return None
        assert conversation_id is not None
        return ContinuableSnapshot(
            run_id=run_id,
            step_index=0,
            messages=self._conversation_state(*parsed),
            conversation_id=conversation_id,
        )

    async def get_tool_effect(self, *, run_id: str, tool_call_id: str) -> ToolEffectRecord | None:
        effects = await self._effects_for_run(run_id)
        return effects.get(tool_call_id)

    async def list_unresolved_tool_effects(self, *, run_id: str) -> list[ToolEffectRecord]:
        effects = await self._effects_for_run(run_id)
        return [e for e in effects.values() if e.status == 'started']

    async def _effects_for_run(self, run_id: str) -> dict[str, ToolEffectRecord]:
        parsed = _parse_conversation(self._run_conv.get(run_id))
        if parsed is None:
            return {}
        latest: dict[str, ToolEffectRecord] = {}  # last line wins per call id
        for line in _read_ledger(*parsed):
            if line.get('record') == 'tool_effect' and line.get('run_id') == run_id:
                record = _effect_from_line(line)
                latest[record.tool_call_id] = record
        return latest


def _parse_ts(value: object) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _run_from_line(line: dict) -> RunRecord:
    return RunRecord(
        run_id=line.get('run_id', ''),
        conversation_id=line.get('conversation_id'),
        parent_run_id=line.get('parent_run_id'),
        agent_name=line.get('agent_name'),
        metadata=line.get('metadata') or {},
        started_at=_parse_ts(line.get('started_at')),
    )


def _event_from_line(line: dict) -> StepEvent:
    return StepEvent(
        run_id=line.get('run_id', ''),
        kind=line.get('kind', 'run_started'),
        step_index=int(line.get('step_index') or 0),
        timestamp=_parse_ts(line.get('timestamp')),
        conversation_id=line.get('conversation_id'),
        parent_run_id=line.get('parent_run_id'),
        agent_name=line.get('agent_name'),
        tool_call_id=line.get('tool_call_id'),
        tool_name=line.get('tool_name'),
        error=line.get('error'),
        metadata=line.get('metadata') or {},
    )


def _effect_from_line(line: dict) -> ToolEffectRecord:
    return ToolEffectRecord(
        tool_call_id=line.get('tool_call_id', ''),
        tool_name=line.get('tool_name', ''),
        run_id=line.get('run_id', ''),
        status=line.get('status', 'started'),
        started_at=_parse_ts(line.get('started_at')),
        ended_at=_parse_ts(line['ended_at']) if line.get('ended_at') else None,
        idempotency_key=line.get('idempotency_key'),
        effect_summary=line.get('effect_summary'),
    )


_store: MarcelStepStore | None = None


def persistence_store() -> MarcelStepStore:
    """The process-wide store instance (reset per scenario in the Terrarium)."""
    global _store
    if _store is None:
        _store = MarcelStepStore()
    return _store
