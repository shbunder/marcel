"""Tests for harness/turn_router.py — slash prefixes + tier precedence (ISSUE-6a38cd)."""

from __future__ import annotations

import pytest

from marcel_core.config import settings
from marcel_core.harness.model_chain import Tier
from marcel_core.harness.turn_router import (
    POWER_REJECT_MESSAGE,
    RESERVED_PREFIXES,
    AdminTierConfig,
    TierSource,
    TurnPlan,
    resolve_turn,
)


DEFAULT_ADMIN = AdminTierConfig()


def _resolve(
    text: str,
    *,
    active_skill_tier: Tier | None = None,
    session_tier: Tier | None = None,
    admin_config: AdminTierConfig = DEFAULT_ADMIN,
    known_skills: tuple[str, ...] = (),
) -> TurnPlan:
    return resolve_turn(
        text,
        active_skill_tier=active_skill_tier,
        session_tier=session_tier,
        admin_config=admin_config,
        known_skills=known_skills,
    )


class TestAdminTierConfig:
    def test_defaults_are_local_fallback_and_fast_default(self) -> None:
        cfg = AdminTierConfig()
        assert cfg.fallback_tier == Tier.LOCAL
        assert cfg.default_tier == Tier.FAST

    def test_power_is_rejected_as_default_tier(self) -> None:
        with pytest.raises(ValueError, match='default_tier cannot be POWER'):
            AdminTierConfig(default_tier=Tier.POWER)

    def test_power_is_rejected_as_fallback_tier(self) -> None:
        with pytest.raises(ValueError, match='fallback_tier cannot be POWER'):
            AdminTierConfig(fallback_tier=Tier.POWER)

    def test_from_settings_reads_int_indexes(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, 'marcel_default_tier', 2)
        monkeypatch.setattr(settings, 'marcel_fallback_tier', 1)

        cfg = AdminTierConfig.from_settings()
        assert cfg.default_tier == Tier.STANDARD
        assert cfg.fallback_tier == Tier.FAST


class TestUserPrefix:
    @pytest.mark.parametrize(
        'prefix,expected_tier',
        [
            ('/local', Tier.LOCAL),
            ('/fast', Tier.FAST),
            ('/standard', Tier.STANDARD),
        ],
    )
    def test_tier_prefix_overrides_everything(self, prefix: str, expected_tier: Tier) -> None:
        plan = _resolve(
            f'{prefix} what is 2+2',
            active_skill_tier=Tier.POWER,  # would win normally
            session_tier=Tier.STANDARD,
        )
        assert plan.tier == expected_tier
        assert plan.cleaned_text == 'what is 2+2'
        assert plan.source == TierSource.USER_PREFIX
        assert plan.skill_override is None
        assert plan.reject_reason is None

    def test_prefix_is_case_insensitive(self) -> None:
        plan = _resolve('/FAST hello')
        assert plan.tier == Tier.FAST
        assert plan.cleaned_text == 'hello'

    def test_prefix_without_body_leaves_empty_cleaned_text(self) -> None:
        plan = _resolve('/fast')
        assert plan.tier == Tier.FAST
        assert plan.cleaned_text == ''

    def test_prefix_eats_extra_whitespace_between_cmd_and_body(self) -> None:
        plan = _resolve('/fast   hello   world')
        assert plan.tier == Tier.FAST
        assert plan.cleaned_text == 'hello   world'

    def test_prefix_preserves_newlines_in_body(self) -> None:
        plan = _resolve('/fast line one\nline two')
        assert plan.tier == Tier.FAST
        assert plan.cleaned_text == 'line one\nline two'

    def test_plain_text_does_not_consume_prefix(self) -> None:
        plan = _resolve('hello /fast')
        assert plan.tier == Tier.FAST or plan.source != TierSource.USER_PREFIX
        # Plain text path: falls through to default
        assert plan.source == TierSource.DEFAULT
        assert plan.cleaned_text == 'hello /fast'

    def test_leading_whitespace_defeats_prefix(self) -> None:
        plan = _resolve(' /fast hello')
        assert plan.source == TierSource.DEFAULT
        assert plan.cleaned_text == ' /fast hello'


class TestPowerReject:
    def test_power_prefix_is_rejected(self) -> None:
        plan = _resolve('/power give me opus')
        assert plan.reject_reason == POWER_REJECT_MESSAGE
        assert plan.cleaned_text == 'give me opus'
        # Fallback tier for rendering/logging: default_tier
        assert plan.tier == DEFAULT_ADMIN.default_tier

    def test_power_prefix_bare_is_rejected(self) -> None:
        plan = _resolve('/power')
        assert plan.reject_reason == POWER_REJECT_MESSAGE

    def test_power_reject_wins_over_active_skill(self) -> None:
        plan = _resolve('/power anything', active_skill_tier=Tier.POWER)
        assert plan.reject_reason == POWER_REJECT_MESSAGE


class TestSkillDispatch:
    def test_known_skill_triggers_dispatch(self) -> None:
        plan = _resolve(
            '/banking balance',
            known_skills=('banking', 'weather'),
            session_tier=Tier.FAST,
        )
        assert plan.skill_override == 'banking'
        assert plan.cleaned_text == 'balance'
        # Tier falls through to normal precedence (session_tier here)
        assert plan.tier == Tier.FAST
        assert plan.source == TierSource.SESSION

    def test_unknown_skill_falls_through_with_original_text(self) -> None:
        plan = _resolve(
            '/notaskill please help',
            known_skills=('banking',),
        )
        assert plan.skill_override is None
        assert plan.cleaned_text == '/notaskill please help'
        assert plan.tier == DEFAULT_ADMIN.default_tier

    def test_skill_lookup_is_case_insensitive(self) -> None:
        plan = _resolve('/Banking now', known_skills=('banking',))
        assert plan.skill_override == 'banking'
        assert plan.cleaned_text == 'now'

    def test_skill_name_with_hyphens_works(self) -> None:
        plan = _resolve('/morning-digest tomorrow', known_skills=('morning-digest',))
        assert plan.skill_override == 'morning-digest'
        assert plan.cleaned_text == 'tomorrow'

    def test_known_skill_cannot_shadow_tier_prefix(self) -> None:
        """Even if a skill is registered as 'fast', the tier prefix wins."""
        plan = _resolve('/fast hello', known_skills=('fast',))
        assert plan.skill_override is None
        assert plan.tier == Tier.FAST
        assert plan.source == TierSource.USER_PREFIX


class TestTierPrecedence:
    def test_active_skill_beats_session(self) -> None:
        plan = _resolve(
            'hello',
            active_skill_tier=Tier.POWER,
            session_tier=Tier.FAST,
        )
        assert plan.tier == Tier.POWER
        assert plan.source == TierSource.ACTIVE_SKILL

    def test_session_beats_default(self) -> None:
        plan = _resolve('hello', session_tier=Tier.STANDARD)
        assert plan.tier == Tier.STANDARD
        assert plan.source == TierSource.SESSION

    def test_default_applies_when_no_session_or_skill(self) -> None:
        plan = _resolve('hello')
        assert plan.tier == Tier.FAST
        assert plan.source == TierSource.DEFAULT

    def test_admin_default_override_is_respected(self) -> None:
        cfg = AdminTierConfig(default_tier=Tier.STANDARD)
        plan = _resolve('hello', admin_config=cfg)
        assert plan.tier == Tier.STANDARD
        assert plan.source == TierSource.DEFAULT


class TestEdgeCases:
    def test_empty_text_falls_through_to_default(self) -> None:
        plan = _resolve('')
        assert plan.tier == DEFAULT_ADMIN.default_tier
        assert plan.cleaned_text == ''
        assert plan.source == TierSource.DEFAULT

    def test_slash_alone_is_plain_text(self) -> None:
        plan = _resolve('/')
        assert plan.skill_override is None
        assert plan.cleaned_text == '/'
        assert plan.tier == DEFAULT_ADMIN.default_tier

    def test_slash_space_is_plain_text(self) -> None:
        plan = _resolve('/ hello')
        assert plan.skill_override is None
        assert plan.cleaned_text == '/ hello'

    def test_slash_digit_is_plain_text(self) -> None:
        plan = _resolve('/123 hello')
        assert plan.skill_override is None
        assert plan.cleaned_text == '/123 hello'


class TestReservedPrefixes:
    def test_reserved_set_matches_documented_tiers(self) -> None:
        assert RESERVED_PREFIXES == frozenset({'local', 'fast', 'standard', 'power'})
