"""Reference mapping from Claude Code transcript lines to transcript.schema.json events.

This exists to prove the schema can be filled from real transcripts (SP2's recordings). The runner
(S-03.4) may port it; the mapping is SP2's table in scratch/spikes/SP2-worker-control/RESULT.md.
Lines of the types in DROPPED carry no conversation content and produce nothing. A line of a type
nobody knows yet becomes a `raw` event, so a Claude Code update shows up instead of vanishing.
"""

from __future__ import annotations

import json
import re
from typing import Any

DROPPED = {
    'attachment',
    'custom-title',
    'agent-name',
    'mode',
    'permission-mode',
    'last-prompt',
    'file-history-snapshot',
    'file-history-delta',
    'atis-latch',
    'cost-state',
    'bridge-session',
    'queue-operation',  # a hand-back is read from the user message that follows it
}
LIMIT_PREFIXES = (
    "You've hit your",
    "You've reached your",
    "You're out of usage credits",
    "You're out of extra usage",
    'Your org is out of usage',
    "Your seat type doesn't include usage",
    'Your usage allocation has been disabled by your admin',
    'Fable 5 requires usage credits',
)
MAX_TEXT = 8192
AGENT_FROM = re.compile(r'<agent-message from="([^"]+)">')
AGENT_LAUNCHED = re.compile(r'agentId:\s*([0-9a-f]+)')

Event = dict[str, Any]
# What an `Agent` tool call asked for, by tool_use_id, so the later launch result can name the
# subagent. The runner keeps one of these per tail stream.
Agents = dict[str, dict[str, str]]


def _clip(text: str, limit: int = MAX_TEXT) -> tuple[str, bool]:
    return (text, False) if len(text) <= limit else (text[:limit], True)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(b.get('text', '') for b in content if isinstance(b, dict))
    return ''


def _summary(name: str, tool_input: dict[str, Any]) -> str:
    for key in ('command', 'file_path', 'description', 'pattern', 'url', 'prompt'):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value.splitlines()[0][:200]
    return name


def _assistant(line: dict[str, Any], agents: Agents) -> list[tuple[str, dict[str, Any]]]:
    message = line.get('message', {})
    blocks = message.get('content', [])
    if line.get('isApiErrorMessage'):
        text = _text_of(blocks)
        error: dict[str, Any] = {'message': text, 'usage_limit': text.startswith(LIMIT_PREFIXES)}
        if isinstance(line.get('api_error_status'), int):
            error['api_error_status'] = line['api_error_status']
        return [('error', error)]
    out: list[tuple[str, dict[str, Any]]] = []
    for block in blocks:
        kind = block.get('type')
        if kind == 'text' and block.get('text'):
            out.append(('text', {'role': 'assistant', 'text': block['text']}))
        elif kind == 'thinking':
            out.append(('thinking', {'duration_ms': int(line.get('thinkingDurationMs') or 0)}))
        elif kind == 'tool_use':
            tool_input = block.get('input') or {}
            if block['name'] == 'Agent':
                agents[block['id']] = {
                    k: tool_input[k]
                    for k in ('description', 'subagent_type')
                    if isinstance(tool_input.get(k), str)
                }
            out.append(
                (
                    'tool_call',
                    {
                        'tool_use_id': block['id'],
                        'tool': block['name'],
                        'summary': _summary(block['name'], tool_input),
                        'input': tool_input,
                    },
                )
            )
    return out


def _tool_result(
    line: dict[str, Any], block: dict[str, Any], agents: Agents
) -> list[tuple[str, dict[str, Any]]]:
    text, truncated = _clip(_text_of(block.get('content')))
    result: dict[str, Any] = {
        'tool_use_id': block['tool_use_id'],
        'is_error': bool(block.get('is_error')),
        'text': text,
    }
    if truncated:
        result['truncated'] = True
    if line.get('toolDenialKind') == 'user-rejected':
        result['denied'] = True
    out: list[tuple[str, dict[str, Any]]] = [('tool_result', result)]
    structured = line.get('toolUseResult')
    if isinstance(structured, dict) and structured.get('structuredPatch'):
        hunks = [
            {
                'old_start': h['oldStart'],
                'old_lines': h['oldLines'],
                'new_start': h['newStart'],
                'new_lines': h['newLines'],
                'lines': h['lines'],
            }
            for h in structured['structuredPatch']
        ]
        out.append(
            (
                'diff',
                {
                    'tool_use_id': block['tool_use_id'],
                    'file_path': structured['filePath'],
                    'hunks': hunks,
                },
            )
        )
    launched = AGENT_LAUNCHED.search(text) if 'Async agent launched' in text else None
    if launched:
        asked = agents.pop(block['tool_use_id'], {})
        start: dict[str, Any] = {
            'tool_use_id': block['tool_use_id'],
            'agent_id': launched.group(1),
            'description': asked.get('description', 'subagent'),
        }
        if 'subagent_type' in asked:
            start['subagent_type'] = asked['subagent_type']
        out.append(('subagent_start', start))
    return out


def _user(line: dict[str, Any], agents: Agents) -> list[tuple[str, dict[str, Any]]]:
    content = line.get('message', {}).get('content')
    # A subagent's hand-back arrives as a user line marked isMeta, so look for it before
    # dropping meta lines.
    if isinstance(content, str) and content.startswith('Another Claude session sent a message'):
        handback = AGENT_FROM.search(content)
        if handback:
            summary, _ = _clip(content, 2048)
            status = 'completed' if '[Subagent hand-back]' in content else 'unknown'
            return [
                (
                    'subagent_stop',
                    {'agent_id': handback.group(1), 'status': status, 'summary': summary},
                )
            ]
    if line.get('isMeta'):
        return []
    if isinstance(content, str):
        data: dict[str, Any] = {'role': 'user', 'text': content}
        source = line.get('promptSource') or line.get('origin')
        if isinstance(source, str):
            data['source'] = source
        return [('text', data)]
    out: list[tuple[str, dict[str, Any]]] = []
    for block in content or []:
        if block.get('type') == 'tool_result':
            out += _tool_result(line, block, agents)
        elif block.get('type') == 'text' and block.get('text'):
            out.append(('text', {'role': 'user', 'text': block['text']}))
    return out


def normalize(
    line: dict[str, Any], *, session_id: str, offset: int, agents: Agents | None = None
) -> list[Event]:
    """Every event one transcript line gives, in order."""
    agents = {} if agents is None else agents
    kind = line.get('type')
    if kind in DROPPED:
        return []
    if kind == 'assistant':
        pairs = _assistant(line, agents)
    elif kind == 'user':
        pairs = _user(line, agents)
    elif kind == 'system' and line.get('subtype') == 'turn_duration':
        turn: dict[str, Any] = {'duration_ms': int(line.get('durationMs') or 0)}
        if isinstance(line.get('messageCount'), int):
            turn['message_count'] = line['messageCount']
        if isinstance(line.get('pendingBackgroundAgentCount'), int):
            turn['pending_background_agents'] = line['pendingBackgroundAgentCount']
        pairs = [('turn_end', turn)]
    elif kind == 'system':
        pairs = []
    else:
        entry = line if len(json.dumps(line)) <= MAX_TEXT else {'type': kind, 'truncated': True}
        pairs = [('raw', {'entry_type': str(kind), 'entry': entry})]
    at = line.get('timestamp') or '1970-01-01T00:00:00Z'
    return [
        {'session_id': session_id, 'offset': offset, 'index': i, 'at': at, 'type': t, 'data': d}
        for i, (t, d) in enumerate(pairs)
    ]


def normalize_file(text: str, session_id: str) -> list[Event]:
    events: list[Event] = []
    agents: Agents = {}
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        offset += len(raw_line.encode())
        if raw_line.strip():
            events += normalize(
                json.loads(raw_line), session_id=session_id, offset=offset, agents=agents
            )
    return events
