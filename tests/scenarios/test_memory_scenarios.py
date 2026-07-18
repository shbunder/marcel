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
        terrarium.user('alice')
        scenario = terrarium.scenario(
            call_tool('write_memory', content='Tomatoes go in the garden in June.', file='garden.md'),
            reply('Noted!'),
            user='alice',
            channel='cli',
        )
        result = await scenario.run('remember: tomatoes in June')

        assert result.reply == 'Noted!'
        path = terrarium.data_root / 'users' / 'alice' / 'memory' / 'garden.md'
        assert path.exists()
        assert 'Tomatoes go in the garden in June.' in path.read_text()
        assert [e.tool_name for e in result.tool_calls] == ['write_memory']


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
