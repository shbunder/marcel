"""Execution-stack scenarios — CodeMode through the real loop (FEAT-260718-38235c)."""

from __future__ import annotations

from odile import call_tool, reply


class TestProtectedPathRefusedByThePolicyGuard:
    """Scenario: a write_file to a self-mod-protected path is denied by the
    MarcelPolicy self-mod guard (feature AC 1, layer 1).

    Layer 2 — the FileSystem capability's own ``protected_patterns`` — is
    unit-asserted in tests/capabilities/execution/test_execution.py; the two
    layers together are the defense-in-depth the feature requires.
    """

    async def test_write_to_claude_md_denied(self, terrarium):
        terrarium.user('root', role='admin')
        scenario = terrarium.scenario(
            call_tool('write_file', path='CLAUDE.md', content='evil'),
            reply('cannot modify that'),
            user='root',
            channel='cli',
        )
        result = await scenario.run('overwrite the rules file')

        # A policy deny surfaces its reason as the tool result (the MarcelPolicy
        # contract): the write never runs, the model sees the unlock guidance.
        completion = next(c for c in result.completions if c.tool_name == 'write_file')
        assert 'unlock' in completion.result.lower() or 'restricted' in completion.result.lower()
        # The tool never executed — no tool_result event carrying a written path.
        assert not any(e.tool_name == 'write_file' for e in result.tool_results)


class TestMontyRejectsHostAccess:
    """Scenario: generated code cannot reach the host (feature AC 4)."""

    async def test_file_io_and_imports_rejected(self, terrarium):
        terrarium.user('alice')
        scenario = terrarium.scenario(
            call_tool('run_code', code="open('/etc/passwd').read()"),
            call_tool('run_code', code='import socket'),
            reply('cannot do that'),
            user='alice',
            channel='cli',
        )
        result = await scenario.run('read the passwd file')

        completions = [c for c in result.completions if c.tool_name == 'run_code']
        assert len(completions) == 2
        for completion in completions:
            assert completion.is_error, completion.result
        assert 'passwd' not in ''.join(c.result for c in completions if 'root:' in c.result)
