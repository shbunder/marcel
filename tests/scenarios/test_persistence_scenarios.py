"""Persistence scenarios — segment integrity across turns (FEAT-260718-ed6d63)."""

from __future__ import annotations

from odile import call_tool, reply

from marcel_core.toolkit import marcel_tool


class TestToolHistoryPersistsExactlyOnce:
    """Regression: a turn-1 tool entry must not be re-appended by turn 2.

    ``_extract_tool_history`` walks the extraction message list; fed
    ``all_messages()`` it re-extracted the converted history prefix every
    turn, so each turn re-appended every historical tool entry to the
    segment — compounding growth masked only by rotation and sealing
    (STORY-260718-bfb1ac). ``new_messages()`` is the correct delta.
    """

    async def test_no_reappend_on_later_turns(self, terrarium):
        @marcel_tool('probe.echo')
        async def echo(params: dict, user_slug: str) -> str:
            return 'probe-result'

        terrarium.user('alice')
        s1 = terrarium.scenario(
            call_tool('toolkit', id='probe.echo', params={}),
            reply('turn one done'),
            user='alice',
            channel='cli',
        )
        await s1.run('use the probe')

        s2 = terrarium.scenario(reply('turn two done'), user='alice', channel='cli')
        await s2.run('just chat')

        from marcel_core.memory.conversation import read_active_segment

        msgs = read_active_segment('alice', 'cli')
        tool_entries = [m for m in msgs if m.role == 'tool']
        assistant_tool_calls = [m for m in msgs if m.role == 'assistant' and m.tool_calls]
        assert len(tool_entries) == 1, f'expected 1 tool entry after 2 turns, found {len(tool_entries)}'
        assert len(assistant_tool_calls) == 1
