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

# Maximum output length before truncation (characters).
# Matches ClawCode's default (30K). Large outputs are stored in paste store.
MAX_OUTPUT_LENGTH = 30000

# Threshold for offloading large bash output to the paste store.
# Above this, only a head+tail preview is kept in history.
_BASH_PASTE_THRESHOLD = 15000

# Project root — src/marcel_core/tools/core.py → parents[3] = project root
_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])


def _effective_cwd(ctx: RunContext[MarcelDeps]) -> str:
    """Return the effective working directory for this request.

    Uses the cwd from deps if set (admin CLI sessions send the caller's pwd;
    admin non-CLI sessions default to $HOME). Falls back to the project root.
    """
    return ctx.deps.cwd or _PROJECT_ROOT


async def _run_bash(command: str, cwd: str | None, timeout: int) -> tuple[int | None, bytes, bytes]:
    """Execute *command*, sandboxed when available, else unsandboxed.

    Routes through the bubblewrap workspace-write sandbox
    (:mod:`marcel_core.harness.sandbox`) when it is enabled and actually
    startable on this host; otherwise runs the command directly (the command
    policy still gates dangerous actions). Returns ``(returncode, stdout,
    stderr)``; raises :class:`asyncio.TimeoutError` on timeout.
    """
    from marcel_core.config import settings
    from marcel_core.harness.sandbox import run_sandboxed, sandbox_available

    # The writable workspace is the effective cwd; the repo root is the
    # fallback when the session has no cwd.
    workspace = Path(cwd) if cwd else Path(__file__).resolve().parents[3]

    if settings.marcel_sandbox_enabled and sandbox_available():
        return await run_sandboxed(
            command,
            workspace=workspace,
            cwd=Path(cwd) if cwd else workspace,
            data_dir=settings.data_dir,
            timeout=timeout,
            allow_network=settings.marcel_sandbox_network,
        )

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
        raise
    return proc.returncode, stdout, stderr


async def _git_shell(ctx: RunContext[MarcelDeps], command: str, timeout: int = 120) -> str:
    """Run a shell command for the git tools (private — not a registered tool).

    The old ``bash`` tool body, kept for the git_* tools which shell out.
    The model-facing shell surface is the SandboxedShell capability
    (FEAT-260718-38235c). Runs inside the bubblewrap workspace-write sandbox when available
    (writes confined to the cwd, self-mod boundary read-only); falls back to
    a direct run where unprivileged user namespaces are unavailable. Either
    way the command policy classifies the command before it reaches here.

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
        returncode, stdout, stderr = await _run_bash(command, cwd, timeout)
    except asyncio.TimeoutError:
        return f'Error: Command timed out after {timeout}s'
    except Exception as exc:
        log.exception('[bash] execution failed')
        return f'Error executing command: {exc}'

    output = stdout.decode('utf-8', errors='replace')
    if stderr:
        error_text = stderr.decode('utf-8', errors='replace')
        output = f'{output}\n[stderr]\n{error_text}' if output else error_text

    if returncode != 0:
        output = f'Exit code {returncode}\n{output}'

    if len(output) > MAX_OUTPUT_LENGTH:
        output = output[:MAX_OUTPUT_LENGTH] + f'\n\n[Output truncated: {len(output)} chars total]'
    elif len(output) > _BASH_PASTE_THRESHOLD:
        # Keep a head+tail preview for context; full output is in JSONL
        head = output[:500]
        tail = output[-500:]
        output = f'{head}\n\n... [{len(output)} chars total — middle omitted for brevity] ...\n\n{tail}'

    return output or '(no output)'


async def git_status(ctx: RunContext[MarcelDeps]) -> str:
    """Show git working tree status.

    Returns:
        Git status output.
    """
    return await _git_shell(ctx, 'git status')


async def git_diff(ctx: RunContext[MarcelDeps], paths: str = '') -> str:
    """Show git diff for staged and unstaged changes.

    Args:
        ctx: Agent context.
        paths: Optional file paths to limit diff (space-separated).

    Returns:
        Git diff output.
    """
    cmd = f'git diff HEAD {paths}'.strip()
    return await _git_shell(ctx, cmd)


async def git_log(ctx: RunContext[MarcelDeps], limit: int = 10) -> str:
    """Show recent git commit history.

    Args:
        ctx: Agent context.
        limit: Number of commits to show (default: 10).

    Returns:
        Git log output.
    """
    return await _git_shell(ctx, f'git log --oneline -{limit}')


async def git_add(ctx: RunContext[MarcelDeps], paths: str) -> str:
    """Stage files for commit.

    Args:
        ctx: Agent context.
        paths: File paths to stage (space-separated).

    Returns:
        Command output or error.
    """
    return await _git_shell(ctx, f'git add {paths}')


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
    return await _git_shell(ctx, cmd)


async def git_push(ctx: RunContext[MarcelDeps], remote: str = 'origin', branch: str = 'HEAD') -> str:
    """Push commits to remote repository.

    Args:
        ctx: Agent context.
        remote: Remote name (default: 'origin').
        branch: Branch to push (default: 'HEAD' = current branch).

    Returns:
        Command output or error.
    """
    return await _git_shell(ctx, f'git push {remote} {branch}')
