"""Session-tier classifier for Marcel (ISSUE-e0db47).

Classifies the first user message of a session as either ``FAST`` or
``STANDARD`` using a user-editable YAML keyword list. Detects frustration
signals and bumps the session up one tier when they appear. ``POWER`` is
never auto-selected here — it's reached only via an explicit skill
``preferred_tier: power`` or subagent ``model: power``.

Config file lives at ``<data_root>/routing.yaml``; seeded from
``defaults/routing.yaml`` on first startup. Reloaded automatically when its
mtime changes, so a user edit takes effect on the next turn without a
restart. If the file is missing or malformed, the classifier falls back to
the baked-in defaults and logs a warning — a broken edit must never brick
the router.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import yaml

from marcel_core.config import settings
from marcel_core.harness.model_chain import Tier

log = logging.getLogger(__name__)

_DEFAULTS_PATH = Path(__file__).resolve().parents[1] / 'defaults' / 'routing.yaml'


@dataclass(frozen=True)
class RoutingConfig:
    """Compiled routing configuration."""

    fast_patterns: tuple[re.Pattern[str], ...]
    standard_patterns: tuple[re.Pattern[str], ...]
    frustration_patterns: tuple[re.Pattern[str], ...]
    default_tier: Tier


def _compile(raw: list[str]) -> tuple[re.Pattern[str], ...]:
    compiled: list[re.Pattern[str]] = []
    for pattern in raw:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error as exc:
            log.warning('tier_classifier: ignoring invalid pattern %r (%s)', pattern, exc)
    return tuple(compiled)


def _flatten(block: object) -> list[str]:
    """Accept either ``{en: [...], nl: [...]}`` or a flat list."""
    if isinstance(block, dict):
        out: list[str] = []
        for value in block.values():
            if isinstance(value, list):
                out.extend(str(x) for x in value)
        return out
    if isinstance(block, list):
        return [str(x) for x in block]
    return []


def _parse(doc: object) -> RoutingConfig:
    if not isinstance(doc, dict):
        raise ValueError('routing.yaml: top-level must be a mapping')

    default_raw = str(doc.get('default_tier', 'standard')).lower()
    try:
        default_tier = Tier(default_raw)
    except ValueError:
        log.warning("tier_classifier: unknown default_tier %r — using 'standard'", default_raw)
        default_tier = Tier.STANDARD
    if default_tier not in (Tier.FAST, Tier.STANDARD):
        log.warning('tier_classifier: default_tier must be fast or standard, got %r — using standard', default_raw)
        default_tier = Tier.STANDARD

    return RoutingConfig(
        fast_patterns=_compile(_flatten(doc.get('fast_triggers'))),
        standard_patterns=_compile(_flatten(doc.get('standard_triggers'))),
        frustration_patterns=_compile(_flatten(doc.get('frustration_triggers'))),
        default_tier=default_tier,
    )


def _load_from(path: Path) -> RoutingConfig:
    text = path.read_text(encoding='utf-8')
    return _parse(yaml.safe_load(text))


_cache_lock = threading.Lock()
_cached: tuple[Path, float, RoutingConfig] | None = None
_defaults_cache: RoutingConfig | None = None


def _defaults() -> RoutingConfig:
    global _defaults_cache
    if _defaults_cache is None:
        _defaults_cache = _load_from(_DEFAULTS_PATH)
    return _defaults_cache


def load_routing_config() -> RoutingConfig:
    """Return the active routing config, reloading on mtime change.

    Falls back to the baked-in defaults if the user's ``routing.yaml`` is
    missing or fails to parse.
    """
    path = settings.data_dir / 'routing.yaml'

    with _cache_lock:
        global _cached
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            return _defaults()

        if _cached and _cached[0] == path and _cached[1] == mtime:
            return _cached[2]

        try:
            cfg = _load_from(path)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            log.warning('tier_classifier: failed to load %s (%s) — using defaults', path, exc)
            return _defaults()

        _cached = (path, mtime, cfg)
        return cfg


def classify_initial_tier(message: str, cfg: RoutingConfig) -> tuple[Tier, str]:
    """Classify a session's first message as FAST or STANDARD.

    Returns ``(tier, reason)``. Reason is a short string identifying the
    trigger that matched (or ``'default'``), suitable for structured logs.
    STANDARD wins over FAST when both fire — complexity signal trumps the
    simple-lookup signal.
    """
    if not message.strip():
        return cfg.default_tier, 'default:empty'

    for pattern in cfg.standard_patterns:
        if pattern.search(message):
            return Tier.STANDARD, f'standard:{pattern.pattern}'

    for pattern in cfg.fast_patterns:
        if pattern.search(message):
            return Tier.FAST, f'fast:{pattern.pattern}'

    return cfg.default_tier, 'default'


def detect_frustration(message: str, cfg: RoutingConfig) -> str | None:
    """Return the matched frustration pattern, or ``None``."""
    for pattern in cfg.frustration_patterns:
        if pattern.search(message):
            return pattern.pattern
    return None


def maybe_bump_tier(current: Tier, message: str, cfg: RoutingConfig) -> tuple[Tier, str | None]:
    """Bump FAST → STANDARD when frustration is detected.

    Returns ``(new_tier, reason_or_None)``. STANDARD stays at STANDARD — the
    classifier never auto-promotes to POWER. Reason is the matched pattern
    when a bump happened, ``None`` otherwise.
    """
    if current != Tier.FAST:
        return current, None
    matched = detect_frustration(message, cfg)
    if matched is None:
        return current, None
    return Tier.STANDARD, matched


__all__ = [
    'RoutingConfig',
    'classify_initial_tier',
    'detect_frustration',
    'load_routing_config',
    'maybe_bump_tier',
]
