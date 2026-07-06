"""Marcel's scenario-test binding of the odile framework — the *terrarium*.

`odile <https://github.com/shbunder/odile>`_ (*Orchestrated Doubles for
Isolated LLM Exercises*, a sibling repo) provides the generic pieces:
:class:`~odile.ScriptedModel` doubles, in-process fake APIs, and the
no-network pytest guard. This package is the **kernel-coupled glue** that
seals a Marcel world around them — a *terrarium*: temporary data root,
controlled zoo, snapshot/restore of the kernel's process globals, event-bus
capture, and human-free approval resolution — so scenario tests drive the
**real** :func:`~marcel_core.harness.runner.stream_turn` end to end.

It lives in the kernel repo (and ships in the wheel, so zoo habitats and
extension authors import the same harness) because it touches kernel
internals that must move in lockstep with the kernel. Decision records:
ADR-260706-b88015 (model seam), ADR-260706 odile split.

The odile primitives are re-exported for one-import scenarios::

    from marcel_testing import call_tool, reply

    async def test_greeting(terrarium):
        scenario = terrarium.scenario(reply('Hello!'), user='alice')
        result = await scenario.run('hi marcel')
        assert result.reply == 'Hello!'
"""

from odile import FakeAPI, ScriptedModel, ScriptError, call_tool, reply

from marcel_testing.terrarium import Scenario, Terrarium, TurnResult

__all__ = [
    'FakeAPI',
    'Scenario',
    'ScriptError',
    'ScriptedModel',
    'Terrarium',
    'TurnResult',
    'call_tool',
    'reply',
]

__version__ = '0.1.0'
