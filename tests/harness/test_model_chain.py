"""Tests for harness/model_chain.py — per-tier fallback chain (ISSUE-e0db47)."""

from __future__ import annotations

from typing import Literal

import pytest

from marcel_core.config import settings
from marcel_core.harness.model_chain import (
    TIER_BY_INDEX,
    TIER_INDEX,
    Tier,
    build_chain,
    build_explain_system_prompt,
    build_explain_user_prompt,
    is_fallback_eligible,
    next_tier,
    tier_from_index,
)


@pytest.fixture(autouse=True)
def _reset_chain_settings(monkeypatch):
    """Start every test with a clean chain config so explicit monkeypatches
    are the only source of tier state."""
    monkeypatch.setattr(settings, 'marcel_fast_model', 'anthropic:claude-haiku-4-5-20251001')
    monkeypatch.setattr(settings, 'marcel_fast_backup_model', None)
    monkeypatch.setattr(settings, 'marcel_standard_model', 'anthropic:claude-sonnet-4-6')
    monkeypatch.setattr(settings, 'marcel_standard_backup_model', None)
    monkeypatch.setattr(settings, 'marcel_power_model', 'anthropic:claude-opus-4-6')
    monkeypatch.setattr(settings, 'marcel_power_backup_model', None)
    monkeypatch.setattr(settings, 'marcel_fallback_model', None)
    monkeypatch.setattr(settings, 'marcel_local_llm_url', None)
    monkeypatch.setattr(settings, 'marcel_local_llm_model', None)


class TestBuildChain:
    def test_minimal_chain_is_primary_only(self):
        chain = build_chain(mode='explain')
        assert len(chain) == 1
        assert chain[0].tier == Tier.STANDARD
        assert chain[0].model == 'anthropic:claude-sonnet-4-6'
        assert chain[0].purpose == 'primary'

    def test_full_chain_explain_mode(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_standard_backup_model', 'openai:gpt-4o')
        monkeypatch.setattr(settings, 'marcel_fallback_model', 'local:qwen3.5:4b')
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        monkeypatch.setattr(settings, 'marcel_local_llm_model', 'qwen3.5:4b')

        chain = build_chain(mode='explain')
        assert [e.tier for e in chain] == [Tier.STANDARD, Tier.STANDARD, Tier.LOCAL]
        assert [e.purpose for e in chain] == ['primary', 'backup', 'explain']
        assert chain[1].model == 'openai:gpt-4o'
        assert chain[2].model == 'local:qwen3.5:4b'

    def test_full_chain_complete_mode_for_jobs(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_standard_backup_model', 'openai:gpt-4o')
        monkeypatch.setattr(settings, 'marcel_fallback_model', 'local:qwen3.5:4b')
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        monkeypatch.setattr(settings, 'marcel_local_llm_model', 'qwen3.5:4b')

        chain = build_chain(mode='complete')
        assert chain[-1].purpose == 'complete'

    def test_skips_local_fallback_when_llm_url_missing(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, 'marcel_fallback_model', 'local:qwen3.5:4b')
        monkeypatch.setattr(settings, 'marcel_local_llm_url', None)
        monkeypatch.setattr(settings, 'marcel_local_llm_model', 'qwen3.5:4b')

        with caplog.at_level('WARNING', logger='marcel_core.harness.model_chain'):
            chain = build_chain(mode='explain')

        assert [e.tier for e in chain] == [Tier.STANDARD]
        assert any('MARCEL_LOCAL_LLM_URL' in r.getMessage() for r in caplog.records), (
            'expected a warning naming MARCEL_LOCAL_LLM_URL'
        )

    def test_skips_local_fallback_when_llm_model_missing(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_fallback_model', 'local:qwen3.5:4b')
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        monkeypatch.setattr(settings, 'marcel_local_llm_model', None)

        chain = build_chain(mode='explain')
        assert [e.tier for e in chain] == [Tier.STANDARD]

    def test_non_local_fallback_does_not_require_llm_config(self, monkeypatch):
        """A cloud model used as the shared fallback doesn't need MARCEL_LOCAL_LLM_*."""
        monkeypatch.setattr(settings, 'marcel_fallback_model', 'openai:gpt-4o-mini')

        chain = build_chain(mode='explain')
        assert [e.tier for e in chain] == [Tier.STANDARD, Tier.LOCAL]
        assert chain[-1].model == 'openai:gpt-4o-mini'

    def test_primary_override_replaces_primary_slot_only(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_standard_backup_model', 'openai:gpt-4o')

        chain = build_chain(primary='anthropic:claude-haiku-4-5-20251001', mode='explain')
        assert chain[0].model == 'anthropic:claude-haiku-4-5-20251001'
        assert chain[0].tier == Tier.STANDARD
        assert chain[0].purpose == 'primary'
        assert chain[1].model == 'openai:gpt-4o'
        assert chain[1].tier == Tier.STANDARD
        assert chain[1].purpose == 'backup'

    def test_fast_tier_uses_fast_primary_and_backup(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_fast_backup_model', 'openai:gpt-4o-mini')

        chain = build_chain(tier=Tier.FAST, mode='explain')
        assert [e.tier for e in chain] == [Tier.FAST, Tier.FAST]
        assert chain[0].model == 'anthropic:claude-haiku-4-5-20251001'
        assert chain[0].purpose == 'primary'
        assert chain[1].model == 'openai:gpt-4o-mini'
        assert chain[1].purpose == 'backup'

    def test_power_tier_uses_power_primary_and_backup(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_power_backup_model', 'openai:o1')

        chain = build_chain(tier=Tier.POWER, mode='explain')
        assert [e.tier for e in chain] == [Tier.POWER, Tier.POWER]
        assert chain[0].model == 'anthropic:claude-opus-4-6'
        assert chain[1].model == 'openai:o1'

    def test_per_tier_backup_is_isolated(self, monkeypatch):
        """FAST backup must not leak into the STANDARD chain and vice versa."""
        monkeypatch.setattr(settings, 'marcel_fast_backup_model', 'openai:gpt-4o-mini')
        monkeypatch.setattr(settings, 'marcel_standard_backup_model', 'openai:gpt-4o')

        fast_chain = build_chain(tier=Tier.FAST, mode='explain')
        std_chain = build_chain(tier=Tier.STANDARD, mode='explain')

        assert fast_chain[1].model == 'openai:gpt-4o-mini'
        assert std_chain[1].model == 'openai:gpt-4o'

    def test_local_tier_single_entry_no_backup_no_fallback(self, monkeypatch):
        """LOCAL is the last-resort tier — its chain is exactly one entry."""
        monkeypatch.setattr(settings, 'marcel_fallback_model', 'local:qwen3.5:4b')
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        monkeypatch.setattr(settings, 'marcel_local_llm_model', 'qwen3.5:4b')

        chain = build_chain(tier=Tier.LOCAL, mode='explain')
        assert [e.tier for e in chain] == [Tier.LOCAL]
        assert chain[0].model == 'local:qwen3.5:4b'
        assert chain[0].purpose == 'primary'

    def test_fallback_tier_defaults_to_local(self, monkeypatch):
        """Default fallback_tier reads marcel_fallback_model (historical behavior)."""
        monkeypatch.setattr(settings, 'marcel_standard_backup_model', None)
        monkeypatch.setattr(settings, 'marcel_fallback_model', 'local:qwen3.5:4b')
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        monkeypatch.setattr(settings, 'marcel_local_llm_model', 'qwen3.5:4b')

        chain = build_chain(tier=Tier.STANDARD, mode='explain')
        assert [e.tier for e in chain] == [Tier.STANDARD, Tier.LOCAL]
        assert chain[-1].model == 'local:qwen3.5:4b'

    def test_fallback_tier_can_be_cloud_tier(self, monkeypatch):
        """Admin can set fallback_tier=FAST so the tail is a cloud haiku, not local."""
        monkeypatch.setattr(settings, 'marcel_fast_model', 'anthropic:claude-haiku-4-5-20251001')
        monkeypatch.setattr(settings, 'marcel_standard_backup_model', None)
        monkeypatch.setattr(settings, 'marcel_fallback_model', None)

        chain = build_chain(tier=Tier.STANDARD, mode='explain', fallback_tier=Tier.FAST)
        assert [e.tier for e in chain] == [Tier.STANDARD, Tier.FAST]
        assert chain[-1].model == 'anthropic:claude-haiku-4-5-20251001'
        assert chain[-1].purpose == 'explain'

    def test_fallback_tier_equal_to_caller_tier_is_skipped(self, monkeypatch):
        """No point tailing the chain with the same tier that just failed."""
        monkeypatch.setattr(settings, 'marcel_fast_model', 'anthropic:claude-haiku-4-5-20251001')
        monkeypatch.setattr(settings, 'marcel_fast_backup_model', None)
        monkeypatch.setattr(settings, 'marcel_fallback_model', None)

        chain = build_chain(tier=Tier.FAST, mode='explain', fallback_tier=Tier.FAST)
        assert [e.tier for e in chain] == [Tier.FAST]

    def test_fallback_tier_power_is_rejected(self):
        """POWER is never admin-selectable — build_chain mirrors AdminTierConfig's guard."""
        with pytest.raises(ValueError, match='fallback_tier cannot be POWER'):
            build_chain(tier=Tier.STANDARD, fallback_tier=Tier.POWER)


class TestIsFallbackEligible:
    def test_overloaded_is_eligible(self):
        eligible, category = is_fallback_eligible('Overloaded')
        assert eligible is True
        assert category == 'server_error'

    def test_rate_limit_is_eligible(self):
        eligible, category = is_fallback_eligible('rate_limit exceeded 429')
        assert eligible is True
        assert category == 'rate_limit'

    def test_auth_quota_is_eligible(self):
        eligible, category = is_fallback_eligible('401 Unauthorized')
        assert eligible is True  # not transient but still chain-eligible
        assert category == 'auth_or_quota'

    def test_permanent_is_not_eligible(self):
        eligible, category = is_fallback_eligible('ValidationError: field required')
        assert eligible is False
        assert category == 'permanent'


class TestNextTier:
    def _full_chain(self, monkeypatch, mode: Literal['explain', 'complete'] = 'explain'):
        monkeypatch.setattr(settings, 'marcel_standard_backup_model', 'openai:gpt-4o')
        monkeypatch.setattr(settings, 'marcel_fallback_model', 'local:qwen3.5:4b')
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        monkeypatch.setattr(settings, 'marcel_local_llm_model', 'qwen3.5:4b')
        return build_chain(mode=mode)

    def test_advances_from_primary_to_backup(self, monkeypatch):
        chain = self._full_chain(monkeypatch)
        nxt = next_tier(chain, chain[0], 'server_error')
        assert nxt is not None
        assert nxt.tier == Tier.STANDARD
        assert nxt.purpose == 'backup'

    def test_advances_from_backup_to_explain(self, monkeypatch):
        chain = self._full_chain(monkeypatch)
        nxt = next_tier(chain, chain[1], 'server_error')
        assert nxt is not None
        assert nxt.tier == Tier.LOCAL
        assert nxt.purpose == 'explain'

    def test_returns_none_at_end_of_chain(self, monkeypatch):
        chain = self._full_chain(monkeypatch)
        nxt = next_tier(chain, chain[-1], 'server_error')
        assert nxt is None

    def test_permanent_error_blocks_advancing_into_explain(self, monkeypatch):
        """Permanent errors (validation, tool crash) should not surface a
        canned apology — the real error message is more useful."""
        chain = self._full_chain(monkeypatch)
        # Fail tier 2 with a permanent error; we should NOT advance to explain.
        nxt = next_tier(chain, chain[1], 'permanent')
        assert nxt is None

    def test_permanent_error_does_not_block_complete_mode_fallback(self, monkeypatch):
        """In job mode the fallback is a completion attempt, not an apology —
        permanent errors don't auto-short-circuit (the caller's own
        eligibility check will catch them)."""
        chain = self._full_chain(monkeypatch, mode='complete')
        nxt = next_tier(chain, chain[1], 'permanent')
        assert nxt is not None
        assert nxt.tier == Tier.LOCAL
        assert nxt.purpose == 'complete'


class TestExplainPromptSynthesis:
    def test_system_prompt_caps_error_length(self):
        big_error = 'A' * 2000
        prompt = build_explain_system_prompt(big_error, 'server_error')
        # Error body should be truncated; full prompt must not be absurdly large.
        assert 'AAAA' in prompt
        assert len(prompt) < 2000  # the original error would have blown this

    def test_system_prompt_uses_first_line_of_error(self):
        err = 'first line\nsecond line\nthird line'
        prompt = build_explain_system_prompt(err, 'server_error')
        assert 'first line' in prompt
        assert 'second line' not in prompt

    def test_system_prompt_includes_category(self):
        prompt = build_explain_system_prompt('anything', 'rate_limit')
        assert '[rate_limit]' in prompt

    def test_user_prompt_quotes_user_text(self):
        prompt = build_explain_user_prompt('what is the weather')
        assert 'what is the weather' in prompt
        assert 'Tell them what happened' in prompt

    def test_user_prompt_truncates_long_messages(self):
        long_msg = 'x' * 2000
        prompt = build_explain_user_prompt(long_msg)
        assert '…' in prompt
        # Rough bound: truncated content (500) + wrapper text
        assert len(prompt) < 800


class TestTierIndex:
    def test_public_indexing_is_0_to_3(self):
        assert TIER_INDEX[Tier.LOCAL] == 0
        assert TIER_INDEX[Tier.FAST] == 1
        assert TIER_INDEX[Tier.STANDARD] == 2
        assert TIER_INDEX[Tier.POWER] == 3

    def test_reverse_mapping_round_trips(self):
        for tier, idx in TIER_INDEX.items():
            assert TIER_BY_INDEX[idx] == tier
            assert tier_from_index(idx) == tier

    @pytest.mark.parametrize('bad_index', [-1, 4, 99])
    def test_tier_from_index_rejects_out_of_range(self, bad_index):
        with pytest.raises(ValueError, match='unknown tier index'):
            tier_from_index(bad_index)
