"""Idle summarization — seals and summarizes conversation segments.

When a conversation is inactive for longer than the configured threshold
(default: 60 minutes), the active segment is sealed, tool results stripped,
and a concise summary generated via Haiku. The summary is chained: each new
summary incorporates its predecessor, creating a rolling "gist" of the
entire conversation history that naturally fades old details.

Triggers:
- On next message after idle period (inline, before processing)
- Background asyncio task (every 15 minutes, all channels)
- Manual /forget command
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic_ai import Agent

from marcel_core.storage.conversation import (
    SegmentSummary,
    has_active_content,
    is_idle,
    load_channel_meta,
    load_latest_summary,
    read_segment,
    save_summary,
    seal_active_segment,
    strip_tool_results_from_segment,
)
from marcel_core.storage.history import HistoryMessage

log = logging.getLogger(__name__)

# Summarization model (fast and cheap).
SUMMARIZATION_MODEL = 'anthropic:claude-haiku-4-5-20251001'

# Circuit breaker: max consecutive failures before disabling.
MAX_SUMMARIZATION_FAILURES = 3

_SUMMARIZATION_SYSTEM_PROMPT = """\
You are summarizing a conversation segment for long-term memory compression.
You will be given a sequence of messages between a user and Marcel (a personal
assistant butler). Your task is to create a concise summary (200-500 words)
that captures:

- Key topics discussed and decisions made
- Action items, commitments, or follow-ups
- Important facts the user shared (names, preferences, dates)
- The emotional tone and relationship context

## Identifier preservation
Always preserve these verbatim — they cannot be reconstructed:
- File paths, URLs, email addresses
- Names of people, places, organizations
- Dates, times, and deadlines
- UUIDs, hashes, account numbers, or reference IDs

## Style
Write in past tense, third person ("The user asked...", "Marcel helped...").
Be concise but complete — this summary replaces the original messages.
Do not include meta-commentary like "Here is the summary:".

Return ONLY the summary text."""

_CHAINED_PROMPT_PREFIX = """\
Here is the rolling summary of the conversation BEFORE this segment:

---
{previous_summary}
---

Now summarize the NEW segment below, incorporating relevant prior context.
Let old details naturally fade unless they are still actionable or referenced.
The combined summary should capture the full conversation arc but emphasize
recent events.\n\n"""


@dataclass
class SummarizationState:
    """Tracks summarization attempts per channel."""

    consecutive_failures: int = 0
    last_attempt: datetime | None = None


# In-memory state tracker: (user_slug, channel) -> SummarizationState
_summarization_state: dict[tuple[str, str], SummarizationState] = {}


def _get_state(user_slug: str, channel: str) -> SummarizationState:
    key = (user_slug, channel)
    if key not in _summarization_state:
        _summarization_state[key] = SummarizationState()
    return _summarization_state[key]


async def summarize_if_idle(
    user_slug: str,
    channel: str,
    idle_minutes: int = 60,
) -> bool:
    """Check if the channel is idle and summarize if so.

    Called at the start of each turn (before processing the new message).
    Returns True if summarization was performed.
    """
    if not is_idle(user_slug, channel, idle_minutes):
        return False
    if not has_active_content(user_slug, channel):
        return False
    return await summarize_active_segment(user_slug, channel, trigger='idle')


async def summarize_active_segment(
    user_slug: str,
    channel: str,
    trigger: str = 'manual',
) -> bool:
    """Seal the active segment and generate a summary.

    This is the main summarization entry point. Used by:
    - summarize_if_idle (trigger='idle')
    - /forget command (trigger='manual')
    - marcel(action="compact") tool (trigger='manual')

    Returns True if summarization succeeded.
    """
    state = _get_state(user_slug, channel)

    # Circuit breaker
    if state.consecutive_failures >= MAX_SUMMARIZATION_FAILURES:
        log.warning(
            '%s-%s: circuit breaker active after %d failures',
            user_slug,
            channel,
            state.consecutive_failures,
        )
        return False

    if not has_active_content(user_slug, channel):
        log.debug('%s-%s: no active content to summarize', user_slug, channel)
        return False

    meta = load_channel_meta(user_slug, channel)
    if meta is None:
        return False

    # Read messages from active segment before sealing
    segment_id = meta.active_segment
    messages = read_segment(user_slug, channel, segment_id)
    if not messages:
        return False

    log.info(
        '%s-%s: starting %s summarization segment=%s (%d messages)',
        user_slug,
        channel,
        trigger,
        segment_id,
        len(messages),
    )

    # Seal the active segment and open a new one, then summarize the sealed
    # file through the shared helper (also used for rotation-pending segments).
    try:
        sealed_id, _meta = seal_active_segment(user_slug, channel)
    except Exception:
        log.exception('%s-%s: sealing failed', user_slug, channel)
        state.consecutive_failures += 1
        state.last_attempt = datetime.now(tz=timezone.utc)
        return False
    return await _summarize_segment_file(user_slug, channel, sealed_id, trigger, state)


async def _summarize_segment_file(
    user_slug: str,
    channel: str,
    segment_id: str,
    trigger: str,
    state: 'SummarizationState',
) -> bool:
    """Summarize one already-sealed segment file, chaining the rolling summary.

    Shared by :func:`summarize_active_segment` (which seals first) and
    :func:`summarize_pending_segments` (rotation queue, already sealed by the
    rotation). Strips tool results, generates a Haiku summary that chains the
    latest one, and saves it. Updates the circuit-breaker state.
    """
    # Idempotency: a crash between save_summary and the pending dequeue would
    # otherwise re-summarize an already-summarized segment, chaining it onto
    # its own summary. Skip (and let the caller dequeue) if one exists.
    from marcel_core.storage.conversation import load_summary

    if load_summary(user_slug, channel, segment_id) is not None:
        log.debug('%s-%s: %s already summarized — skipping', user_slug, channel, segment_id)
        return True

    messages = read_segment(user_slug, channel, segment_id)
    if not messages:
        return False
    try:
        stripped = strip_tool_results_from_segment(user_slug, channel, segment_id)
        log.debug('%s-%s: stripped %d tool results from %s', user_slug, channel, stripped, segment_id)

        previous_summary = load_latest_summary(user_slug, channel)
        summary_text = await _generate_summary(messages, previous_summary)

        timestamps = [m.timestamp for m in messages if m.timestamp]
        time_from = min(timestamps) if timestamps else datetime.now(tz=timezone.utc)
        time_to = max(timestamps) if timestamps else datetime.now(tz=timezone.utc)

        save_summary(
            user_slug,
            channel,
            SegmentSummary(
                segment_id=segment_id,
                created_at=datetime.now(tz=timezone.utc),
                trigger=trigger,
                message_count=len(messages),
                time_span_from=time_from,
                time_span_to=time_to,
                summary=summary_text,
                previous_summary_segment=(previous_summary.segment_id if previous_summary else None),
            ),
        )
        state.consecutive_failures = 0
        state.last_attempt = datetime.now(tz=timezone.utc)
        log.info(
            '%s-%s: %s summarization complete — %d messages → %d char summary',
            user_slug,
            channel,
            trigger,
            len(messages),
            len(summary_text),
        )
        return True
    except Exception:
        log.exception('%s-%s: summarization failed for %s', user_slug, channel, segment_id)
        state.consecutive_failures += 1
        state.last_attempt = datetime.now(tz=timezone.utc)
        return False


async def summarize_pending_segments(user_slug: str, channel: str) -> int:
    """Summarize any segments rotation queued but never summarized.

    Rotation (size/budget) hands the outgoing segment to
    ``meta.pending_summary_segments`` synchronously; this reconciles that queue
    into the rolling summary before a turn's context is built, so a rotated
    segment is never silently absent from context (FEAT-260707-89a886).
    Processes oldest-first so the summaries chain correctly. Returns how many
    were summarized.
    """
    from marcel_core.storage.conversation import load_channel_meta, save_channel_meta

    meta = load_channel_meta(user_slug, channel)
    if meta is None or not meta.pending_summary_segments:
        return 0

    state = _get_state(user_slug, channel)
    if state.consecutive_failures >= MAX_SUMMARIZATION_FAILURES:
        return 0

    done = 0
    for segment_id in list(meta.pending_summary_segments):
        ok = await _summarize_segment_file(user_slug, channel, segment_id, 'rotate', state)
        # Re-load meta each iteration: _summarize_segment_file does not touch it,
        # but keeping the dequeue authoritative against concurrent appends.
        meta = load_channel_meta(user_slug, channel)
        if meta is None:
            break
        if ok and segment_id in meta.pending_summary_segments:
            meta.pending_summary_segments.remove(segment_id)
            save_channel_meta(user_slug, channel, meta)
            done += 1
        elif not ok:
            break  # circuit-breaker / transient — retry the rest next turn
    return done


async def _generate_summary(
    messages: list[HistoryMessage],
    previous_summary: SegmentSummary | None = None,
) -> str:
    """Use Haiku to generate a summary of conversation messages."""
    # Build text representation (tool results already compact for older msgs)
    lines: list[str] = []
    for msg in messages:
        if msg.role == 'user':
            lines.append(f'User: {msg.text or "(no text)"}')
        elif msg.role == 'assistant':
            if msg.tool_calls:
                tool_names = ', '.join(tc.name for tc in msg.tool_calls)
                lines.append(f'Marcel: [called tools: {tool_names}]')
            if msg.text:
                lines.append(f'Marcel: {msg.text}')
        elif msg.role == 'tool':
            # Compact: just tool name + truncated result
            tool_label = msg.tool_name or 'tool'
            text = msg.text or '(no output)'
            if len(text) > 300:
                text = text[:300] + '...'
            lines.append(f'  → {tool_label}: {text}')
        elif msg.role == 'system':
            if msg.text:
                lines.append(f'[System: {msg.text}]')

    conversation_text = '\n'.join(lines)

    # Build prompt with optional chaining
    if previous_summary:
        prompt = _CHAINED_PROMPT_PREFIX.format(previous_summary=previous_summary.summary)
        prompt += f'Conversation segment to summarize:\n\n{conversation_text}'
    else:
        prompt = f'Summarize this conversation:\n\n{conversation_text}'

    summarizer: Agent[None, str] = Agent(
        SUMMARIZATION_MODEL,
        system_prompt=_SUMMARIZATION_SYSTEM_PROMPT,
        retries=2,
    )

    result = await summarizer.run(prompt)
    return result.output.strip()


def reset_summarization_state(user_slug: str, channel: str) -> None:
    """Reset summarization state (for testing or after manual intervention)."""
    key = (user_slug, channel)
    if key in _summarization_state:
        del _summarization_state[key]
