"""Harness Shell routed through Marcel's bubblewrap sandbox.

The harness ``Shell`` capability is explicitly *not* a security boundary —
its allow/deny lists are a speed bump. Marcel keeps its real containment:
every spawned command is wrapped in the bubblewrap workspace-write sandbox
(:mod:`marcel_core.harness.sandbox`) when it is enabled and startable on
this host, exactly as the retired ``bash`` tool was. When the sandbox is
unavailable the command runs unconfined — the same documented fallback as
before (the command policy still gates dangerous actions in front, via
MarcelPolicy).

The seam: ``ShellToolset`` builds one final command *string* per spawn in
``_build_cwd_capture`` and passes it to ``anyio.open_process`` (shell
form). Wrapping that string with the shell-joined bwrap argv routes every
foreground and background command through the sandbox without forking the
harness implementation.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai.tools import ToolDefinition
from pydantic_ai_harness.shell import Shell
from pydantic_ai_harness.shell._toolset import ShellToolset

from marcel_core.config import settings


class SandboxedShellToolset(ShellToolset):
    """ShellToolset whose spawned commands run inside bubblewrap.

    Both spawn paths are covered: ``run_command`` (via ``_build_cwd_capture``,
    the only place the harness calls it) and ``start_command`` (the harness
    spawns the raw command directly, so we override it to wrap too). A
    background command missing the sandbox was a real bypass — see
    STORY-260718-bcb643.
    """

    def _bwrap_wrap(self, command: str) -> str:
        """Wrap ``command`` in the bwrap argv when the sandbox is startable.

        Mirrors ``run_sandboxed()``: the bwrap argv + ``bash -c <cmd>``,
        shell-joined because ShellToolset spawns a shell string. Returns the
        command unchanged when the sandbox is disabled or unavailable (the
        documented fallback — the command policy still gates in front).
        """
        if not settings.marcel_sandbox_enabled:
            return command
        from marcel_core.harness.sandbox import build_bwrap_argv, sandbox_available

        if not sandbox_available():
            return command
        workspace = Path(self._cwd)
        argv = build_bwrap_argv(
            workspace=workspace,
            cwd=workspace,
            data_dir=settings.data_dir,
            allow_network=settings.marcel_sandbox_network,
        )
        return shlex.join([*argv, 'bash', '-c', command])

    def _build_cwd_capture(self, command: str) -> tuple[str, Path | None]:
        actual, cwd_file = super()._build_cwd_capture(command)
        return self._bwrap_wrap(actual), cwd_file

    async def start_command(self, command: str) -> str:
        # The harness spawns the background command directly (no
        # _build_cwd_capture), so wrap it here — otherwise background
        # commands run with the marcel process's full ambient authority.
        return await super().start_command(self._bwrap_wrap(command))


@dataclass
class SandboxedShell(Shell):
    """The harness Shell capability with bubblewrap-routed execution.

    ``allowed_tools`` narrows the toolset for constrained subagents (same
    role-gating contract as FilteredFileSystem); ``None`` exposes the full
    run/start/check/stop set for the main admin agent.
    """

    allowed_tools: frozenset[str] | None = field(default=None)

    def get_toolset(self) -> Any:
        toolset = SandboxedShellToolset(
            cwd=Path(self.cwd),
            allowed_commands=self.allowed_commands,
            denied_commands=self.denied_commands,
            denied_operators=self.denied_operators,
            default_timeout=self.default_timeout,
            max_output_chars=self.max_output_chars,
            persist_cwd=self.persist_cwd,
            allow_interactive=self.allow_interactive,
            env=self.env,
            denied_env_patterns=self.denied_env_patterns,
        )
        if self.allowed_tools is None:
            return toolset
        allowed = self.allowed_tools

        def _keep(ctx: object, td: ToolDefinition) -> bool:
            return td.name in allowed

        return toolset.filtered(_keep)
