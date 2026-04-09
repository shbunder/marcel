"""Claude Code delegation tool — delegate complex coding tasks to Claude Code CLI.

Marcel can handle simple file edits and bash commands directly, but for complex
multi-file refactoring, comprehensive testing, or tasks requiring careful code
review, it should delegate to Claude Code CLI which is specialized for those tasks.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps

log = logging.getLogger(__name__)

# Maximum output length before truncation
MAX_OUTPUT_LENGTH = 100000


async def claude_code(ctx: RunContext[MarcelDeps], task: str, timeout: int = 600) -> str:
    """Delegate a complex coding task to Claude Code CLI.

    Use this tool when you need:
    - Multi-file refactoring across the codebase
    - Complex code changes that require careful testing
    - Tasks that benefit from Claude Code's specialized coding abilities
    - Code review or analysis of existing implementations

    For simple tasks (reading files, editing a single file, running bash commands),
    use the direct tools instead (read_file, edit_file, bash).

    Args:
        ctx: Agent context with user information.
        task: Description of the coding task (will be passed as --message to claude-code).
        timeout: Maximum execution time in seconds (default: 600 = 10 minutes).

    Returns:
        Claude Code's output or error message.
    """
    log.info('[claude_code] user=%s task=%s', ctx.deps.user_slug, task[:100])

    # Build claude-code command
    # Use --message to pass the task, run from project root
    cmd = ['claude-code', '--message', task]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd='/home/sagemaker-user/projects/marcel',
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f'Error: Claude Code task timed out after {timeout}s. Consider breaking it into smaller tasks.'

        output = stdout.decode('utf-8', errors='replace')
        if stderr:
            error_text = stderr.decode('utf-8', errors='replace')
            if error_text.strip():
                output = f'{output}\n[stderr]\n{error_text}'

        if proc.returncode != 0:
            output = f'Claude Code exited with code {proc.returncode}\n\n{output}'

        if len(output) > MAX_OUTPUT_LENGTH:
            output = output[:MAX_OUTPUT_LENGTH] + f'\n\n[Output truncated: {len(output)} chars total]'

        return output or '(no output from Claude Code)'

    except FileNotFoundError:
        return (
            'Error: claude-code CLI not found. Make sure Claude Code is installed and in PATH.\n\n'
            'Install: npm install -g @anthropic-ai/claude-code'
        )
    except Exception as exc:
        log.exception('[claude_code] execution failed')
        return f'Error delegating to Claude Code: {exc}'
