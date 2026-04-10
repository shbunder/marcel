"""JSONL conversation history — structured, parseable turn-by-turn log.

Replaces the Markdown conversation logs with a format optimized for:
- Efficient appending and reading (one line per message)
- Tool call tracking (function calls and results)
- Filtering by conversation, date range, role
- Large content offloading to paste store

Each line is a JSON object with:
- role: 'user' | 'assistant' | 'tool' | 'system'
- text: message content (or None if result_ref used)
- timestamp: ISO 8601 UTC
- conversation_id: conversation identifier
- tool_calls: list of {id, name, arguments} for assistant messages
- tool_call_id: reference for tool messages
- result_ref: content hash for large tool results (stored in paste store)
- is_error: boolean for tool errors
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from marcel_core.storage._root import data_root

log = logging.getLogger(__name__)

MessageRole = Literal['user', 'assistant', 'tool', 'system']


@dataclass
class ToolCall:
    """A tool invocation from an assistant message."""

    id: str
    name: str
    arguments: dict


@dataclass
class HistoryMessage:
    """A single message in the conversation history."""

    role: MessageRole
    text: str | None
    timestamp: datetime
    conversation_id: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None  # tool name for role='tool' messages
    result_ref: str | None = None
    is_error: bool = False

    def to_jsonl(self) -> str:
        """Serialize to a single JSON line."""
        obj = {
            'role': self.role,
            'text': self.text,
            'timestamp': self.timestamp.isoformat(),
            'conversation_id': self.conversation_id,
        }
        if self.tool_calls:
            obj['tool_calls'] = [{'id': tc.id, 'name': tc.name, 'arguments': tc.arguments} for tc in self.tool_calls]
        if self.tool_call_id:
            obj['tool_call_id'] = self.tool_call_id
        if self.tool_name:
            obj['tool_name'] = self.tool_name
        if self.result_ref:
            obj['result_ref'] = self.result_ref
        if self.is_error:
            obj['is_error'] = True
        return json.dumps(obj, ensure_ascii=False)

    @classmethod
    def from_jsonl(cls, line: str) -> HistoryMessage:
        """Deserialize from a JSON line."""
        obj = json.loads(line)
        tool_calls = None
        if 'tool_calls' in obj:
            tool_calls = [ToolCall(id=tc['id'], name=tc['name'], arguments=tc['arguments']) for tc in obj['tool_calls']]
        return cls(
            role=obj['role'],
            text=obj.get('text'),
            timestamp=datetime.fromisoformat(obj['timestamp']),
            conversation_id=obj['conversation_id'],
            tool_calls=tool_calls,
            tool_call_id=obj.get('tool_call_id'),
            tool_name=obj.get('tool_name'),
            result_ref=obj.get('result_ref'),
            is_error=obj.get('is_error', False),
        )


def _history_path(user_slug: str) -> Path:
    """Return the path to the user's history.jsonl file."""
    return data_root() / 'users' / user_slug / 'history.jsonl'


def append_message(user_slug: str, message: HistoryMessage) -> None:
    """Append a message to the user's history file.

    Creates the file if it doesn't exist. Thread-safe via atomic append.

    Args:
        user_slug: The user's slug.
        message: The message to append.
    """
    path = _history_path(user_slug)
    path.parent.mkdir(parents=True, exist_ok=True)

    line = message.to_jsonl() + '\n'

    # Atomic append: open in append mode
    with open(path, 'a', encoding='utf-8') as f:
        f.write(line)


def read_history(
    user_slug: str,
    conversation_id: str | None = None,
    limit: int | None = None,
) -> list[HistoryMessage]:
    """Read history messages for a user, optionally filtered by conversation.

    Args:
        user_slug: The user's slug.
        conversation_id: If provided, only return messages from this conversation.
        limit: Maximum number of messages to return (most recent first).

    Returns:
        List of messages, newest first if limit is provided, otherwise chronological order.
    """
    path = _history_path(user_slug)
    if not path.exists():
        return []

    messages: list[HistoryMessage] = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = HistoryMessage.from_jsonl(line)
                if conversation_id is None or msg.conversation_id == conversation_id:
                    messages.append(msg)
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                log.warning('Failed to parse history line: %s — %s', line[:100], exc)
                continue

    # If limit specified, return most recent N messages
    if limit is not None and len(messages) > limit:
        messages = messages[-limit:]

    return messages


def read_recent_turns(user_slug: str, conversation_id: str, num_turns: int = 10) -> list[HistoryMessage]:
    """Read the most recent N turns (user + assistant pairs) for a conversation.

    Args:
        user_slug: The user's slug.
        conversation_id: The conversation identifier.
        num_turns: Number of turns to retrieve (default: 10).

    Returns:
        List of messages from the last N turns, chronological order.
    """
    # Read all messages for this conversation
    messages = read_history(user_slug, conversation_id=conversation_id)

    # Count turns (user messages start turns)
    turn_starts: list[int] = []
    for i, msg in enumerate(messages):
        if msg.role == 'user':
            turn_starts.append(i)

    # Take last N turn starts
    if len(turn_starts) > num_turns:
        start_idx = turn_starts[-num_turns]
        messages = messages[start_idx:]

    return messages


def count_tokens_estimate(messages: list[HistoryMessage]) -> int:
    """Estimate token count for a list of messages.

    Uses a simple heuristic: ~4 characters per token.

    Args:
        messages: List of history messages.

    Returns:
        Estimated token count.
    """
    total_chars = 0
    for msg in messages:
        if msg.text:
            total_chars += len(msg.text)
        if msg.tool_calls:
            for tc in msg.tool_calls:
                total_chars += len(tc.name) + len(json.dumps(tc.arguments))
    return total_chars // 4


def create_compaction_summary(
    messages: list[HistoryMessage], summary_text: str, conversation_id: str
) -> HistoryMessage:
    """Create a synthetic system message representing a compaction summary.

    Args:
        messages: The messages that were summarized (not used, but kept for signature clarity).
        summary_text: The summary content.
        conversation_id: The conversation identifier.

    Returns:
        A system message containing the summary.
    """
    return HistoryMessage(
        role='system',
        text=f'[Context summary: {summary_text}]',
        timestamp=datetime.now(tz=timezone.utc),
        conversation_id=conversation_id,
    )
