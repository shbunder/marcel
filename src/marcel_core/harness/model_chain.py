"""Four-tier model fallback chain (ISSUE-076).

This module centralises Marcel's "which model do I use when the primary
fails" policy so that both the interactive turn runner and the scheduled
job executor share the same tier resolution + error-eligibility rules.

Tiers
-----

1. ``STANDARD`` — normal calls. ``settings.marcel_standard_model``, or an
   explicit ``primary`` override (per-channel pin, explicit caller arg).
2. ``BACKUP`` — different-cloud-provider backup tried when tier 1 raises a
   transient or auth/quota error. ``settings.marcel_backup_model``; skipped
   when unset.
3. ``FALLBACK`` — last-ditch model, typically a small local LLM.
   ``settings.marcel_fallback_model``; skipped when unset, or when the
   value is a ``local:`` string but ``marcel_local_llm_url`` /
   ``marcel_local_llm_model`` aren't configured. Has two run modes:

   * ``'explain'`` (interactive turns) — runs with an empty message history,
     an empty tool filter, and a synthesized system prompt whose sole job is
     to tell the user that cloud models are temporarily unavailable. Does
     *not* attempt to answer the original question — small local models will
     hallucinate, and the primary goal is a reliable error message.
   * ``'complete'`` (scheduled jobs) — runs the same task against the local
     model like the legacy ISSUE-070 path. Preserves existing semantics.

4. ``POWER`` — not part of the failure chain. Returned here only so a single
   ``tier:<name>`` sentinel vocabulary covers every tier. Used by the
   ``power`` default subagent via the delegate tool.

Usage
-----

.. code-block:: python

    from marcel_core.harness.model_chain import build_chain, is_fallback_eligible, next_tier

    chain = build_chain(primary=channel_pin, mode='explain')
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
    """The four named tiers in Marcel's model fallback chain."""

    STANDARD = 'standard'
    BACKUP = 'backup'
    FALLBACK = 'fallback'
    POWER = 'power'


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


def build_chain(
    *,
    primary: str | None = None,
    mode: Literal['explain', 'complete'] = 'explain',
) -> list[TierEntry]:
    """Resolve the ordered tier list for a given call.

    Args:
        primary: Optional override for tier 1. When ``None`` the chain uses
            ``settings.marcel_standard_model``. A per-channel pin or explicit
            caller argument should pass in their resolved value here — this
            replaces tier 1 *only*, tiers 2 and 3 still come from env vars.
        mode: ``'explain'`` for interactive turns (tier 3 gets the
            explain-failure purpose), ``'complete'`` for scheduled jobs (tier
            3 tries to finish the task on the local model, matching the
            legacy ISSUE-070 fallback semantics).

    Returns:
        A list with at least one entry (tier 1). Tier 2 is appended iff
        ``settings.marcel_backup_model`` is set. Tier 3 is appended iff
        ``settings.marcel_fallback_model`` is set *and*, when it's a
        ``local:`` model, the local LLM transport is configured.
    """
    chain: list[TierEntry] = [
        TierEntry(
            tier=Tier.STANDARD,
            model=primary or settings.marcel_standard_model,
            purpose='primary',
        )
    ]

    if settings.marcel_backup_model:
        chain.append(
            TierEntry(
                tier=Tier.BACKUP,
                model=settings.marcel_backup_model,
                purpose='backup',
            )
        )

    fallback_model = settings.marcel_fallback_model
    if fallback_model and _fallback_tier_usable(fallback_model):
        chain.append(
            TierEntry(
                tier=Tier.FALLBACK,
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
        super().__init__(
            f'tier {tier!r} is not configured — set MARCEL_{tier.upper()}_MODEL'
        )


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
        Tier.STANDARD: settings.marcel_standard_model,
        Tier.BACKUP: settings.marcel_backup_model,
        Tier.FALLBACK: settings.marcel_fallback_model,
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
