"""Execution stack unit tests (FEAT-260718-38235c).

Pins Marcel's wiring: bubblewrap routing in SandboxedShellToolset, the
FileSystem protected patterns as a second layer under the policy guard,
CodeMode orchestration of eligible tools, and Monty's host rejection.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from marcel_core.capabilities.execution import FilteredFileSystem, SandboxedShell
from marcel_core.composition import (
    CODE_MODE_ELIGIBLE,
    FILESYSTEM_PROTECTED_PATTERNS,
    FILESYSTEM_TOOL_NAMES,
    SHELL_TOOL_NAMES,
    build_capabilities,
)
from marcel_core.harness.context import MarcelDeps, TurnState
from marcel_core.storage import _root


def _deps(role: str = 'admin') -> MarcelDeps:
    return MarcelDeps(
        user_slug='alice',
        conversation_id='alice:cli',
        channel='cli',
        role=role,
        turn=TurnState(event_bus=None),
    )


def _shell_toolset(cwd):
    return SandboxedShell(cwd=str(cwd), denied_commands=[]).get_toolset()


class TestSandboxRouting:
    def test_command_wrapped_in_bwrap_when_available(self, tmp_path):
        toolset = _shell_toolset(tmp_path)
        with (
            patch('marcel_core.harness.sandbox.sandbox_available', return_value=True),
            patch(
                'marcel_core.harness.sandbox.build_bwrap_argv',
                return_value=['/usr/bin/bwrap', '--unshare-user'],
            ) as argv_mock,
        ):
            actual, _cwd_file = toolset._build_cwd_capture('echo hi')
        assert actual.startswith('/usr/bin/bwrap --unshare-user bash -c ')
        assert 'echo hi' in actual
        assert argv_mock.call_args.kwargs['workspace'] == tmp_path

    def test_plain_command_when_sandbox_unavailable(self, tmp_path):
        toolset = _shell_toolset(tmp_path)
        with patch('marcel_core.harness.sandbox.sandbox_available', return_value=False):
            actual, _ = toolset._build_cwd_capture('echo hi')
        assert actual == 'echo hi'

    @pytest.mark.asyncio
    async def test_start_command_is_also_sandboxed(self, tmp_path):
        """Regression: the background spawn path (start_command) must be
        wrapped too — the harness spawns it directly without touching
        _build_cwd_capture, so it was a real bypass (security review)."""
        toolset = _shell_toolset(tmp_path)
        wrapped: list[str] = []

        async def _fake_super_start(self, command: str) -> str:  # noqa: ANN001
            wrapped.append(command)
            return 'started'

        with (
            patch('marcel_core.harness.sandbox.sandbox_available', return_value=True),
            patch('marcel_core.harness.sandbox.build_bwrap_argv', return_value=['/usr/bin/bwrap', '--unshare-net']),
            patch(
                'pydantic_ai_harness.shell._toolset.ShellToolset.start_command',
                _fake_super_start,
            ),
        ):
            await toolset.start_command('sleep 60')

        assert wrapped and wrapped[0].startswith('/usr/bin/bwrap --unshare-net bash -c ')
        assert 'sleep 60' in wrapped[0]


class TestCompositionRoleAxes:
    def test_admin_gets_shell_and_filesystem(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        names = {type(c).__name__ for c in build_capabilities(role='admin', cwd=str(tmp_path))}
        assert {'SandboxedShell', 'FilteredFileSystem', 'CodeMode'} <= names

    def test_user_role_gets_codemode_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        names = {type(c).__name__ for c in build_capabilities(role='user')}
        assert 'CodeMode' in names
        assert 'SandboxedShell' not in names and 'FilteredFileSystem' not in names

    def test_empty_filter_grants_no_admin_capabilities(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        names = {type(c).__name__ for c in build_capabilities(role='admin', tool_filter=set())}
        assert 'SandboxedShell' not in names and 'FilteredFileSystem' not in names

    def test_legacy_bash_filter_grants_full_shell(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        caps = build_capabilities(role='admin', tool_filter={'bash'})
        shells = [c for c in caps if isinstance(c, SandboxedShell)]
        assert shells and not any(isinstance(c, FilteredFileSystem) for c in caps)
        # legacy 'bash' → the full shell surface, not a narrowed subset
        assert shells[0].allowed_tools is None

    def test_read_only_subagent_filter_excludes_write(self, tmp_path, monkeypatch):
        """A read-only explorer (``tools: [read_file]``) must not gain
        write_file/edit_file when FileSystem is attached (role-gating)."""
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        caps = build_capabilities(role='admin', tool_filter={'read_file', 'web'})
        fs = next(c for c in caps if isinstance(c, FilteredFileSystem))
        assert fs.allowed_tools == frozenset({'read_file'})
        assert not any(isinstance(c, SandboxedShell) for c in caps)

    def test_admin_gate_covers_capability_tools(self):
        from marcel_core.harness.agent import admin_tool_names

        assert SHELL_TOOL_NAMES | FILESYSTEM_TOOL_NAMES <= admin_tool_names()


class TestFileSystemProtectedPatterns:
    def test_self_mod_paths_are_in_the_protected_set(self):
        """The composition roots FileSystem with the restricted self-mod
        paths; every pattern is depth-agnostic (`**/`) because the admin
        root is $HOME on the primary Telegram path."""
        assert '**/CLAUDE.md' in FILESYSTEM_PROTECTED_PATTERNS
        assert '**/src/marcel_core/config.py' in FILESYSTEM_PROTECTED_PATTERNS
        assert '**/.git/**' in FILESYSTEM_PROTECTED_PATTERNS
        assert '**/.claude/**' in FILESYSTEM_PROTECTED_PATTERNS
        assert all(p.startswith('**/') for p in FILESYSTEM_PROTECTED_PATTERNS)

    @pytest.mark.asyncio
    async def test_capability_refuses_a_protected_write_at_depth(self, tmp_path):
        """AC1 layer 2: FilteredFileSystem refuses a write to a self-mod
        path on its own — independent of the policy guard — and the depth
        of the file under the root does not matter ($HOME-rooted case).
        Driven by a scripted model so the write_file arg is a real path.
        """
        from odile import call_tool, reply
        from odile.script import ScriptedModel
        from pydantic_ai import Agent

        from marcel_core.capabilities.execution import FilteredFileSystem
        from marcel_core.composition import FILESYSTEM_PROTECTED_PATTERNS

        nested = tmp_path / 'projects' / 'marcel'
        nested.mkdir(parents=True)
        (nested / 'CLAUDE.md').write_text('# rules\n')

        fs = FilteredFileSystem(
            root_dir=str(tmp_path),
            protected_patterns=list(FILESYSTEM_PROTECTED_PATTERNS),
        )
        agent = Agent(
            ScriptedModel(
                call_tool('write_file', path='projects/marcel/CLAUDE.md', content='evil'),
                reply('refused'),
            ),
            deps_type=MarcelDeps,
            capabilities=[fs],  # no MarcelPolicy — this is layer 2 in isolation
        )
        result = await agent.run('overwrite the rules', deps=_deps())

        assert (nested / 'CLAUDE.md').read_text() == '# rules\n', 'protected file untouched'
        rendered = str(result.all_messages()).lower()
        assert 'protected' in rendered or 'not allowed' in rendered or 'denied' in rendered


class TestCodeModeEligibility:
    def test_eligible_set_is_conservative(self):
        """Only tools whose own body is safe to invoke from model-written
        code are eligible: no shell/approval/dispatcher, and NOT
        generate_chart (it execs model input outside every sandbox)."""
        assert CODE_MODE_ELIGIBLE == {'web'}
        assert not CODE_MODE_ELIGIBLE & SHELL_TOOL_NAMES
        assert 'generate_chart' not in CODE_MODE_ELIGIBLE
        assert 'toolkit' not in CODE_MODE_ELIGIBLE
        assert 'marcel' not in CODE_MODE_ELIGIBLE

    @pytest.mark.asyncio
    async def test_run_code_orchestrates_two_tools(self):
        """AC3: one run_code script drives two distinct wrapped tools in a
        single call. Purpose-built local tools (the prod eligible set is
        network-bound `web`); Monty actually executes the async calls."""
        from odile import call_tool, reply
        from odile.script import ScriptedModel
        from pydantic_ai import Agent
        from pydantic_ai_harness.code_mode import CodeMode

        calls: list[str] = []

        agent = Agent(
            ScriptedModel(
                call_tool(
                    'run_code',
                    code=("a = await alpha(x=3)\nb = await beta(x=a)\nf'{a},{b}'"),
                ),
                reply('done'),
            ),
            deps_type=MarcelDeps,
            capabilities=[CodeMode(tools=['alpha', 'beta'])],
        )

        @agent.tool_plain
        async def alpha(x: int) -> int:
            calls.append('alpha')
            return x + 1

        @agent.tool_plain
        async def beta(x: int) -> int:
            calls.append('beta')
            return x * 10

        result = await agent.run('compute', deps=_deps('user'))
        assert result.output == 'done'
        assert calls == ['alpha', 'beta'], 'both distinct tools ran inside one run_code'

    def test_code_mode_off_for_lean_paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from pydantic_ai_harness.code_mode import CodeMode

        caps = build_capabilities(role='user', code_mode=False)
        assert not any(isinstance(c, CodeMode) for c in caps)
