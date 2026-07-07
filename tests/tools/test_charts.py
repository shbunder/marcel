"""Scenario-based tests for tools/charts.py — chart generation.

Tests generate_chart through different scenarios: valid charts, code errors,
empty figures, and Telegram delivery.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marcel_core.harness.context import MarcelDeps
from marcel_core.storage import _root
from marcel_core.tools.charts import generate_chart


def _ctx(channel: str = 'cli') -> MagicMock:
    deps = MarcelDeps(user_slug='alice', conversation_id='conv-1', channel=channel)
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)


class TestGenerateChart:
    @pytest.mark.asyncio
    async def test_basic_bar_chart(self):
        code = "plt.bar(['A', 'B', 'C'], [10, 20, 15]); plt.title('Test')"
        result = await generate_chart(_ctx(), code, title='Bar Chart')
        assert 'Artifact ID' in result

    @pytest.mark.asyncio
    async def test_code_error(self):
        result = await generate_chart(_ctx(), 'raise ValueError("bad code")')
        assert 'Error' in result

    @pytest.mark.asyncio
    async def test_empty_figure(self):
        result = await generate_chart(_ctx(), '# no plot commands')
        assert 'Error' in result
        assert 'No chart' in result

    @pytest.mark.asyncio
    async def test_telegram_delivery(self):
        code = 'plt.plot([1, 2, 3], [1, 4, 9])'
        with (
            patch('marcel_core.channels.telegram.sessions.get_chat_id', return_value='123'),
            patch('marcel_core.channels.telegram.bot.send_photo', new_callable=AsyncMock, return_value=1),
        ):
            result = await generate_chart(_ctx(channel='telegram'), code, title='Line')
        assert 'sent to Telegram' in result

    @pytest.mark.asyncio
    async def test_telegram_delivery_failure(self):
        code = 'plt.plot([1, 2], [3, 4])'
        with (
            patch('marcel_core.channels.telegram.sessions.get_chat_id', return_value='123'),
            patch(
                'marcel_core.channels.telegram.bot.send_photo',
                new_callable=AsyncMock,
                side_effect=RuntimeError('timeout'),
            ),
        ):
            result = await generate_chart(_ctx(channel='telegram'), code, title='Fail')
        assert 'failed to send' in result


# ---------------------------------------------------------------------------
# ratchet edges (STORY-260707-c332a7)
# ---------------------------------------------------------------------------


class TestStorageEdges:
    @pytest.mark.asyncio
    async def test_artifact_reload_miss_is_tolerated(self):
        # The artifact vanished between save and reload — chart still reports.
        code = 'plt.plot([1], [1])'
        with patch('marcel_core.storage.artifacts.load_artifact', return_value=None):
            result = await generate_chart(_ctx(), code, title='Ghost')
        assert 'Artifact ID' in result

    @pytest.mark.asyncio
    async def test_artifact_store_failure_reported(self):
        code = 'plt.plot([1], [1])'
        with patch('marcel_core.storage.artifacts.load_artifact', side_effect=RuntimeError('disk full')):
            result = await generate_chart(_ctx(), code, title='Doomed')
        assert 'failed to store' in result
        assert 'disk full' in result

    @pytest.mark.asyncio
    async def test_telegram_unlinked_user_falls_back_to_stored(self):
        # No linked chat → channel.send_photo returns False → stored message.
        # (bot-level delivery failure is masked by the zoo channel always
        # returning True — tracked as STORY-260707-98ff8f-f.)
        code = 'plt.plot([1, 2], [3, 4])'
        with patch('marcel_core.channels.telegram.sessions.get_chat_id', return_value=None):
            result = await generate_chart(_ctx(channel='telegram'), code, title='Quiet')
        assert 'generated and stored' in result
