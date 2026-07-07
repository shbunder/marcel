"""Marcel's terrarium as an odile binding — added with FEAT-260707-99fc13.

The behavior-preservation proof for the rebase is that the *pre-existing*
suites (``test_terrarium.py``, ``tests/scenarios/``, the zoo parks) pass
unmodified; this file only covers what the rebase added — the subclass
relationship and the strict-kwargs guards that keep typo'd options loud.
"""

from __future__ import annotations

import pytest
from odile import Scenario as OdileScenario, Terrarium as OdileTerrarium, TurnResult as OdileTurnResult, reply

from marcel_testing import Scenario, Terrarium, TurnResult


class TestOdileBinding:
    def test_the_constructs_are_odile_subclasses(self):
        assert issubclass(Terrarium, OdileTerrarium)
        assert issubclass(Scenario, OdileScenario)
        assert issubclass(TurnResult, OdileTurnResult)

    def test_unknown_scenario_option_is_rejected(self, terrarium):
        with pytest.raises(TypeError, match='unknown scenario option.*channnel.*supported: model, user, channel'):
            terrarium.scenario(reply('hi'), channnel='cli')

    async def test_unknown_run_option_is_rejected(self, terrarium):
        scenario = terrarium.scenario(reply('hi'))
        with pytest.raises(TypeError, match='unknown run option.*conversation'):
            await scenario.run('hello', converation_id='typo')
