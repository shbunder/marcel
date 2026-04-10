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

from marcel_core.memory.conversation import (
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
from marcel_core.memory.history import HistoryMessage

log = logging.getLogger(__name__)

# Summarization model (fast and cheap).
SUMMARIZATION_MODEL = 'claude-haiku-4-5-20251001'

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

    try:
        # 1. Seal the active segment and open a new one
        sealed_id, meta = seal_active_segment(user_slug, channel)

        # 2. Strip tool results from the sealed segment
        stripped = strip_tool_results_from_segment(user_slug, channel, sealed_id)
        log.debug('%s-%s: stripped %d tool results from %s', user_slug, channel, stripped, sealed_id)

        # 3. Generate summary via Haiku
        previous_summary = load_latest_summary(user_slug, channel)
        summary_text = await _generate_summary(messages, previous_summary)

        # 4. Compute time span
        timestamps = [m.timestamp for m in messages if m.timestamp]
        time_from = min(timestamps) if timestamps else datetime.now(tz=timezone.utc)
        time_to = max(timestamps) if timestamps else datetime.now(tz=timezone.utc)

        # 5. Save summary
        summary = SegmentSummary(
            segment_id=sealed_id,
            created_at=datetime.now(tz=timezone.utc),
            trigger=trigger,
            message_count=len(messages),
            time_span_from=time_from,
            time_span_to=time_to,
            summary=summary_text,
            previous_summary_segment=(previous_summary.segment_id if previous_summary else None),
        )
        save_summary(user_slug, channel, summary)

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
        log.exception('%s-%s: summarization failed', user_slug, channel)
        state.consecutive_failures += 1
        state.last_attempt = datetime.now(tz=timezone.utc)
        return False


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
