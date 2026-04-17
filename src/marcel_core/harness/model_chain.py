"""Per-tier model fallback chain (ISSUE-e0db47, extends ISSUE-076).

Centralises Marcel's "which model do I use when the primary fails" policy
so that both the interactive turn runner and the scheduled job executor
share the same tier resolution + error-eligibility rules.

Tiers
-----

Four tiers, publicly indexed 0–3 so admins and users can reason about them
numerically. Higher index = more capable (and typically more expensive).

* ``LOCAL`` (0) — Self-hosted local LLM, from ``settings.marcel_fallback_model``
  (historical env-var name). No cross-cloud backup. Used as the
  cloud-outage fallback for higher tiers, and selectable directly by the
  user via ``/local`` or by an admin default.
* ``FAST`` (1) — Haiku-class. Primary from ``settings.marcel_fast_model``,
  optional cross-cloud backup from ``settings.marcel_fast_backup_model``.
* ``STANDARD`` (2) — Sonnet-class daily driver. Primary from
  ``settings.marcel_standard_model``, optional backup from
  ``settings.marcel_standard_backup_model``.
* ``POWER`` (3) — Opus-class. Primary from ``settings.marcel_power_model``,
  optional backup from ``settings.marcel_power_backup_model``. Never
  auto-selected by the session classifier and never user-selectable; reached
  only via an explicit skill ``preferred_tier: power`` or subagent
  ``model: power``.

When a higher-tier chain exhausts every cloud option, the shared last-resort
``LOCAL`` entry appended to the chain ``explain``s the failure to the user.
That entry is skipped when ``marcel_fallback_model`` is unset, or when the
value is a ``local:`` string but ``marcel_local_llm_url`` /
``marcel_local_llm_model`` aren't configured. Two run modes:

* ``'explain'`` (interactive turns) — runs with an empty message history,
  an empty tool filter, and a synthesized system prompt whose sole job is
  to tell the user that cloud models are temporarily unavailable. Does
  *not* attempt to answer the original question.
* ``'complete'`` (scheduled jobs) — runs the same task against the local
  model like the legacy ISSUE-070 path. Preserves existing semantics.

Usage
-----

.. code-block:: python

    from marcel_core.harness.model_chain import Tier, build_chain, is_fallback_eligible, next_tier

    chain = build_chain(tier=Tier.STANDARD, primary=channel_pin, mode='explain')
    current = chain[0]
    while current is not None:
        try:
            run_once(current.model, ...)
            break
        except Exception as exc:
            eligible, category = is_fallback_eligible(str(exc))
            if not eligible:
                raise
            current = next_tier(chain, current, category)

The turn runner and job executor both wrap this basic loop with their own
bookkeeping (streaming, segment history, backoff retries, etc).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from marcel_core.config import settings

log = logging.getLogger(__name__)

# The prefix used by pydantic-ai agent construction to route to a self-hosted
# OpenAI-compatible server. Re-exported here so the chain helper doesn't need
# to import from ``harness.agent`` (which would create a circular import when
# ``harness.agent`` ever wants to consult the chain).
_LOCAL_PREFIX = 'local:'


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------
#
# Lives here (not in jobs/executor.py) because fallback-chain advancement is
# driven by these categories, and the chain is consumed by both the runner
# and the executor. Keeping the rules co-located with ``build_chain`` /
# ``next_tier`` avoids the backwards ``harness → jobs`` import this module
# used to carry (audit finding, ISSUE-077).

_TRANSIENT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'rate.?limit|429|too many requests', re.I), 'rate_limit'),
    (re.compile(r'timeout|timed?\s*out|etimedout', re.I), 'timeout'),
    (re.compile(r'connect|network|dns|socket|econnr', re.I), 'network'),
    (re.compile(r'50[0-4]|server error|internal error|bad gateway|overloaded', re.I), 'server_error'),
]

# Auth / quota / billing errors — permanent for the cloud provider (retrying
# won't help), but a valid trigger for the local-LLM fallback path: the cloud
# credential is broken or out of budget, not the request itself.
_AUTH_QUOTA_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r'401|403|unauthori[sz]ed|forbidden', re.I),
    re.compile(r'invalid api key|api key not found|authentication error', re.I),
    re.compile(r'insufficient[_ ]quota|quota exceeded|credit balance too low|billing', re.I),
]

# Error categories the fallback chain will advance on. Transient cloud errors
# retry on the same provider first; if those retries exhaust, the chain still
# fires for this set. Auth/quota errors fire the chain immediately (no point
# retrying the same broken credential).
FALLBACK_ELIGIBLE_CATEGORIES: frozenset[str] = frozenset(
    {'rate_limit', 'timeout', 'network', 'server_error', 'auth_or_quota'}
)


def classify_error(error: str) -> tuple[bool, str]:
    """Classify an error as transient or permanent.

    Returns ``(is_transient, category)``. Category is one of the transient
    pattern names, ``"auth_or_quota"`` for cloud auth/billing failures (not
    transient but fallback-eligible), or ``"permanent"``.
    """
    for pattern, category in _TRANSIENT_PATTERNS:
        if pattern.search(error):
            return True, category
    for pattern in _AUTH_QUOTA_PATTERNS:
        if pattern.search(error):
            return False, 'auth_or_quota'
    return False, 'permanent'


class Tier(str, Enum):
    """Named tiers in Marcel's model ladder.

    Four tiers, publicly indexed 0–3 via :data:`TIER_INDEX`. All four are
    valid ``tier:<name>`` sentinels for skill/subagent frontmatter. User
    prefix routing (``/local``, ``/fast``, ``/standard``) exposes every tier
    *except* POWER — POWER is reserved for skills/subagents that declare it
    explicitly.
    """

    LOCAL = 'local'
    FAST = 'fast'
    STANDARD = 'standard'
    POWER = 'power'


TIER_INDEX: dict[Tier, int] = {
    Tier.LOCAL: 0,
    Tier.FAST: 1,
    Tier.STANDARD: 2,
    Tier.POWER: 3,
}

TIER_BY_INDEX: dict[int, Tier] = {index: tier for tier, index in TIER_INDEX.items()}


def tier_from_index(index: int) -> Tier:
    """Resolve a public tier index (0–3) to its :class:`Tier` member.

    Raises ``ValueError`` for out-of-range indexes so admin config validation
    gets a clear error message rather than a silent KeyError.
    """
    try:
        return TIER_BY_INDEX[index]
    except KeyError:
        raise ValueError(
            f'unknown tier index {index!r} — must be one of 0 (local), 1 (fast), 2 (standard), 3 (power)'
        ) from None


Purpose = Literal['primary', 'backup', 'explain', 'complete']


@dataclass(frozen=True)
class TierEntry:
    """A single entry in a resolved fallback chain.

    ``purpose`` is the *role* this entry plays in the chain, not the tier
    itself:

    * ``'primary'`` — tier 1, the caller's normal model.
    * ``'backup'`` — tier 2, a cross-provider cloud backup.
    * ``'explain'`` — tier 3 in turn mode: local LLM running the synthesised
      "explain the failure" prompt, with no tools and empty history.
    * ``'complete'`` — tier 3 in job mode: local LLM running the original
      task like the legacy ISSUE-070 path.
    """

    tier: Tier
    model: str
    purpose: Purpose


def _fallback_tier_usable(model: str) -> bool:
    """Return True if a ``local:`` fallback model has its transport configured.

    Non-``local:`` fallback models (e.g. a second cloud provider used as the
    failure-explanation model) are always usable as long as the env var is
    set — we can't ping them without actually calling them, so we defer
    validation to run time.
    """
    if not model.startswith(_LOCAL_PREFIX):
        return True
    if not settings.marcel_local_llm_url:
        log.warning(
            'model_chain: MARCEL_FALLBACK_MODEL=%s requires MARCEL_LOCAL_LLM_URL but it is not set — '
            'tier 3 will be skipped',
            model,
        )
        return False
    if not settings.marcel_local_llm_model:
        log.warning(
            'model_chain: MARCEL_FALLBACK_MODEL=%s requires MARCEL_LOCAL_LLM_MODEL but it is not set — '
            'tier 3 will be skipped',
            model,
        )
        return False
    return True


_TIER_PRIMARY_ATTR = {
    Tier.LOCAL: 'marcel_fallback_model',
    Tier.FAST: 'marcel_fast_model',
    Tier.STANDARD: 'marcel_standard_model',
    Tier.POWER: 'marcel_power_model',
}

# LOCAL has no cross-cloud backup — it *is* the last-resort tier. Membership
# lookup is gated by ``.get`` so callers can safely ask any tier.
_TIER_BACKUP_ATTR = {
    Tier.FAST: 'marcel_fast_backup_model',
    Tier.STANDARD: 'marcel_standard_backup_model',
    Tier.POWER: 'marcel_power_backup_model',
}


def build_chain(
    *,
    tier: Tier = Tier.STANDARD,
    primary: str | None = None,
    mode: Literal['explain', 'complete'] = 'explain',
    fallback_tier: Tier = Tier.LOCAL,
) -> list[TierEntry]:
    """Resolve the ordered tier list for a given call.

    Args:
        tier: The user-facing tier driving this call. Selects the primary
            model env var and (for non-``LOCAL`` tiers) the per-tier backup
            env var. Any tier is accepted; ``LOCAL`` produces a single-entry
            chain with no backup and no further fallback.
        primary: Optional override for the primary slot. When ``None`` the
            chain uses ``settings.marcel_<tier>_model``. A per-channel pin
            or explicit caller argument passes in its resolved value here —
            this replaces the primary *only*, the backup still comes from
            ``settings.marcel_<tier>_backup_model``.
        mode: ``'explain'`` for interactive turns (local fallback gets the
            explain-failure purpose), ``'complete'`` for scheduled jobs
            (local fallback tries to finish the task on the local model,
            matching legacy ISSUE-070 semantics).
        fallback_tier: Tier used for the cloud-outage tail entry. Default
            ``Tier.LOCAL`` (the historical behavior, reading
            ``marcel_fallback_model``). Admins can override via
            ``AdminTierConfig.fallback_tier`` to tail the chain with a
            cloud tier instead (``FAST`` → ``marcel_fast_model``). The tail
            entry is omitted when ``fallback_tier`` equals ``tier`` (no
            point appending the same model twice).

    Returns:
        A list with at least one entry (the primary). For non-``LOCAL``
        tiers, the per-tier backup is appended iff
        ``settings.marcel_<tier>_backup_model`` is set, and a
        ``fallback_tier`` tail entry is appended iff the admin-selected
        fallback resolves to a usable model (for ``LOCAL``, that means the
        local LLM transport is configured; for cloud tiers, the model env
        var is set). For the ``LOCAL`` tier, the chain contains only the
        primary (no recursive append — LOCAL IS the fallback).
    """
    if tier not in _TIER_PRIMARY_ATTR:
        raise ValueError(f'build_chain: unknown tier {tier!r}')
    if fallback_tier == Tier.POWER:
        raise ValueError('build_chain: fallback_tier cannot be POWER')

    primary_model = primary or getattr(settings, _TIER_PRIMARY_ATTR[tier])
    chain: list[TierEntry] = [TierEntry(tier=tier, model=primary_model, purpose='primary')]

    if tier == Tier.LOCAL:
        return chain

    backup_attr = _TIER_BACKUP_ATTR.get(tier)
    backup_model = getattr(settings, backup_attr) if backup_attr else None
    if backup_model:
        chain.append(TierEntry(tier=tier, model=backup_model, purpose='backup'))

    if fallback_tier != tier:
        fallback_model = getattr(settings, _TIER_PRIMARY_ATTR[fallback_tier])
        if fallback_model and (fallback_tier != Tier.LOCAL or _fallback_tier_usable(fallback_model)):
            chain.append(
                TierEntry(
                    tier=fallback_tier,
                    model=fallback_model,
                    purpose='explain' if mode == 'explain' else 'complete',
                )
            )

    return chain


def is_fallback_eligible(error: str) -> tuple[bool, str]:
    """Classify ``error`` and report whether the chain should advance.

    Returns a ``(eligible, category)`` tuple. ``category`` is one of
    ``'rate_limit'``, ``'timeout'``, ``'network'``, ``'server_error'``,
    ``'auth_or_quota'``, or ``'permanent'``. The chain only advances for
    categories that are in ``FALLBACK_ELIGIBLE_CATEGORIES``. Permanent
    errors (validation failures, unknown skills, etc.) short-circuit the
    chain immediately — there's no point trying a different model when the
    input itself is malformed.
    """
    is_transient, category = classify_error(error)
    eligible = is_transient or category in FALLBACK_ELIGIBLE_CATEGORIES
    return eligible, category


def next_tier(
    chain: list[TierEntry],
    failed: TierEntry,
    category: str,
) -> TierEntry | None:
    """Return the next eligible entry in ``chain`` after ``failed``, or ``None``.

    The ``explain`` tier has a stricter eligibility rule than plain backup:
    we don't fire a canned "cloud failed" apology on a permanent validation
    error — the real error message is more useful to the user. Backup-purpose
    tiers advance on any eligible category; explain-purpose tiers advance
    only on categories that are genuinely in ``FALLBACK_ELIGIBLE_CATEGORIES``
    (which excludes ``'permanent'``).
    """
    try:
        idx = chain.index(failed)
    except ValueError:
        return None

    for entry in chain[idx + 1 :]:
        if entry.purpose == 'explain' and category not in FALLBACK_ELIGIBLE_CATEGORIES:
            return None
        return entry

    return None


# ---------------------------------------------------------------------------
# Tier sentinel resolution
# ---------------------------------------------------------------------------
#
# Agents can reference a tier by name in their frontmatter (``model: power``)
# so that env-var changes to ``MARCEL_POWER_MODEL`` take effect without a
# restart. The agents loader canonicalises bare names to ``tier:<name>`` at
# load time; the delegate tool resolves the sentinel to a concrete model
# string at call time. Both sides share the helpers below.

TIER_SENTINEL_PREFIX = 'tier:'


class TierNotConfigured(RuntimeError):
    """A ``tier:<name>`` sentinel was used but its env var is unset.

    Carries the tier name so the caller can emit a targeted error that
    names the missing ``MARCEL_<NAME>_MODEL`` env var.
    """

    def __init__(self, tier: str) -> None:
        self.tier = tier
        super().__init__(f'tier {tier!r} is not configured — set MARCEL_{tier.upper()}_MODEL')


def is_tier_sentinel(value: str) -> bool:
    """Return True if ``value`` is a ``tier:<name>`` sentinel string."""
    return value.startswith(TIER_SENTINEL_PREFIX)


def make_tier_sentinel(name: str) -> str | None:
    """Canonicalise a bare tier name like ``"standard"`` to ``"tier:standard"``.

    Returns ``None`` if ``name`` is not a known :class:`Tier` member — the
    caller should then treat the input as a literal model string instead.
    """
    try:
        Tier(name)
    except ValueError:
        return None
    return f'{TIER_SENTINEL_PREFIX}{name}'


def resolve_tier_sentinel(sentinel: str) -> str:
    """Resolve ``"tier:<name>"`` to the configured model string from settings.

    Args:
        sentinel: A ``tier:<name>`` string where ``<name>`` matches a
            :class:`Tier` enum value.

    Returns:
        The configured model string for that tier (e.g.
        ``'anthropic:claude-opus-4-6'``).

    Raises:
        ValueError: ``sentinel`` is not a tier sentinel, or ``<name>`` is
            not a known tier.
        TierNotConfigured: tier is valid but its env var is unset.
    """
    if not is_tier_sentinel(sentinel):
        raise ValueError(f'not a tier sentinel: {sentinel!r}')
    tier_name = sentinel[len(TIER_SENTINEL_PREFIX) :]
    try:
        tier = Tier(tier_name)
    except ValueError:
        raise ValueError(f'unknown tier {tier_name!r}') from None
    tier_map = {
        Tier.LOCAL: settings.marcel_fallback_model,
        Tier.FAST: settings.marcel_fast_model,
        Tier.STANDARD: settings.marcel_standard_model,
        Tier.POWER: settings.marcel_power_model,
    }
    resolved = tier_map[tier]
    if not resolved:
        raise TierNotConfigured(tier_name)
    return resolved


# ---------------------------------------------------------------------------
# Explain-mode prompt synthesis
# ---------------------------------------------------------------------------

EXPLAIN_SYSTEM_PROMPT = """\
You are Marcel's offline fallback voice. The primary cloud models just \
failed, and you are running on a small local model so the user doesn't \
get a silent error.

Your ONLY job is to tell the user — in one short, friendly paragraph — \
that Marcel's main models are temporarily unavailable, and suggest they \
try again in a minute or two. If the error hints at a specific cause \
(rate limit, provider outage, auth problem), mention it plainly.

Do NOT attempt to answer the user's original question. You do not have \
the tools, memory, or context to do so reliably, and pretending to try \
would waste the user's time. Keep it under 60 words. No apologies longer \
than "sorry about that". No speculation about when the outage will end.

The error that triggered this fallback:
[{category}] {error_summary}
"""

_ERROR_SUMMARY_MAX = 240


def build_explain_system_prompt(error: str, category: str) -> str:
    """Render the system prompt handed to the explain-tier run.

    The error is trimmed to the first non-empty line, capped at 240 chars,
    so a 2KB stack trace doesn't eat the whole context window of a small
    local model.
    """
    first_line = next((line for line in error.strip().splitlines() if line.strip()), '')
    summary = first_line[:_ERROR_SUMMARY_MAX]
    return EXPLAIN_SYSTEM_PROMPT.format(category=category, error_summary=summary)


def build_explain_user_prompt(user_text: str) -> str:
    """Render the user turn handed to the explain-tier run.

    The local model sees a short wrapper around the original message so it
    knows what was attempted, but not the full conversation history — the
    only goal is a reliable apology, not a conversation resume.
    """
    clipped = user_text.strip()
    if len(clipped) > 500:
        clipped = clipped[:500] + '…'
    return f'The user said: {clipped!r}\n\nTell them what happened.'


__all__ = [
    'Tier',
    'TIER_INDEX',
    'TIER_BY_INDEX',
    'tier_from_index',
    'TierEntry',
    'Purpose',
    'TIER_SENTINEL_PREFIX',
    'TierNotConfigured',
    'FALLBACK_ELIGIBLE_CATEGORIES',
    'classify_error',
    'is_tier_sentinel',
    'make_tier_sentinel',
    'resolve_tier_sentinel',
    'build_chain',
    'is_fallback_eligible',
    'next_tier',
    'build_explain_system_prompt',
    'build_explain_user_prompt',
]
