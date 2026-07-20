"""Channel interaction profiles (FEAT-260707-89a886, STORY-260707-aa82d6)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from marcel_core.channels.adapter import (
    ChannelCapabilities,
    channel_interaction_profile,
    seal_session_if_needed,
)


class TestProfileDeclaration:
    def test_default_is_continuous(self):
        assert ChannelCapabilities().interaction_profile == 'continuous'

    def test_session_is_valid(self):
        assert ChannelCapabilities(interaction_profile='session').interaction_profile == 'session'

    def test_invalid_profile_fails_loud(self):
        with pytest.raises(ValueError, match='continuous.*session'):
            ChannelCapabilities(interaction_profile='ephemeral')


class TestResolver:
    def test_builtin_session_channels(self):
        assert channel_interaction_profile('cli') == 'session'
        assert channel_interaction_profile('websocket') == 'session'

    def test_builtin_continuous_channels(self):
        assert channel_interaction_profile('telegram') == 'continuous'
        assert channel_interaction_profile('app') == 'continuous'
        assert channel_interaction_profile('unknown-channel') == 'continuous'

    def test_registered_plugin_profile_wins(self, monkeypatch):
        # A registered plugin declaring session overrides the builtin default.
        monkeypatch.setattr(
            'marcel_core.plugin.channels.channel_registered_interaction_profile',
            lambda name: 'session' if name == 'telegram' else None,
        )
        assert channel_interaction_profile('telegram') == 'session'


class TestSessionEndSeal:
    @pytest.mark.asyncio
    async def test_session_channel_seals(self):
        with (
            patch('marcel_core.storage.conversation.has_active_content', return_value=True),
            patch(
                'marcel_core.memory.summarizer.summarize_active_segment',
                AsyncMock(return_value=True),
            ) as seal,
        ):
            assert await seal_session_if_needed('shaun', 'cli') is True
        seal.assert_awaited_once_with('shaun', 'cli', trigger='session_end')

    @pytest.mark.asyncio
    async def test_continuous_channel_does_not_seal(self):
        with patch(
            'marcel_core.memory.summarizer.summarize_active_segment',
            AsyncMock(return_value=True),
        ) as seal:
            assert await seal_session_if_needed('shaun', 'telegram') is False
        seal.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_seal_when_nothing_to_summarize(self):
        with (
            patch('marcel_core.storage.conversation.has_active_content', return_value=False),
            patch(
                'marcel_core.memory.summarizer.summarize_active_segment',
                AsyncMock(return_value=True),
            ) as seal,
        ):
            assert await seal_session_if_needed('shaun', 'cli') is False
        seal.assert_not_awaited()
