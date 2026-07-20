"""JSONL conversation history — message types.

Core data types (``HistoryMessage``, ``ToolCall``) are used across the
codebase. For conversation storage, use ``memory/conversation.py``
(segment-based continuous conversations).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

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
