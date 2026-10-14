"""Transcript tailing: `GET /sessions/{id}/tail`.

A session's transcript is the JSONL file Claude Code writes under
`~/.claude/projects/<cwd, every non-alphanumeric character → ->/<session-id>.jsonl`; a subagent's is
`<session-id>/subagents/agent-<agent_id>.jsonl` next to it (SP2). A workflow's run (runner-api
0.1.12, SP10) has its own folder, `<session-id>/subagents/workflows/<run id>/`, with the run's
`journal.jsonl` and one `agent-<agent_id>.jsonl` per agent. The runner reads one of those files and
streams each complete line as zero or more `contracts/transcript.schema.json` events over
Server-Sent Events.

- Workflows in the main transcript: the `Workflow` tool's launch result (`toolUseResult.status`
  `async_launched`, `taskType` `local_workflow`) gives `workflow_start`; the later
  `<task-notification>` naming that call gives `workflow_stop` instead of a `text`. The journal
  (`tail?workflow=`) gives `workflow_agent_start` / `workflow_agent_stop`; an agent's file
  (`tail?workflow=&subagent=`) is read like any transcript. Every event of a workflow stream carries
  `workflow_id`. The file must resolve inside the session's own folder (no `..`, no symlink out).

- `Normaliser` turns one line into events. It is a port of `contracts/tests/reference_normalizer.py`
  (SP2's mapping table) that never raises: a line it cannot read or a type it does not know becomes
  a `raw` event. A `turn_end` carries `usage`: the four token counts of the turn's last
  main-thread assistant line, kept per stream and cleared at each turn end. Subagent lines
  (`isSidechain`, or a stream of a file under `subagents/`) never count.
- `LineReader` reads complete lines of one file from a byte offset, with bounded memory. A partial
  last line waits until its newline arrives. A line longer than `MAX_LINE` is skipped without being
  held in memory. A file that is truncated, replaced or deleted is read again from the start.
- `Tails` answers the HTTP request. One tail per file: a second tail for the same file replaces the
  first, which ends. That way a client that reconnects before the runner noticed the old
  connection closing is never refused.

Each SSE message is `id: <byte offset after the line>`, `event: transcript`, `data: <event>`.
Resuming from an `offset` (or `Last-Event-ID`) starts at that byte; an offset in the middle of a
line starts at the beginning of that line, never mid-line.
"""

import contextlib
import json
import os
import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from marcel_runner.sessions import SessionError, project_dir

MAX_TEXT = 8192  # tool results, `raw` entries and each string of a tool's input (transcript schema)
MAX_SUMMARY = 2048  # a subagent's hand-back summary
MAX_LINE = 8 * 1024 * 1024  # a longer line is skipped, never held in memory
MAX_AGENTS = 256  # `Agent` calls remembered per stream until their launch result names them
CHUNK = 64 * 1024  # bytes read at a time when skipping a long line or looking back for a line start
POLL = 0.25  # seconds between two looks at a quiet file
SEARCH_EVERY = 5.0  # seconds between two searches of every project folder for a missing file
KEEPALIVE = 15.0  # seconds between SSE comments on a quiet stream, to notice a client that left
SUBAGENT_ID = re.compile(r'^[0-9a-f]+$')
WORKFLOW_ID = re.compile(r'^wf_[A-Za-z0-9_-]{1,64}$')
TOOL_USE_ID = re.compile(r'<tool-use-id>([^<]+)</tool-use-id>')
NOTE_STATUS = re.compile(r'<status>([^<]*)</status>')
NOTE_SUMMARY = re.compile(r'<summary>(.*?)</summary>', re.DOTALL)
WORKFLOW_STATUS = {
    'completed': 'completed',
    'failed': 'failed',
    'stopped': 'stopped',
    'killed': 'stopped',
}
OFFSET = re.compile(r'^[0-9]+$')  # not str.isdigit(): that also takes '²', which int() refuses
GLOB_SAFE = re.compile(r'^[A-Za-z0-9-]+$')

# Lines of these types carry no conversation content and give no event (SP2).
DROPPED = frozenset(
    {
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
)
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
AGENT_FROM = re.compile(r'<agent-message from="([^"]+)">')
AGENT_LAUNCHED = re.compile(r'agentId:\s*([0-9a-f]+)')
# transcript.schema.json `TurnEnd.usage`, from `message.usage` of an assistant line.
USAGE_KEYS = (
    'input_tokens',
    'cache_creation_input_tokens',
    'cache_read_input_tokens',
    'output_tokens',
)

Event = dict[str, Any]
Pair = tuple[str, dict[str, Any]]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _is_time(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return False
    return True


def _clip(text: str, limit: int = MAX_TEXT) -> tuple[str, bool]:
    return (text, False) if len(text) <= limit else (text[:limit], True)


def _clip_strings(value: Any) -> Any:
    """`value` with every string in it cut to `MAX_TEXT` characters."""
    if isinstance(value, str):
        return _clip(value)[0]
    if isinstance(value, dict):
        return {k: _clip_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip_strings(v) for v in value]
    return value


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(
            b['text'] for b in content if isinstance(b, dict) and isinstance(b.get('text'), str)
        )
    return ''


def _summary(name: str, tool_input: Mapping[str, Any]) -> str:
    for key in ('command', 'file_path', 'description', 'pattern', 'url', 'prompt'):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().splitlines()[0][:200]
    return name


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class Normaliser:
    """Transcript lines → events for one stream. It keeps what the `Agent` calls asked for (by
    `tool_use_id`, at most `MAX_AGENTS`), so a later launch result can name the subagent, the
    last line's time, for a line that has none, and the token counts of the last main-thread
    assistant line, for the next `turn_end`."""

    def __init__(
        self,
        session_id: str,
        subagent_id: str | None = None,
        workflow_id: str | None = None,
        journal: bool = False,
    ) -> None:
        self.session_id = session_id
        self.subagent_id = subagent_id
        self.workflow_id = workflow_id
        self.journal = journal  # a run's journal.jsonl, not a transcript
        self.agents: dict[str, dict[str, str]] = {}
        self.workflows: dict[str, str] = {}  # a `Workflow` call's tool_use_id → its run id
        self.usage: dict[str, int] = {}
        self._at: str | None = None

    def reset(self) -> None:
        """Forget everything: the file is read again from its start."""
        self.agents.clear()
        self.workflows.clear()
        self.usage.clear()
        self._at = None

    def line(self, raw: bytes, offset: int) -> list[Event]:
        """Every event one JSONL line gives, in order. Never raises."""
        try:
            entry = json.loads(raw)
        except ValueError:
            return self._events(offset, None, [('raw', {'entry_type': 'unreadable'})])
        if not isinstance(entry, dict):
            return self._events(offset, None, [('raw', {'entry_type': 'unreadable'})])
        try:
            pairs = self._journal(entry) if self.journal else self._pairs(entry)
        except (AttributeError, KeyError, TypeError, ValueError):
            pairs = [self._raw(entry)]  # a known type in a shape nobody has seen
        return self._events(offset, entry.get('timestamp'), pairs)

    def too_long(self, offset: int, size: int) -> list[Event]:
        """The event for a line that was skipped because it is longer than the limit."""
        entry = {'type': 'line-too-long', 'bytes': size, 'truncated': True}
        return self._events(
            offset, None, [('raw', {'entry_type': 'line-too-long', 'entry': entry})]
        )

    def _events(self, offset: int, at: Any, pairs: list[Pair]) -> list[Event]:
        if _is_time(at):
            self._at = at
        when = self._at or _now()
        out = []
        for i, (kind, data) in enumerate(pairs):
            event: Event = {'session_id': self.session_id, 'offset': offset, 'index': i}
            if self.subagent_id is not None:
                event['subagent_id'] = self.subagent_id
            if self.workflow_id is not None:
                event['workflow_id'] = self.workflow_id
            event.update(at=when, type=kind, data=data)
            out.append(event)
        return out

    def _journal(self, entry: dict[str, Any]) -> list[Pair]:
        """One line of a run's `journal.jsonl` (SP10): `launched`, `started`, `result`."""
        kind, run = entry.get('type'), str(self.workflow_id)
        agent = entry.get('agentId')
        if kind == 'launched':
            return []
        if kind in ('started', 'result') and isinstance(agent, str) and SUBAGENT_ID.match(agent):
            if kind == 'started':
                label = entry.get('label')
                start: dict[str, Any] = {
                    'run_id': run,
                    'agent_id': agent,
                    'label': label if isinstance(label, str) and label else 'agent',
                }
                if isinstance(entry.get('phase'), str) and entry['phase']:
                    start['phase'] = entry['phase']
                return [('workflow_agent_start', start)]
            stop: dict[str, Any] = {'run_id': run, 'agent_id': agent}
            if 'result' in entry:
                result = entry['result']
                text = (
                    result if isinstance(result, str) else json.dumps(result, separators=(',', ':'))
                )
                stop['summary'] = _clip(text, MAX_SUMMARY)[0]
            return [('workflow_agent_stop', stop)]
        return [self._raw(entry)]

    def _pairs(self, entry: dict[str, Any]) -> list[Pair]:
        kind = entry.get('type')
        if kind in DROPPED:
            return []
        if kind == 'assistant':
            return self._assistant(entry)
        if kind == 'user':
            return self._user(entry)
        if kind == 'system' and entry.get('subtype') == 'turn_duration':
            turn: dict[str, Any] = {'duration_ms': _int(entry.get('durationMs'))}
            if _count(entry.get('messageCount')) is not None:
                turn['message_count'] = entry['messageCount']
            if _count(entry.get('pendingBackgroundAgentCount')) is not None:
                turn['pending_background_agents'] = entry['pendingBackgroundAgentCount']
            if self.usage:
                turn['usage'] = dict(self.usage)
                self.usage.clear()
            return [('turn_end', turn)]
        if kind == 'system':
            return []
        return [self._raw(entry)]

    @staticmethod
    def _raw(entry: dict[str, Any]) -> Pair:
        kind = str(entry.get('type'))
        fits = len(json.dumps(entry)) <= MAX_TEXT
        return 'raw', {
            'entry_type': kind,
            'entry': entry if fits else {'type': kind, 'truncated': True},
        }

    def _keep_usage(self, entry: dict[str, Any]) -> None:
        """Remember this line's token counts when it is a main-thread line that has all four."""
        if self.subagent_id is not None or entry.get('isSidechain'):
            return
        counts = (entry.get('message') or {}).get('usage')
        if not isinstance(counts, dict):
            return
        kept = {k: v for k in USAGE_KEYS if (v := _count(counts.get(k))) is not None}
        if len(kept) == len(USAGE_KEYS):
            self.usage = kept

    def _assistant(self, entry: dict[str, Any]) -> list[Pair]:
        self._keep_usage(entry)
        blocks = (entry.get('message') or {}).get('content') or []
        if entry.get('isApiErrorMessage'):
            text = _text_of(blocks)
            error: dict[str, Any] = {
                'message': text,
                'usage_limit': text.startswith(LIMIT_PREFIXES),
            }
            status = entry.get('api_error_status')
            if isinstance(status, int) and not isinstance(status, bool):
                error['api_error_status'] = status
            return [('error', error)]
        out: list[Pair] = []
        for block in blocks if isinstance(blocks, list) else []:
            kind = block.get('type') if isinstance(block, dict) else None
            if kind == 'text' and isinstance(block.get('text'), str) and block['text']:
                out.append(('text', {'role': 'assistant', 'text': block['text']}))
            elif kind == 'thinking':
                out.append(('thinking', {'duration_ms': _int(entry.get('thinkingDurationMs'))}))
            elif kind == 'tool_use':
                out.append(self._tool_use(block))
        return out

    def _tool_use(self, block: dict[str, Any]) -> Pair:
        use_id, name = str(block['id']), str(block['name'])
        tool_input = block.get('input')
        tool_input = tool_input if isinstance(tool_input, dict) else {}
        if name == 'Agent':
            self.agents[use_id] = {
                k: tool_input[k]
                for k in ('description', 'subagent_type')
                if isinstance(tool_input.get(k), str)
            }
            while len(self.agents) > MAX_AGENTS:  # a call never launched is forgotten, oldest first
                del self.agents[next(iter(self.agents))]
        call = {'tool_use_id': use_id, 'tool': name, 'summary': _summary(name, tool_input)}
        return 'tool_call', {**call, 'input': _clip_strings(tool_input)}

    def _user(self, entry: dict[str, Any]) -> list[Pair]:
        content = (entry.get('message') or {}).get('content')
        # A subagent's hand-back arrives as a user line marked isMeta: look before dropping those.
        if isinstance(content, str) and content.startswith('Another Claude session sent a message'):
            handback = AGENT_FROM.search(content)
            if handback:
                status = 'completed' if '[Subagent hand-back]' in content else 'unknown'
                summary = _clip(content, MAX_SUMMARY)[0]
                return [
                    (
                        'subagent_stop',
                        {'agent_id': handback.group(1), 'status': status, 'summary': summary},
                    )
                ]
        if entry.get('isMeta'):
            return []
        stopped = self._workflow_stop(content)
        if stopped is not None:
            return [stopped]
        if isinstance(content, str):
            data: dict[str, Any] = {'role': 'user', 'text': content}
            source = entry.get('promptSource') or entry.get('origin')
            if isinstance(source, str):
                data['source'] = source
            return [('text', data)]
        out: list[Pair] = []
        for block in content if isinstance(content, list) else []:
            kind = block.get('type') if isinstance(block, dict) else None
            if kind == 'tool_result':
                out += self._tool_result(entry, block)
            elif kind == 'text' and isinstance(block.get('text'), str) and block['text']:
                out.append(('text', {'role': 'user', 'text': block['text']}))
        return out

    def _workflow_stop(self, content: Any) -> Pair | None:
        """A `<task-notification>` naming a `Workflow` call this stream saw launch, or None."""
        text = _text_of(content)
        if '<task-notification>' not in text:
            return None
        use = TOOL_USE_ID.search(text)
        if use is None or use.group(1) not in self.workflows:
            return None
        status = NOTE_STATUS.search(text)
        stop: dict[str, Any] = {
            'tool_use_id': use.group(1),
            'run_id': self.workflows.pop(use.group(1)),
            'status': WORKFLOW_STATUS.get(status.group(1).strip() if status else '', 'unknown'),
        }
        summary = NOTE_SUMMARY.search(text)
        if summary:
            stop['summary'] = _clip(summary.group(1).strip(), MAX_SUMMARY)[0]
        return 'workflow_stop', stop

    def _workflow_start(self, use_id: str, structured: Any) -> Pair | None:
        """The `Workflow` tool's launch result (SP10), or None."""
        if not isinstance(structured, dict):
            return None
        run = structured.get('runId')
        launched = structured.get('status') == 'async_launched'
        if not (launched and structured.get('taskType') == 'local_workflow'):
            return None
        if not (isinstance(run, str) and WORKFLOW_ID.match(run)):
            return None
        self.workflows[use_id] = run
        while len(self.workflows) > MAX_AGENTS:  # a run never told ended is forgotten, oldest first
            del self.workflows[next(iter(self.workflows))]
        name = structured.get('workflowName')
        start: dict[str, Any] = {
            'tool_use_id': use_id,
            'run_id': run,
            'name': name if isinstance(name, str) and name else 'workflow',
        }
        if isinstance(structured.get('taskId'), str) and structured['taskId']:
            start['task_id'] = structured['taskId']
        if isinstance(structured.get('summary'), str) and structured['summary']:
            start['description'] = structured['summary']
        return 'workflow_start', start

    def _tool_result(self, entry: dict[str, Any], block: dict[str, Any]) -> list[Pair]:
        use_id = str(block['tool_use_id'])
        full = _text_of(block.get('content'))
        text, truncated = _clip(full)
        result: dict[str, Any] = {
            'tool_use_id': use_id,
            'is_error': bool(block.get('is_error')),
            'text': text,
        }
        if truncated:
            result['truncated'] = True
        if entry.get('toolDenialKind') == 'user-rejected':
            result['denied'] = True
        out: list[Pair] = [('tool_result', result)]
        structured = entry.get('toolUseResult')
        if isinstance(structured, dict) and structured.get('structuredPatch'):
            hunks = [
                {
                    'old_start': int(h['oldStart']),
                    'old_lines': int(h['oldLines']),
                    'new_start': int(h['newStart']),
                    'new_lines': int(h['newLines']),
                    'lines': [str(line) for line in h['lines']],
                }
                for h in structured['structuredPatch']
            ]
            out.append(
                (
                    'diff',
                    {
                        'tool_use_id': use_id,
                        'file_path': str(structured['filePath']),
                        'hunks': hunks,
                    },
                )
            )
        launched = AGENT_LAUNCHED.search(full) if 'Async agent launched' in full else None
        if launched:
            asked = self.agents.pop(use_id, {})
            start: dict[str, Any] = {
                'tool_use_id': use_id,
                'agent_id': launched.group(1),
                'description': asked.get('description', 'subagent'),
            }
            if 'subagent_type' in asked:
                start['subagent_type'] = asked['subagent_type']
            out.append(('subagent_start', start))
        workflow = self._workflow_start(use_id, structured)
        if workflow is not None:
            out.append(workflow)
        return out


# Reading the file ------------------------------------------------------------------------------


class LineReader:
    """Complete lines of one file, from a byte offset on. Memory stays below about `max_line`.

    `lines()` yields `(start, end, line)`: the line's first byte, the byte offset just after it,
    and its bytes, or None when it was longer than `max_line` and skipped. It stops at the end of
    what is there; call it again to read what was added since. `restarts` goes up each time the
    file had to be read again from the start (it was truncated, replaced or deleted), so the
    caller can forget what it learned from the old one. `on_open(path, pos)` is called each time
    a file is opened, with the position reading starts from.
    """

    def __init__(
        self,
        locate: Callable[[], Path | None],
        offset: int = 0,
        max_line: int = MAX_LINE,
        on_open: Callable[[Path, int], None] | None = None,
    ) -> None:
        self.locate = locate
        self.on_open = on_open
        self.pos = offset  # where the next line starts
        self.max_line = max_line
        self.restarts = 0
        self.path: Path | None = None
        self._f: BinaryIO | None = None
        self._ident: tuple[int, int] | None = None
        self._scanned = 0  # how far a too-long line at `pos` has been looked through

    def close(self) -> None:
        if self._f is not None:
            self._f.close()
        self._f = None

    def _restart(self) -> None:
        self.pos, self._scanned = 0, 0
        self.restarts += 1

    def _open(self) -> bool:
        path = self.locate()
        if path is None:
            return False
        try:
            f = path.open('rb')
        except OSError:
            return False
        st = os.fstat(f.fileno())
        self.path, self._f, self._ident = path, f, (st.st_dev, st.st_ino)
        if self.pos > st.st_size:  # an offset into an older, longer file: start over
            self._restart()
        elif self.pos > 0:
            self.pos = _line_start(f, self.pos)
        if self.on_open is not None:
            self.on_open(path, self.pos)
        return True

    def _replaced(self) -> bool:
        """The path now names another file, or none."""
        assert self.path is not None
        try:
            st = os.stat(self.path)
        except OSError:
            return True
        return (st.st_dev, st.st_ino) != self._ident

    def lines(self) -> Iterator[tuple[int, int, bytes | None]]:
        if self._f is None and not self._open():
            return
        assert self._f is not None
        if os.fstat(self._f.fileno()).st_size < self.pos:  # truncated in place
            self._restart()
        replaced = self._replaced()
        yield from self._read()
        if replaced:  # what the old file still had is read; the new one starts from 0
            self.close()
            self._restart()

    def _read(self) -> Iterator[tuple[int, int, bytes | None]]:
        f = self._f
        assert f is not None
        while True:
            start = self.pos
            if self._scanned:
                end = self._skip()
                if end is None:
                    return  # the long line is still being written
                self.pos, self._scanned = end, 0
                yield start, end, None
                continue
            f.seek(start)
            line = f.readline(self.max_line + 1)  # never more than this in memory
            if line.endswith(b'\n'):
                self.pos += len(line)
                yield start, self.pos, line
            elif len(line) > self.max_line:
                self._scanned = start + len(line)
            else:
                return  # nothing more, or a line still being written

    def _skip(self) -> int | None:
        """Look for the end of the too-long line at `pos`. Its end offset, or None for not yet."""
        f = self._f
        assert f is not None
        f.seek(self._scanned)
        while chunk := f.read(CHUNK):
            newline = chunk.find(b'\n')
            if newline >= 0:
                return self._scanned + newline + 1
            self._scanned += len(chunk)
        return None


def _line_start(f: BinaryIO, pos: int) -> int:
    """The start of the line `pos` falls in: `pos` itself when the byte before it ends a line,
    else the byte after the newline before `pos` (or 0). Reads backwards in chunks."""
    end = pos
    while end > 0:
        start = max(0, end - CHUNK)
        f.seek(start)
        chunk = f.read(end - start)
        if end == pos and chunk.endswith(b'\n'):
            return pos
        newline = chunk.rfind(b'\n')
        if newline >= 0:
            return start + newline + 1
        end = start
    return 0


def _agents_before(normaliser: Normaliser, path: Path, pos: int, max_line: int) -> None:
    """Learn the `Agent` calls made before a resume point, so a launch after it is still named,
    and the token counts of the turn it resumes in, so its `turn_end` still carries them. A
    `turn_end` before the resume point clears those counts, as it would have on the stream."""
    if pos == 0:
        return
    before = LineReader(lambda: path, 0, max_line)
    try:
        for _, end, line in before.lines():
            if end > pos:
                break
            if line is not None and (
                b'"Agent"' in line
                or b'Async agent launched' in line
                or b'local_workflow' in line
                or b'"usage"' in line
                or b'turn_duration' in line
            ):
                normaliser.line(line, end)
    finally:
        before.close()


# Finding the file ------------------------------------------------------------------------------


def _name(session_id: str, subagent: str | None, workflow: str | None = None) -> str:
    if workflow is not None:
        run = f'{session_id}/subagents/workflows/{workflow}'
        return f'{run}/agent-{subagent}.jsonl' if subagent is not None else f'{run}/journal.jsonl'
    if subagent is None:
        return f'{session_id}.jsonl'
    return f'{session_id}/subagents/agent-{subagent}.jsonl'


def inside_session(path: Path, name: str, session_id: str) -> bool:
    """`path`, found as `<project folder>/<name>`, resolves where it should: the main transcript in
    its project folder, anything else inside `<project folder>/<session-id>/` (no `..`, no symlink
    that leads out of the session's own folder)."""
    depth = len(Path(name).parts)
    project = path.parents[depth - 1].resolve()
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if depth == 1:
        return resolved.parent == project
    return resolved.is_relative_to(project / session_id)


def _expected(
    home: Path, session_id: str, cwd: str, subagent: str | None, workflow: str | None = None
) -> Path | None:
    """The file in the folder named after the cwd, when it exists."""
    if not cwd:
        return None
    name = _name(session_id, subagent, workflow)
    exact = home / '.claude' / 'projects' / project_dir(cwd) / name
    return exact if exact.is_file() else None


def _search(
    home: Path, session_id: str, subagent: str | None, workflow: str | None = None
) -> Path | None:
    """The file in any project folder (sorted by name, the first wins)."""
    if not GLOB_SAFE.match(session_id):
        return None
    projects = home / '.claude' / 'projects'
    name = _name(session_id, subagent, workflow)
    return next(iter(sorted(projects.glob(f'*/{name}'))), None)


def transcript_path(
    home: Path,
    session_id: str,
    cwd: str,
    subagent: str | None = None,
    workflow: str | None = None,
) -> Path | None:
    """Where the transcript (or a run's journal, or one of its agents) is, or None while it does
    not exist.

    The folder name comes from the session's cwd. Only `/` → `-` is recorded (SP2); other
    characters (`_`, `.`) and very long paths are a guess, so when that folder has no such file the
    runner looks for the session id in every project folder.
    """
    return _expected(home, session_id, cwd, subagent, workflow) or _search(
        home, session_id, subagent, workflow
    )


class Locator:
    """`transcript_path` for a tail that waits for its file: the expected folder is looked at on
    every call (one `stat`), but every project folder is searched at most once per `search_every`
    seconds, so a tail waiting for a file that does not exist yet does not list `~/.claude/projects`
    four times a second."""

    def __init__(
        self,
        home: Path,
        session_id: str,
        cwd: str,
        subagent: str | None = None,
        workflow: str | None = None,
        *,
        search_every: float = SEARCH_EVERY,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.home, self.session_id, self.cwd, self.subagent = home, session_id, cwd, subagent
        self.workflow = workflow
        self._name = _name(session_id, subagent, workflow)
        self.search_every = search_every
        self.clock = clock
        self.searches = 0
        self._searched: float | None = None

    def __call__(self) -> Path | None:
        found = self._find()
        if found is not None and not inside_session(found, self._name, self.session_id):
            return None  # a link out of the session's folder is never read (checked again per open)
        return found

    def _find(self) -> Path | None:
        found = _expected(self.home, self.session_id, self.cwd, self.subagent, self.workflow)
        if found is not None:
            return found
        now = self.clock()
        if self._searched is not None and now - self._searched < self.search_every:
            return None
        self._searched = now
        self.searches += 1
        return _search(self.home, self.session_id, self.subagent, self.workflow)

    def unsafe(self) -> bool:
        """The file exists but resolves outside the session's folder."""
        found = transcript_path(self.home, self.session_id, self.cwd, self.subagent, self.workflow)
        return found is not None and not inside_session(found, self._name, self.session_id)


# HTTP ------------------------------------------------------------------------------------------


class Tails:
    """Answers `GET /sessions/{id}/tail`. Thread-safe: each request runs on its own thread."""

    def __init__(
        self,
        roster: Callable[[], list[dict[str, Any]]],
        home: Path,
        *,
        poll: float = POLL,
        keepalive: float = KEEPALIVE,
        max_line: int = MAX_LINE,
    ) -> None:
        self.roster = roster
        self.home = home
        self.poll = poll
        self.keepalive = keepalive
        self.max_line = max_line
        self._open: dict[tuple[str, str, str], threading.Event] = {}
        self._lock = threading.Lock()
        self._closed = False

    def _claim(self, key: tuple[str, str, str]) -> threading.Event:
        """Register a tail for one file, ending the one that read it before (one tail per file)."""
        stop = threading.Event()
        with self._lock:
            previous = self._open.get(key)
            if previous is not None:
                previous.set()
            if self._closed:
                stop.set()
            self._open[key] = stop
        return stop

    def _release(self, key: tuple[str, str, str], stop: threading.Event) -> None:
        with self._lock:
            if self._open.get(key) is stop:
                del self._open[key]

    def close(self) -> None:
        """End every open tail (the runner is stopping)."""
        with self._lock:
            self._closed = True
            for stop in self._open.values():
                stop.set()

    def _session(self, ref: str) -> tuple[str, str]:
        for row in self.roster():
            if ref and ref in (row.get('sessionId'), row.get('id')) and row.get('sessionId'):
                return str(row['sessionId']), str(row.get('cwd') or '')
        raise SessionError(404, 'not_found', f'There is no session {ref} on this machine.')

    def handle(self, handler: Any, ref: str, query: Mapping[str, list[str]]) -> None:
        """Answer one tail request on a `BaseHTTPRequestHandler`, until the client leaves, the file
        is read (`follow=false`), another tail takes this file over, or the runner stops."""
        try:
            subagent, workflow, offset, follow = _params(
                query, handler.headers.get('Last-Event-ID')
            )
            session_id, cwd = self._session(ref)
            locate = Locator(self.home, session_id, cwd, subagent, workflow)
            if locate.unsafe():
                raise SessionError(
                    400,
                    'bad_request',
                    "That file leads out of the session's own folder, so it is not read.",
                )
        except SessionError as e:
            body = json.dumps(e.body()).encode()
            handler.send_response(e.status)
            handler.send_header('content-type', 'application/json')
            handler.send_header('content-length', str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
            return
        key = (session_id, workflow or '', subagent or '')
        stop = self._claim(key)
        journal = workflow is not None and subagent is None
        normaliser = Normaliser(session_id, subagent, workflow, journal=journal)
        reader = LineReader(
            locate,
            offset,
            self.max_line,
            on_open=lambda path, pos: _agents_before(normaliser, path, pos, self.max_line),
        )
        handler.send_response(200)
        handler.send_header('content-type', 'text/event-stream')
        handler.send_header('cache-control', 'no-cache')
        handler.end_headers()
        try:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self._stream(handler.wfile, reader, normaliser, stop, follow)
        finally:
            reader.close()
            self._release(key, stop)

    def _stream(
        self,
        out: Any,
        reader: LineReader,
        normaliser: Normaliser,
        stop: threading.Event,
        follow: bool,
    ) -> None:
        restarts, quiet = 0, 0.0
        while not stop.is_set():
            sent = False
            for start, end, line in reader.lines():
                if reader.restarts != restarts:  # a new file: what the old one said no longer holds
                    restarts = reader.restarts
                    normaliser.reset()
                if line is None:
                    events = normaliser.too_long(end, end - start)
                else:
                    events = normaliser.line(line, end)
                if events:
                    out.write(b''.join(_message(e) for e in events))
                    out.flush()
                    sent = True
                if stop.is_set():
                    return
            if not follow:
                return
            quiet = 0.0 if sent else quiet + self.poll
            if quiet >= self.keepalive:
                out.write(b': still here\n\n')
                out.flush()
                quiet = 0.0
            stop.wait(self.poll)


def _message(event: Event) -> bytes:
    return f'id: {event["offset"]}\nevent: transcript\ndata: {json.dumps(event)}\n\n'.encode()


def _params(
    query: Mapping[str, list[str]], last_event_id: str | None
) -> tuple[str | None, str | None, int, bool]:
    """`subagent`, `workflow`, the offset to start from and `follow`. `Last-Event-ID` wins over
    `offset`."""
    subagent = (query.get('subagent') or [None])[-1]
    if subagent is not None and not SUBAGENT_ID.match(subagent):
        raise SessionError(
            400, 'bad_request', f'"{subagent}" is not a subagent id (hex digits only).'
        )
    workflow = (query.get('workflow') or [None])[-1]
    if workflow is not None and not WORKFLOW_ID.match(workflow):
        raise SessionError(
            400, 'bad_request', f'"{workflow}" is not a workflow run id (wf_ and letters, digits).'
        )
    raw = last_event_id.strip() if last_event_id and last_event_id.strip() else None
    raw = raw or (query.get('offset') or ['0'])[-1]
    if not OFFSET.match(raw):
        raise SessionError(
            400, 'bad_request', f'The offset must be a whole number of bytes, not "{raw}".'
        )
    follow = (query.get('follow') or ['true'])[-1].lower() not in ('false', '0', 'no')
    return subagent, workflow, int(raw), follow
