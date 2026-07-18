"""Execution stack unit tests (FEAT-260718-38235c).

Pins Marcel's wiring: bubblewrap routing in SandboxedShellToolset, the
FileSystem protected patterns as a second layer under the policy guard,
CodeMode orchestration of eligible tools, and Monty's host rejection.
"""

from __future__ import annotations

from unittest.mock import patch

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
        """AC1 wiring: the composition roots FileSystem with the restricted
        self-mod paths, so the capability refuses those writes on its own —
        a second layer under the MarcelPolicy guard. The end-to-end refusal
        is exercised by the Terrarium scenario (both-layers assertion)."""
        assert 'CLAUDE.md' in FILESYSTEM_PROTECTED_PATTERNS
        assert 'src/marcel_core/config.py' in FILESYSTEM_PROTECTED_PATTERNS
        assert any('auth' in p for p in FILESYSTEM_PROTECTED_PATTERNS)
        assert '.env' in FILESYSTEM_PROTECTED_PATTERNS


class TestCodeModeEligibility:
    def test_eligible_set_is_conservative(self):
        """Approval-gated and dispatcher tools stay direct; only read-only-ish
        orchestration candidates move behind run_code (ADR-260718-d511f7)."""
        assert CODE_MODE_ELIGIBLE == {'web', 'generate_chart'}
        assert not CODE_MODE_ELIGIBLE & SHELL_TOOL_NAMES
        assert 'toolkit' not in CODE_MODE_ELIGIBLE
        assert 'marcel' not in CODE_MODE_ELIGIBLE
