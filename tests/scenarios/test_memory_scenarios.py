"""Memory capability scenarios — Marcel takes notes (FEAT-260718-30d45a)."""

from __future__ import annotations

from odile import call_tool, reply


class TestAgentWritesANote:
    """Scenario: the model writes a memory during a turn and it persists.

    The write flows through the real loop — policy-gated, ledgered by the
    persistence store — and lands as a file in the user's pre-capability
    memory directory.
    """

    async def test_write_memory_persists_a_file(self, terrarium):
        from marcel_core.storage.memory import parse_frontmatter

        terrarium.user('alice')
        note = '---\nname: garden\ndescription: Garden plan\ntype: reference\n---\nTomatoes go in the garden in June.\n'
        scenario = terrarium.scenario(
            call_tool('write_memory', content=note, file='garden.md'),
            reply('Noted!'),
            user='alice',
            channel='cli',
        )
        result = await scenario.run('remember: tomatoes in June')

        assert result.reply == 'Noted!'
        path = terrarium.data_root / 'users' / 'alice' / 'memory' / 'garden.md'
        assert path.exists()
        text = path.read_text()
        assert 'Tomatoes go in the garden in June.' in text
        metadata, body = parse_frontmatter(text)
        assert metadata.get('type') == 'reference', 'the existing frontmatter format round-trips'
        assert [e.tool_name for e in result.tool_calls] == ['write_memory']

    async def test_note_written_in_one_conversation_is_injected_in_the_next(self, terrarium):
        """Feature AC 1, the full seam: the model writes its notebook in
        conversation one; conversation two's model request carries it in the
        injected <memory> snapshot."""
        from pydantic_ai import Agent
        from pydantic_ai.capabilities.hooks import Hooks
        from pydantic_ai.models.test import TestModel

        from marcel_core.composition import build_capabilities
        from marcel_core.harness.context import MarcelDeps, TurnState

        terrarium.user('alice')
        s1 = terrarium.scenario(
            call_tool('write_memory', content='- garden: tomatoes go in during June'),
            reply('Noted in my notebook!'),
            user='alice',
            channel='cli',
        )
        await s1.run('remember: tomatoes in June')
        notebook = terrarium.data_root / 'users' / 'alice' / 'memory' / 'MEMORY.md'
        assert notebook.exists() and 'tomatoes' in notebook.read_text()

        seen: list[str] = []
        hooks = Hooks()

        @hooks.on.before_model_request
        async def capture(ctx, request_context):
            seen.append(str(request_context.messages))
            return request_context

        agent = Agent(
            TestModel(call_tools=[]),
            deps_type=MarcelDeps,
            capabilities=[*build_capabilities(), hooks],
        )
        deps = MarcelDeps(user_slug='alice', conversation_id='alice:cli', channel='cli', role='user', turn=TurnState())
        await agent.run('what goes in the garden?', deps=deps)

        assert '<memory>' in seen[0]
        assert 'tomatoes go in during June' in seen[0], 'the note from conversation one is injected'


class TestMemoryIsUserScoped:
    """Scenario: another user cannot reach a private memory, but the shared
    household notebook is readable for everyone."""

    async def test_bob_cannot_read_alices_note_but_reads_household(self, terrarium):
        alice_mem = terrarium.data_root / 'users' / 'alice' / 'memory'
        alice_mem.mkdir(parents=True)
        (alice_mem / 'secret.md').write_text('Surprise party for Bob on Saturday.')
        household = terrarium.data_root / 'users' / '_household' / 'memory'
        household.mkdir(parents=True)
        (household / 'wifi.md').write_text('Password: hunter2')

        terrarium.user('bob')
        s1 = terrarium.scenario(
            call_tool('read_memory', file='secret.md'),
            reply('nothing there'),
            user='bob',
            channel='cli',
        )
        r1 = await s1.run('read the secret file')
        denial = next(c for c in r1.completions if c.tool_name == 'read_memory')
        assert 'Surprise party' not in denial.result

        s2 = terrarium.scenario(
            call_tool('read_memory', file='household.wifi.md'),
            reply('the wifi password is hunter2'),
            user='bob',
            channel='cli',
        )
        r2 = await s2.run('what is the wifi password?')
        shared = next(c for c in r2.completions if c.tool_name == 'read_memory')
        assert 'hunter2' in shared.result
