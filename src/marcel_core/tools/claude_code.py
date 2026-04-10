"""Claude Code delegation tool — delegate complex coding tasks to Claude Code CLI.

Marcel can handle simple file edits and bash commands directly, but for complex
multi-file refactoring, comprehensive testing, or tasks requiring careful code
review, it should delegate to Claude Code CLI which is specialized for those tasks.

Session relay
-------------
Claude Code may pause mid-task and ask the user a question via the AskUserQuestion
tool. When this happens, this tool returns a string prefixed with ``PAUSED:``:

    PAUSED:{session_id}:{question text}

Marcel's agent must relay the question to the user, wait for their answer, then
call this tool again with ``task=<user answer>`` and ``resume_session=<session_id>``.
This makes Marcel a session shell around Claude Code, proxying questions and answers
until the task completes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps
from marcel_core.tools.integration import notify

log = logging.getLogger(__name__)

# Maximum final-result length before truncation
MAX_OUTPUT_LENGTH = 100_000

# Accumulated text chars before we flush a mid-task progress notification
_NOTIFY_CHUNK = 400

# Return value prefix when Claude Code asks a question and is waiting for input
PAUSED_PREFIX = 'PAUSED:'

# Project root — src/marcel_core/tools/claude_code.py → parents[3] = project root
_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])


def _claude_binary() -> str:
    """Return the path to the claude CLI binary."""
    for name in ('claude', 'claude-code'):
        found = shutil.which(name)
        if found:
            return found

    return 'claude'  # will raise FileNotFoundError at runtime if missing


async def claude_code(
    ctx: RunContext[MarcelDeps],
    task: str,
    timeout: int = 600,
    resume_session: str | None = None,
) -> str:
    """Delegate a complex coding task to Claude Code CLI with streaming output.

    Use this tool when you need:
    - Multi-file refactoring across the codebase
    - Complex code changes that require careful testing
    - Tasks that benefit from Claude Code's specialised coding abilities
    - Code review or analysis of existing implementations

    For simple tasks (reading files, editing a single file, running bash commands),
    use the direct tools instead (read_file, edit_file, bash).

    Resuming a paused session
    -------------------------
    If this tool returns a string starting with ``PAUSED:``, Claude Code asked a
    question mid-task.  Extract the session_id and question from the return value,
    relay the question to the user, then call this tool again with:

    - ``task`` = the user's answer
    - ``resume_session`` = the session_id from the ``PAUSED:`` string

    Return value format when paused::

        PAUSED:{session_id}:{question text}

    Args:
        ctx: Agent context with user information.
        task: Coding task description, or the user's answer when resuming.
        timeout: Maximum execution time in seconds (default: 600 = 10 minutes).
        resume_session: Session ID to resume (from a prior ``PAUSED:`` return value).

    Returns:
        Claude Code's final output, or ``PAUSED:{session_id}:{question}`` if Claude
        Code needs user input before it can continue.
    """
    log.info('delegating to claude_code: user=%s resume=%s task=%.100s', ctx.deps.user_slug, resume_session, task)

    binary = _claude_binary()
    cmd = [binary, '-p']
    if resume_session:
        cmd += ['--resume', resume_session]
    cmd += [
        '--output-format',
        'stream-json',
        '--verbose',
        '--dangerously-skip-permissions',
        task,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_PROJECT_ROOT,
        )
    except FileNotFoundError:
        return (
            'Error: claude CLI not found. '
            'Make sure Claude Code is installed and in PATH.\n'
            'Install: npm install -g @anthropic-ai/claude-code'
        )
    except Exception as exc:
        log.exception('claude_code: failed to start subprocess')
        return f'Error starting Claude Code: {exc}'

    session_id: str | None = None
    result_text: str | None = None
    notify_buf: list[str] = []
    notify_len = 0

    async def _flush() -> None:
        nonlocal notify_buf, notify_len
        if notify_buf:
            text = ''.join(notify_buf).strip()
            if text:
                await notify(ctx, text[:500])
            notify_buf = []
            notify_len = 0

    try:
        async with asyncio.timeout(timeout):
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode('utf-8', errors='replace').strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event_type = event.get('type')

                # Capture session ID from the init event — needed for resume
                if event_type == 'system' and event.get('subtype') == 'init':
                    session_id = event.get('session_id')

                # Stream assistant text to user and intercept AskUserQuestion
                elif event_type == 'assistant':
                    for block in event.get('message', {}).get('content', []):
                        btype = block.get('type')

                        if btype == 'text':
                            chunk = block.get('text', '')
                            if chunk:
                                notify_buf.append(chunk)
                                notify_len += len(chunk)
                                if notify_len >= _NOTIFY_CHUNK:
                                    await _flush()

                        elif btype == 'tool_use' and block.get('name') == 'AskUserQuestion':
                            question = block.get('input', {}).get('question', '').strip()
                            if question and session_id:
                                log.info('claude_code paused — question: %s', question)
                                proc.kill()
                                return f'{PAUSED_PREFIX}{session_id}:{question}'

                # Task complete — grab the final result
                elif event_type == 'result':
                    result_text = event.get('result', '')
                    break

    except asyncio.TimeoutError:
        if proc.returncode is None:
            proc.kill()
        return f'Error: Claude Code task timed out after {timeout}s. Consider breaking it into smaller tasks.'
    finally:
        # Ensure the process is always reaped — covers normal exit, timeout,
        # and early PAUSED: returns so we never leave zombie processes.
        if proc.returncode is None:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
        await _flush()

    if proc.returncode not in (0, None, -9):
        stderr_data = b''
        if proc.stderr:
            try:
                stderr_data = await asyncio.wait_for(proc.stderr.read(), timeout=5)
            except asyncio.TimeoutError:
                pass
        err = stderr_data.decode('utf-8', errors='replace').strip()
        log.warning('claude_code: exit %d stderr=%s', proc.returncode, err[:200])

    output = result_text or '(no output from Claude Code)'
    if len(output) > MAX_OUTPUT_LENGTH:
        output = output[:MAX_OUTPUT_LENGTH] + f'\n\n[Output truncated: {len(output)} chars total]'
    return output
