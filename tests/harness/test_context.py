"""Tests for harness/context.py — MarcelDeps, build_instructions, and server context."""

from __future__ import annotations

import pytest

from marcel_core.harness.context import (
    MarcelDeps,
    build_instructions,
    build_instructions_async,
    build_server_context,
)
from marcel_core.storage import _root

# ---------------------------------------------------------------------------
# build_server_context
# ---------------------------------------------------------------------------


class TestBuildServerContext:
    def test_returns_string(self):
        result = build_server_context()
        assert isinstance(result, str)
        assert '## Server context' in result

    def test_includes_working_directory(self):
        result = build_server_context(cwd='/some/path')
        assert '/some/path' in result

    def test_includes_home_directory(self):
        from pathlib import Path

        result = build_server_context()
        assert str(Path.home()) in result

    def test_includes_hostname_when_available(self, monkeypatch):
        from pathlib import Path

        original_read_text = Path.read_text

        def patched_read_text(self, *args, **kwargs):
            if str(self) == '/etc/hostname':
                return 'myserver\n'
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, 'read_text', patched_read_text)
        result = build_server_context()
        assert 'myserver' in result

    def test_hostname_oserror_is_swallowed(self, monkeypatch):
        """OSError reading /etc/hostname is silently skipped."""
        from pathlib import Path

        original_read_text = Path.read_text

        def patched_read_text(self, *args, **kwargs):
            if str(self) == '/etc/hostname':
                raise OSError('no permission')
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, 'read_text', patched_read_text)
        result = build_server_context()
        assert '## Server context' in result

    def test_docker_socket_shown_when_available(self, monkeypatch):
        from pathlib import Path

        original_exists = Path.exists

        def patched_exists(self):
            if str(self) == '/var/run/docker.sock':
                return True
            return original_exists(self)

        monkeypatch.setattr(Path, 'exists', patched_exists)
        result = build_server_context()
        assert 'docker' in result.lower()


# ---------------------------------------------------------------------------
# build_instructions
# ---------------------------------------------------------------------------


class TestBuildInstructions:
    def test_includes_user_slug(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='alice', conversation_id='conv-1', channel='cli')
        result = build_instructions(deps)
        assert 'alice' in result

    def test_includes_channel_hint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='bob', conversation_id='conv-1', channel='telegram')
        result = build_instructions(deps)
        assert 'telegram' in result.lower()

    def test_cli_channel_hint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='bob', conversation_id='conv-1', channel='cli')
        result = build_instructions(deps)
        assert 'markdown' in result.lower()

    def test_admin_role_includes_server_context(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='admin', conversation_id='conv-1', channel='cli', role='admin')
        result = build_instructions(deps)
        assert '## Server context' in result

    def test_user_role_excludes_server_context(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='alice', conversation_id='conv-1', channel='cli', role='user')
        result = build_instructions(deps)
        assert 'Server context' not in result

    def test_includes_profile_when_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        # Write a profile file
        user_dir = tmp_path / 'users' / 'carol'
        user_dir.mkdir(parents=True)
        (user_dir / 'profile.md').write_text('Carol is a data scientist.', encoding='utf-8')

        deps = MarcelDeps(user_slug='carol', conversation_id='conv-1', channel='app')
        result = build_instructions(deps)
        assert 'Carol is a data scientist.' in result

    def test_unknown_channel_falls_back_to_cli_hint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='bob', conversation_id='conv-1', channel='unknown-channel')
        result = build_instructions(deps)
        assert isinstance(result, str)
        assert 'bob' in result

    @pytest.mark.parametrize('channel', ['cli', 'app', 'ios', 'telegram', 'websocket'])
    def test_all_known_channels(self, tmp_path, monkeypatch, channel):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='user', conversation_id='conv-1', channel=channel)
        result = build_instructions(deps)
        assert isinstance(result, str)
        assert len(result) > 10


# ---------------------------------------------------------------------------
# build_instructions_async
# ---------------------------------------------------------------------------


class TestBuildInstructionsAsync:
    @pytest.mark.asyncio
    async def test_includes_user_slug(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='dan', conversation_id='conv-1', channel='cli')
        result = await build_instructions_async(deps, query='hello')
        assert 'Dan' in result

    @pytest.mark.asyncio
    async def test_emits_five_h1_blocks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='dan', conversation_id='conv-1', channel='cli')
        result = await build_instructions_async(deps)
        # The five blocks, in order
        for header in (
            '# Marcel — who you are',
            '# Dan — who the user is',
            '# Skills — what you can do',
            '# Memory — what you should know',
            '# Cli — how to respond',
        ):
            assert header in result
        # And they appear in the expected order
        positions = [
            result.index(h)
            for h in (
                '# Marcel — who you are',
                '# Dan — who the user is',
                '# Skills — what you can do',
                '# Memory — what you should know',
                '# Cli — how to respond',
            )
        ]
        assert positions == sorted(positions)

    @pytest.mark.asyncio
    async def test_memory_index_replaces_full_dump(self, tmp_path, monkeypatch):
        """Memory section should be a compact index, not raw file bodies."""
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        mem_dir = tmp_path / 'users' / 'dan' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'family.md').write_text(
            "---\nname: family\ndescription: Family members\n---\nCosette is Dan's partner. Secret.\n"
        )

        deps = MarcelDeps(user_slug='dan', conversation_id='conv-1', channel='cli')
        result = await build_instructions_async(deps, query='tell me about my family')

        # Index shows name + description
        assert '**family**' in result
        assert 'Family members' in result
        # Body content is NOT pre-dumped — must be loaded via read_memory
        assert 'Cosette' not in result
        # Hint directing the agent to use the tools
        assert 'read_memory' in result
        assert 'search_memory' in result

    @pytest.mark.asyncio
    async def test_admin_server_context_folded_under_user_block(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='admin', conversation_id='conv-1', channel='cli', role='admin')
        result = await build_instructions_async(deps)

        # Server context is present as an H2 (not H1)
        assert '## Server context' in result

        # And it appears AFTER the user H1 and BEFORE the next H1
        user_h1 = result.index('# Admin — who the user is')
        server_h2 = result.index('## Server context')
        skill_h1 = result.index('# Skills — what you can do')
        assert user_h1 < server_h2 < skill_h1

    @pytest.mark.asyncio
    async def test_non_admin_omits_server_context(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        deps = MarcelDeps(user_slug='alice', conversation_id='conv-1', channel='cli', role='user')
        result = await build_instructions_async(deps)
        assert '## Server context' not in result

    @pytest.mark.asyncio
    async def test_profile_h1_stripped_before_wrapping(self, tmp_path, monkeypatch):
        """A profile.md that begins with '# Shaun' should NOT produce a duplicate H1."""
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        user_dir = tmp_path / 'users' / 'shaun'
        user_dir.mkdir(parents=True)
        (user_dir / 'profile.md').write_text('# Shaun\n\nRegular Marcel user.\n', encoding='utf-8')

        deps = MarcelDeps(user_slug='shaun', conversation_id='conv-1', channel='cli')
        result = await build_instructions_async(deps)

        # The wrapper H1 should be present exactly once under the Shaun block
        shaun_block_start = result.index('# Shaun — who the user is')
        next_block_start = result.index('# Skills')
        shaun_block = result[shaun_block_start:next_block_start]

        # Only the wrapper H1 — not the profile's own '# Shaun'
        assert shaun_block.count('# Shaun') == 1
        assert 'Regular Marcel user.' in shaun_block

    @pytest.mark.asyncio
    async def test_rich_ui_channel_includes_a2ui_catalog(self, tmp_path, monkeypatch):
        import marcel_core.skills.loader as loader

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        skills_root = tmp_path / 'skills'
        banking_dir = skills_root / 'banking'
        banking_dir.mkdir(parents=True)
        (banking_dir / 'SKILL.md').write_text('---\nname: banking\ndescription: Banking\n---\n\nBody.')
        (banking_dir / 'components.yaml').write_text(
            'components:\n'
            '  - name: transaction_list\n'
            '    description: List of bank transactions\n'
            '    props:\n'
            '      type: object\n'
            '      properties:\n'
            '        transactions:\n'
            '          type: array\n'
        )
        monkeypatch.setattr(loader, '_skills_dir', lambda: skills_root)

        deps = MarcelDeps(user_slug='shaun', conversation_id='conv-1', channel='telegram')
        result = await build_instructions_async(deps)

        assert '## A2UI Components' in result
        assert 'transaction_list' in result
        assert 'marcel(action="render"' in result

    @pytest.mark.asyncio
    async def test_cli_channel_omits_a2ui_catalog(self, tmp_path, monkeypatch):
        import marcel_core.skills.loader as loader

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        skills_root = tmp_path / 'skills'
        banking_dir = skills_root / 'banking'
        banking_dir.mkdir(parents=True)
        (banking_dir / 'SKILL.md').write_text('---\nname: banking\ndescription: Banking\n---\n\nBody.')
        (banking_dir / 'components.yaml').write_text(
            'components:\n  - name: transaction_list\n    description: x\n    props: {}\n'
        )
        monkeypatch.setattr(loader, '_skills_dir', lambda: skills_root)

        deps = MarcelDeps(user_slug='shaun', conversation_id='conv-1', channel='cli')
        result = await build_instructions_async(deps)

        assert '## A2UI Components' not in result
