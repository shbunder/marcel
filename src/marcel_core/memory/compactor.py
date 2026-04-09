"""Auto-compaction for JSONL conversation history.

When conversation history exceeds a token threshold (default: 75k), automatically:
1. Preserve the last 5 turns verbatim (recent context is critical)
2. Summarize older turns into 1-2 paragraphs using a fast model
3. Prepend the summary as a synthetic "system" message
4. Archive the original full history

Circuit breaker: stops after 3 consecutive failures.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic_ai import Agent

from marcel_core.memory.history import (
    HistoryMessage,
    append_message,
    count_tokens_estimate,
    create_compaction_summary,
    read_recent_turns,
)

log = logging.getLogger(__name__)

# Compaction threshold in estimated tokens
COMPACTION_THRESHOLD = 75_000

# Number of recent turns to preserve verbatim
PRESERVE_RECENT_TURNS = 5

# Compaction model (fast and cheap)
COMPACTION_MODEL = 'claude-haiku-4-5-20251001'

# Circuit breaker: max consecutive failures before giving up
MAX_COMPACTION_FAILURES = 3

_COMPACTION_SYSTEM_PROMPT = """\
You are summarizing a conversation history for context compression. \
You will be given a sequence of messages between a user and Marcel (an AI assistant). \
Your task is to create a concise summary (1-2 paragraphs) that captures:

- Key topics discussed
- Important facts or decisions made
- Outstanding questions or follow-ups
- Relevant context for future turns

The summary will be prepended to the conversation, so recent messages will still be visible. \
Focus on what's important to retain for continuity.

Return ONLY the summary text, no preamble or meta-commentary."""


@dataclass
class CompactionState:
    """Tracks compaction attempts and failures per conversation."""

    conversation_id: str
    consecutive_failures: int = 0
    last_compaction: datetime | None = None


# In-memory state tracker (conversation_id -> CompactionState)
_compaction_state: dict[str, CompactionState] = {}


async def check_and_compact(
    user_slug: str,
    conversation_id: str,
    force: bool = False,
) -> bool:
    """Check if compaction is needed and perform it if so.

    Args:
        user_slug: The user's slug.
        conversation_id: The conversation to check.
        force: If True, compact regardless of threshold.

    Returns:
        True if compaction was performed, False otherwise.
    """
    state = _compaction_state.get(conversation_id)
    if state is None:
        state = CompactionState(conversation_id=conversation_id)
        _compaction_state[conversation_id] = state

    # Circuit breaker check
    if state.consecutive_failures >= MAX_COMPACTION_FAILURES:
        log.warning(
            '[compaction] Circuit breaker active for %s after %d failures',
            conversation_id,
            state.consecutive_failures,
        )
        return False

    # Load full history for this conversation
    messages = read_recent_turns(user_slug, conversation_id, num_turns=999)

    # Check token threshold
    token_estimate = count_tokens_estimate(messages)
    if not force and token_estimate < COMPACTION_THRESHOLD:
        log.debug(
            '[compaction] %s at %d tokens, below threshold %d',
            conversation_id,
            token_estimate,
            COMPACTION_THRESHOLD,
        )
        return False

    log.info(
        '[compaction] Starting compaction for %s (%d tokens)',
        conversation_id,
        token_estimate,
    )

    try:
        # Split: recent turns to preserve, older turns to summarize
        # Count user messages (turn starts)
        user_msg_indices = [i for i, m in enumerate(messages) if m.role == 'user']

        if len(user_msg_indices) <= PRESERVE_RECENT_TURNS:
            # Not enough history to compact
            log.debug('[compaction] Only %d turns, skipping', len(user_msg_indices))
            return False

        # Preserve last N turns
        preserve_start_idx = user_msg_indices[-PRESERVE_RECENT_TURNS]
        to_summarize = messages[:preserve_start_idx]
        to_preserve = messages[preserve_start_idx:]

        # Generate summary
        summary_text = await _summarize_messages(to_summarize)

        # Create summary message
        summary_msg = create_compaction_summary(to_summarize, summary_text, conversation_id)

        # TODO: Archive the full history before compaction
        # For now, we just log the compaction event

        log.info(
            '[compaction] Compacted %d messages into summary (%d chars), preserving %d messages',
            len(to_summarize),
            len(summary_text),
            len(to_preserve),
        )

        # In a real implementation, we'd:
        # 1. Write summary_msg + to_preserve to a new history file
        # 2. Move the old file to archive
        # For now, just append the summary (in Phase 4 we'll implement full archival)
        append_message(user_slug, summary_msg)

        state.consecutive_failures = 0
        state.last_compaction = datetime.now(tz=timezone.utc)
        return True

    except Exception:
        log.exception('[compaction] Failed for %s', conversation_id)
        state.consecutive_failures += 1
        return False


async def _summarize_messages(messages: list[HistoryMessage]) -> str:
    """Use a fast model to summarize a list of messages."""
    # Build a text representation of the conversation
    lines: list[str] = []
    for msg in messages:
        role_label = msg.role.capitalize()
        if msg.role == 'assistant':
            role_label = 'Marcel'
        text = msg.text or '(no text)'
        lines.append(f'{role_label}: {text}')

    conversation_text = '\n\n'.join(lines)
    prompt = f'Summarize this conversation:\n\n{conversation_text}'

    # Create summarizer agent
    summarizer: Agent[None, str] = Agent(
        COMPACTION_MODEL,
        system_prompt=_COMPACTION_SYSTEM_PROMPT,
        retries=2,
    )

    result = await summarizer.run(prompt)
    return result.data.strip()


def reset_compaction_state(conversation_id: str) -> None:
    """Reset compaction state for a conversation (for testing)."""
    if conversation_id in _compaction_state:
        del _compaction_state[conversation_id]
