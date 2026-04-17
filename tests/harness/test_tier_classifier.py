"""Tests for harness/tier_classifier.py — ISSUE-e0db47 session-tier classifier."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from marcel_core.config import settings
from marcel_core.harness import tier_classifier
from marcel_core.harness.model_chain import Tier
from marcel_core.harness.tier_classifier import (
    classify_initial_tier,
    detect_frustration,
    load_routing_config,
    maybe_bump_tier,
)


@pytest.fixture(autouse=True)
def _isolated_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every test at a fresh data root so routing.yaml is controlled."""
    monkeypatch.setattr(settings, 'marcel_data_dir', str(tmp_path))
    # Reset the module-level cache between tests.
    monkeypatch.setattr(tier_classifier, '_cached', None)
    return tmp_path


def _write_routing_yaml(root: Path, body: str) -> Path:
    path = root / 'routing.yaml'
    path.write_text(body, encoding='utf-8')
    return path


class TestLoadRoutingConfig:
    def test_defaults_when_file_missing(self, _isolated_data_root: Path) -> None:
        """Fresh install has no routing.yaml → we use the baked-in defaults."""
        cfg = load_routing_config()
        assert cfg.default_tier == Tier.STANDARD
        assert len(cfg.fast_patterns) > 0
        assert len(cfg.standard_patterns) > 0
        assert len(cfg.frustration_patterns) > 0

    def test_loads_user_file(self, _isolated_data_root: Path) -> None:
        _write_routing_yaml(
            _isolated_data_root,
            r"""
fast_triggers:
  en: ['\bhello\b']
standard_triggers:
  en: ['\bbuild\b']
frustration_triggers:
  en: ['\bugh\b']
default_tier: fast
""",
        )
        cfg = load_routing_config()
        assert cfg.default_tier == Tier.FAST
        assert any(p.search('hello there') for p in cfg.fast_patterns)
        assert any(p.search('build it') for p in cfg.standard_patterns)
        assert any(p.search('ugh') for p in cfg.frustration_patterns)

    def test_mtime_reload_picks_up_edits(self, _isolated_data_root: Path) -> None:
        path = _write_routing_yaml(
            _isolated_data_root,
            "fast_triggers: {en: ['\\bping\\b']}\ndefault_tier: standard\n",
        )
        cfg1 = load_routing_config()
        assert any(p.search('ping') for p in cfg1.fast_patterns)

        time.sleep(0.01)  # ensure mtime differs on coarse filesystems
        path.write_text(
            "fast_triggers: {en: ['\\bpong\\b']}\ndefault_tier: standard\n",
            encoding='utf-8',
        )
        # Force a different mtime in case the write landed in the same tick.
        import os

        os.utime(path, (time.time() + 1, time.time() + 1))

        cfg2 = load_routing_config()
        assert any(p.search('pong') for p in cfg2.fast_patterns)
        assert not any(p.search('ping') for p in cfg2.fast_patterns)

    def test_broken_yaml_falls_back_to_defaults(
        self, _isolated_data_root: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write_routing_yaml(_isolated_data_root, "fast_triggers: [unclosed: 'quote")
        with caplog.at_level('WARNING', logger='marcel_core.harness.tier_classifier'):
            cfg = load_routing_config()
        # Defaults still loaded, classifier keeps working.
        assert len(cfg.fast_patterns) > 0
        assert any('failed to load' in r.getMessage() for r in caplog.records)

    def test_invalid_pattern_is_skipped(self, _isolated_data_root: Path) -> None:
        _write_routing_yaml(
            _isolated_data_root,
            "fast_triggers:\n  en: ['(unclosed', '\\bok\\b']\nstandard_triggers: {en: []}\nfrustration_triggers: {en: []}\ndefault_tier: standard\n",
        )
        cfg = load_routing_config()
        # The invalid pattern is dropped; the valid one remains.
        assert len(cfg.fast_patterns) == 1
        assert any(p.search('ok') for p in cfg.fast_patterns)


class TestClassifyInitialTier:
    @pytest.fixture
    def cfg(self, _isolated_data_root: Path):
        return load_routing_config()

    def test_empty_message_uses_default(self, cfg) -> None:
        tier, reason = classify_initial_tier('', cfg)
        assert tier == cfg.default_tier
        assert reason.startswith('default')

    def test_english_fast_trigger(self, cfg) -> None:
        tier, reason = classify_initial_tier('what time is it?', cfg)
        assert tier == Tier.FAST
        assert reason.startswith('fast:')

    def test_dutch_fast_trigger(self, cfg) -> None:
        tier, reason = classify_initial_tier('hoe laat is het?', cfg)
        assert tier == Tier.FAST

    def test_english_standard_trigger(self, cfg) -> None:
        tier, _ = classify_initial_tier('debug this Python error please', cfg)
        assert tier == Tier.STANDARD

    def test_dutch_standard_trigger(self, cfg) -> None:
        tier, _ = classify_initial_tier('implementeer een functie voor mij', cfg)
        assert tier == Tier.STANDARD

    def test_standard_wins_over_fast(self, cfg) -> None:
        """A message that looks like a lookup *and* a complex ask gets STANDARD."""
        tier, _ = classify_initial_tier('what is wrong with this code, debug it', cfg)
        assert tier == Tier.STANDARD

    def test_fenced_code_is_standard(self, cfg) -> None:
        tier, _ = classify_initial_tier('have a look\n```\nprint(x)\n```', cfg)
        assert tier == Tier.STANDARD

    def test_no_match_uses_default(self, cfg) -> None:
        tier, reason = classify_initial_tier('just chatting about the weekend', cfg)
        assert tier == cfg.default_tier
        assert reason == 'default'


class TestFrustration:
    @pytest.fixture
    def cfg(self, _isolated_data_root: Path):
        return load_routing_config()

    def test_english_frustration(self, cfg) -> None:
        assert detect_frustration('wtf this is broken', cfg) is not None

    def test_dutch_frustration(self, cfg) -> None:
        assert detect_frustration('verdomme dat werkt niet', cfg) is not None

    def test_neutral_message_no_frustration(self, cfg) -> None:
        assert detect_frustration('please help me with this', cfg) is None

    def test_bump_fast_to_standard(self, cfg) -> None:
        new, reason = maybe_bump_tier(Tier.FAST, 'wtf this sucks', cfg)
        assert new == Tier.STANDARD
        assert reason is not None

    def test_standard_is_never_auto_bumped_to_power(self, cfg) -> None:
        """Frustration on a STANDARD session is a no-op — POWER is subagent-only."""
        new, reason = maybe_bump_tier(Tier.STANDARD, 'wtf this is terrible', cfg)
        assert new == Tier.STANDARD
        assert reason is None

    def test_no_bump_without_frustration(self, cfg) -> None:
        new, reason = maybe_bump_tier(Tier.FAST, 'ok can you help', cfg)
        assert new == Tier.FAST
        assert reason is None
