#!/usr/bin/env python3
"""Give Marcel's brain what it must not lose when it starts or is compacted (S-06.8, ADR-010).

The marcel plugin runs this for two hooks (`hooks/hooks.json`):

- **`PreCompact`**: just before Claude Code compacts the context, it writes a short hand-over to
  `.marcel/handoff.md` in the session's folder: when, why (`manual` or `auto`), the open threads and
  the last exchange (the last message to Marcel and Marcel's last words, from the transcript). It
  prints nothing.
- **`SessionStart`** (matcher `startup|resume|compact`): whatever it prints is added to the
  session's context. For Marcel's brain (or a side thread), in this order:
  1. after a compaction only, the hand-over the `PreCompact` hook wrote (then it is removed, so an
     old one never comes back);
  2. `MEMORY.md` from the session's folder (the first 8 KB, like the rollover seed);
  3. the open threads, from the hub's `GET /brain/tasks`: one line each, `<id> <state> <title>`;
  4. today's summary and the live sessions, from `GET /brain/summary` (contract request S-06.8).
     Until the hub has that route, or when it answers 404 or 503, one line says it is missing.

Any other session (a worker, an adopted session) is refused by the brain API: then it prints and
writes nothing. A brain that has just started may call before the hub knows its id: on start or
resume an unknown session is asked about again, up to `START_RETRIES` times one second apart, with
one line on stderr each time. When the hub
cannot be reached, it still gives `MEMORY.md` and one line saying so. It always exits 0: a hook
error must never block the session.

It reads the hook's JSON on stdin (`hook_event_name`, `session_id`, `cwd`, `source`, `trigger`,
`transcript_path`), the hub secret from `$CLAUDE_PLUGIN_DATA/hub-secret`, and calls the hub like
the brain tools do (`tools/src/marcel_tools/hub.py`: `X-Marcel-Secret`, `X-Marcel-Session`,
`http://127.0.0.1:7420/brain`). Standard library only.
"""

import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'src'))
from marcel_tools.hub import DEFAULT_HUB_URL, Transport, UrllibTransport  # noqa: E402

OPEN_STATES = 'queued,starting,working,needs_you,silent'
MEMORY_BYTES = 8 * 1024
TAIL_BYTES = 256 * 1024  # how much of the transcript's end is read for the last exchange
SAID_CHARS = 600  # each side of the last exchange is cut here
TIMEOUT = 5.0  # seconds per call; the hook itself gets 15 s (hooks.json)
NOT_A_BRAIN = {'not_brain', 'unknown_session'}
# A brain that has just started may call before the hub knows its id (the runner answers the
# spawn only once the channel is registered): on start or resume, ask again this often, 1 s apart.
START_RETRIES = 5
RETRY_WAIT_S = 1.0
HANDOFF = Path('.marcel') / 'handoff.md'
NO_SUMMARY = "The hub has no summary of today or of the live sessions yet (it can't give one)."
HEADINGS = {
    'compact': 'Your context was just compacted. What you keep across compactions:',
    'resume': 'This session was resumed. Where things stand:',
}
STARTED = 'Where things stand as this session starts:'


class Unreachable(Exception):
    """The hub could not be asked. The message says why, in one sentence."""


class NotABrain(Exception):
    """The brain API refused this session: it is not Marcel's brain or a side thread."""


def memory(cwd: Path) -> str | None:
    try:
        raw = (cwd / 'MEMORY.md').read_bytes()
    except OSError:
        return None
    text = raw[:MEMORY_BYTES].decode('utf-8', errors='replace').strip()
    if len(raw) > MEMORY_BYTES:
        text += '\n… (cut at 8 KB; read MEMORY.md for the rest)'
    return text or None


def secret(env: Mapping[str, str]) -> str:
    data_dir = env.get('CLAUDE_PLUGIN_DATA', '')
    if not data_dir:
        raise Unreachable('CLAUDE_PLUGIN_DATA is not set, so the hub secret cannot be found.')
    try:
        value = (Path(data_dir) / 'hub-secret').read_text().strip()
    except OSError as exc:
        raise Unreachable(f'the hub secret could not be read ({exc.strerror}).') from exc
    if not value:
        raise Unreachable('the hub secret file is empty.')
    return value


class Brain:
    """The two `/brain` reads these hooks need."""

    def __init__(
        self,
        transport: Transport,
        session_id: str,
        secret: str,
        retries: int = 0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.transport = transport
        self.retries = retries
        self.sleep = sleep
        self.headers = {
            'X-Marcel-Secret': secret,
            'X-Marcel-Session': session_id,
            'Accept': 'application/json',
        }

    def get(self, path: str, params: dict[str, str] | None = None) -> tuple[int, Any]:
        while True:
            try:
                return self._get_once(path, params)
            except NotABrain as exc:
                if str(exc) != 'unknown_session' or self.retries <= 0:
                    raise
                self.retries -= 1
                print(
                    'brain_context: the hub does not know this session yet; asking again in '
                    f'{RETRY_WAIT_S:.0f} s.',
                    file=sys.stderr,
                )
                self.sleep(RETRY_WAIT_S)

    def _get_once(self, path: str, params: dict[str, str] | None) -> tuple[int, Any]:
        try:
            status, body = self.transport.request(
                'GET', '/brain' + path, params=params, body=None, headers=self.headers
            )
        except OSError as exc:
            raise Unreachable(f'the hub is not answering ({exc}).') from exc
        code = body.get('code') if isinstance(body, dict) else None
        if status in (401, 403) and code in NOT_A_BRAIN:
            raise NotABrain(code)
        return status, body

    def open_threads(self) -> list[str]:
        status, body = self.get('/tasks', {'state': OPEN_STATES})
        if status != 200 or not isinstance(body, dict):
            message = body.get('message') if isinstance(body, dict) else None
            raise Unreachable(message or f'the hub answered {status}.')
        items = [t for t in body.get('items', []) if isinstance(t, dict)]
        return [
            f'{t.get("id", "?")} {t.get("state", "?")} {t.get("title", "")}'.strip() for t in items
        ]

    def summary(self) -> str | None:
        status, body = self.get('/summary')
        if status == 200 and isinstance(body, dict) and body.get('text'):
            return str(body['text']).strip()
        return None


def _text(content: Any) -> str:
    """The plain text of a transcript message's `content` (a string, or a list of blocks)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text'
        ]
        return '\n'.join(p for p in parts if p).strip()
    return ''


def last_exchange(transcript: str | None) -> tuple[str, str]:
    """The last message to the session and its own last words, from the end of the transcript.
    The JSONL format is Claude Code's own and may change: anything unreadable gives ''."""
    said = {'user': '', 'assistant': ''}
    if not transcript:
        return '', ''
    try:
        with open(transcript, 'rb') as f:
            f.seek(max(0, f.seek(0, os.SEEK_END) - TAIL_BYTES))
            lines = f.read().decode('utf-8', errors='replace').splitlines()
    except OSError:
        return '', ''
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # the first line of the tail may be cut in half
        message = entry.get('message') if isinstance(entry, dict) else None
        if isinstance(message, dict) and entry.get('type') in said:
            text = _text(message.get('content'))
            if text:
                said[entry['type']] = text
    cut = {k: v if len(v) <= SAID_CHARS else v[:SAID_CHARS] + ' …' for k, v in said.items()}
    return cut['user'], cut['assistant']


def connect(
    hook: Mapping[str, Any],
    env: Mapping[str, str],
    transport: Transport | None,
    retries: int = 0,
    sleep: Callable[[float], None] = time.sleep,
) -> Brain:
    session_id = str(hook.get('session_id') or '')
    if not session_id:
        raise Unreachable('the hook gave no session id.')
    url = env.get('MARCEL_HUB_URL') or DEFAULT_HUB_URL
    transport = transport or UrllibTransport(url, TIMEOUT)
    return Brain(transport, session_id, secret(env), retries, sleep)


def handoff(
    hook: Mapping[str, Any],
    env: Mapping[str, str],
    transport: Transport | None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Path | None:
    """`PreCompact`: write the hand-over; None (and nothing written) for a session that is not
    Marcel's brain, or outside a brain workspace (no `MEMORY.md`): with the hub down a worker cannot
    be told from the brain, and a stray file must never land in a repository."""
    cwd = Path(str(hook.get('cwd') or os.getcwd()))
    if not (cwd / 'MEMORY.md').exists():
        return None
    try:
        threads = connect(hook, env, transport).open_threads()
        open_part = '\n'.join(threads) if threads else 'None.'
    except NotABrain:
        return None
    except Unreachable as exc:
        open_part = f'Marcel’s hub could not be asked: {exc}'
    asked, answered = last_exchange(hook.get('transcript_path'))
    trigger = 'you asked for it' if hook.get('trigger') == 'manual' else 'the context was full'
    text = (
        f'Compacted at {now().strftime("%Y-%m-%d %H:%M")} UTC because {trigger}.\n\n'
        f'### Open threads then\n\n{open_part}\n\n'
        f'### The last message to you\n\n{asked or "(none found)"}\n\n'
        f'### What you last said\n\n{answered or "(none found)"}\n'
    )
    path = cwd / HANDOFF
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return path


def compose(
    hook: Mapping[str, Any],
    env: Mapping[str, str],
    transport: Transport | None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """`SessionStart`: what to print. Empty for a session that is not Marcel's brain. On start
    or resume, a session the hub does not know yet is asked about again (`START_RETRIES`)."""
    cwd = Path(str(hook.get('cwd') or os.getcwd()))
    source = str(hook.get('source') or '')
    parts = [HEADINGS.get(source, STARTED)]
    saved = cwd / HANDOFF
    if source == 'compact' and saved.is_file():
        parts.append('## Hand-over from before the compaction\n\n' + saved.read_text().strip())
    notes = memory(cwd)
    if notes:
        parts.append('## MEMORY.md\n\n' + notes)
    try:
        retries = 0 if source == 'compact' else START_RETRIES
        brain = connect(hook, env, transport, retries, sleep)
        threads = brain.open_threads()
        parts.append('## Open threads\n\n' + ('\n'.join(threads) if threads else 'None.'))
        parts.append('## Today and the live sessions\n\n' + (brain.summary() or NO_SUMMARY))
    except NotABrain:
        return ''
    except Unreachable as exc:
        parts.append(f'Marcel’s hub could not be asked for the open threads: {exc}')
    if source == 'compact':
        saved.unlink(missing_ok=True)  # given once; an old hand-over never comes back
    return '\n\n'.join(parts) + '\n'


def main(stdin: TextIO = sys.stdin, env: Mapping[str, str] = os.environ) -> int:
    try:
        hook = json.loads(stdin.read() or '{}')
        hook = hook if isinstance(hook, dict) else {}
        if hook.get('hook_event_name') == 'PreCompact':
            handoff(hook, env, None)
        else:
            print(compose(hook, env, None), end='')
    except Exception as exc:  # never block the session over this hook
        print(f'brain_context: {exc}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
