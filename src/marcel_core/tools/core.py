"""Core tools for Marcel — bash, file operations, and git commands.

These tools give Marcel direct system access for server management and
simple code modifications. For complex multi-file refactoring, Marcel should
delegate to the claude-code CLI tool.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps

log = logging.getLogger(__name__)

# Maximum output length before truncation (characters)
MAX_OUTPUT_LENGTH = 50000

# Project root — src/marcel_core/tools/core.py → parents[3] = project root
_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])


def _effective_cwd(ctx: RunContext[MarcelDeps]) -> str:
    """Return the effective working directory for this request.

    Uses the cwd from deps if set (admin CLI sessions send the caller's pwd;
    admin non-CLI sessions default to $HOME). Falls back to the project root.
    """
    return ctx.deps.cwd or _PROJECT_ROOT


async def bash(ctx: RunContext[MarcelDeps], command: str, timeout: int = 120) -> str:
    """Execute a bash command on the server.

    Use this for system commands, package management, service control, etc.
    For complex code tasks, use the claude_code tool instead.

    Args:
        ctx: Agent context with user and conversation info.
        command: The bash command to execute.
        timeout: Maximum execution time in seconds (default: 120).

    Returns:
        Command output (stdout + stderr combined).
    """
    cwd = _effective_cwd(ctx)
    log.info('[bash] user=%s cwd=%s cmd=%s', ctx.deps.user_slug, cwd, command[:100])

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f'Error: Command timed out after {timeout}s'

        output = stdout.decode('utf-8', errors='replace')
        if stderr:
            error_text = stderr.decode('utf-8', errors='replace')
            output = f'{output}\n[stderr]\n{error_text}' if output else error_text

        if proc.returncode != 0:
            output = f'Exit code {proc.returncode}\n{output}'

        if len(output) > MAX_OUTPUT_LENGTH:
            output = output[:MAX_OUTPUT_LENGTH] + f'\n\n[Output truncated: {len(output)} chars total]'

        return output or '(no output)'

    except Exception as exc:
        log.exception('[bash] execution failed')
        return f'Error executing command: {exc}'


async def read_file(ctx: RunContext[MarcelDeps], path: str, offset: int = 0, limit: int | None = None) -> str:
    """Read file contents from the server.

    Args:
        ctx: Agent context.
        path: Absolute or relative path to the file.
        offset: Line number to start reading from (0-indexed).
        limit: Maximum number of lines to read (None = all).

    Returns:
        File contents with line numbers, or error message.
    """
    log.info('[read_file] user=%s path=%s', ctx.deps.user_slug, path)

    try:
        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = Path(_effective_cwd(ctx)) / path

        if not file_path.exists():
            return f'Error: File not found: {path}'

        if file_path.is_dir():
            return f'Error: {path} is a directory. Use bash with ls to list contents.'

        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Apply offset and limit
        if offset > 0:
            lines = lines[offset:]
        if limit is not None:
            lines = lines[:limit]

        # Add line numbers (starting from offset)
        numbered = [f'{i + offset + 1:5d}\t{line}' for i, line in enumerate(lines)]
        content = ''.join(numbered)

        if len(content) > MAX_OUTPUT_LENGTH:
            content = content[:MAX_OUTPUT_LENGTH] + '\n\n[Output truncated: use offset/limit parameters]'

        return content

    except Exception as exc:
        log.exception('[read_file] failed')
        return f'Error reading file: {exc}'


async def write_file(ctx: RunContext[MarcelDeps], path: str, content: str) -> str:
    """Write content to a file (create or overwrite).

    Args:
        ctx: Agent context.
        path: Absolute or relative path to the file.
        content: Content to write.

    Returns:
        Success message or error.
    """
    log.info('[write_file] user=%s path=%s len=%d', ctx.deps.user_slug, path, len(content))

    try:
        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = Path(_effective_cwd(ctx)) / path

        # Create parent directories if needed
        file_path.parent.mkdir(parents=True, exist_ok=True)

        file_path.write_text(content, encoding='utf-8')
        return f'✓ Wrote {len(content)} characters to {path}'

    except Exception as exc:
        log.exception('[write_file] failed')
        return f'Error writing file: {exc}'


async def edit_file(ctx: RunContext[MarcelDeps], path: str, old_string: str, new_string: str) -> str:
    """Edit a file by replacing exact string match.

    Args:
        ctx: Agent context.
        path: Absolute or relative path to the file.
        old_string: The exact text to replace (must match exactly).
        new_string: The replacement text.

    Returns:
        Success message or error.
    """
    log.info('[edit_file] user=%s path=%s', ctx.deps.user_slug, path)

    try:
        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = Path(_effective_cwd(ctx)) / path

        if not file_path.exists():
            return f'Error: File not found: {path}'

        content = file_path.read_text(encoding='utf-8')

        if old_string not in content:
            return f'Error: old_string not found in {path}. Make sure the string matches exactly.'

        # Check if replacement is unique
        occurrences = content.count(old_string)
        if occurrences > 1:
            return (
                f'Error: old_string appears {occurrences} times in {path}. '
                f'Provide a larger unique string or use write_file to rewrite the entire file.'
            )

        new_content = content.replace(old_string, new_string, 1)
        file_path.write_text(new_content, encoding='utf-8')

        return f'✓ Replaced {len(old_string)} chars with {len(new_string)} chars in {path}'

    except Exception as exc:
        log.exception('[edit_file] failed')
        return f'Error editing file: {exc}'


async def git_status(ctx: RunContext[MarcelDeps]) -> str:
    """Show git working tree status.

    Returns:
        Git status output.
    """
    return await bash(ctx, 'git status')


async def git_diff(ctx: RunContext[MarcelDeps], paths: str = '') -> str:
    """Show git diff for staged and unstaged changes.

    Args:
        ctx: Agent context.
        paths: Optional file paths to limit diff (space-separated).

    Returns:
        Git diff output.
    """
    cmd = f'git diff HEAD {paths}'.strip()
    return await bash(ctx, cmd)


async def git_log(ctx: RunContext[MarcelDeps], limit: int = 10) -> str:
    """Show recent git commit history.

    Args:
        ctx: Agent context.
        limit: Number of commits to show (default: 10).

    Returns:
        Git log output.
    """
    return await bash(ctx, f'git log --oneline -{limit}')


async def git_add(ctx: RunContext[MarcelDeps], paths: str) -> str:
    """Stage files for commit.

    Args:
        ctx: Agent context.
        paths: File paths to stage (space-separated).

    Returns:
        Command output or error.
    """
    return await bash(ctx, f'git add {paths}')


async def git_commit(ctx: RunContext[MarcelDeps], message: str) -> str:
    """Create a git commit with staged changes.

    Args:
        ctx: Agent context.
        message: Commit message (will be properly quoted).

    Returns:
        Command output or error.
    """
    # Use heredoc for proper quoting
    cmd = f'git commit -m "$(cat <<\'EOF\'\n{message}\nEOF\n)"'
    return await bash(ctx, cmd)


async def git_push(ctx: RunContext[MarcelDeps], remote: str = 'origin', branch: str = 'HEAD') -> str:
    """Push commits to remote repository.

    Args:
        ctx: Agent context.
        remote: Remote name (default: 'origin').
        branch: Branch to push (default: 'HEAD' = current branch).

    Returns:
        Command output or error.
    """
    return await bash(ctx, f'git push {remote} {branch}')
