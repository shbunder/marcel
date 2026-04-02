"""Agent module — Claude-powered conversation engine."""

from .context import build_system_prompt
from .events import (
    AgentEvent,
    RunError,
    RunFinished,
    RunStarted,
    TextMessageContent,
    TextMessageEnd,
    TextMessageStart,
    ToolCallEnd,
    ToolCallResult,
    ToolCallStart,
)
from .memory_extract import extract_and_save_memories
from .memory_select import select_relevant_memories
from .runner import stream_response
from .sessions import ActiveSession, SessionManager, session_manager

__all__ = [
    'ActiveSession',
    'AgentEvent',
    'RunError',
    'RunFinished',
    'RunStarted',
    'SessionManager',
    'TextMessageContent',
    'TextMessageEnd',
    'TextMessageStart',
    'ToolCallEnd',
    'ToolCallResult',
    'ToolCallStart',
    'build_system_prompt',
    'extract_and_save_memories',
    'select_relevant_memories',
    'session_manager',
    'stream_response',
]
