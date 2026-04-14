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
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from marcel_core.config import settings
from marcel_core.jobs.executor import FALLBACK_ELIGIBLE_CATEGORIES, classify_error

log = logging.getLogger(__name__)

# The prefix used by pydantic-ai agent construction to route to a self-hosted
# OpenAI-compatible server. Re-exported here so the chain helper doesn't need
# to import from ``harness.agent`` (which would create a circular import when
# ``harness.agent`` ever wants to consult the chain).
_LOCAL_PREFIX = 'local:'


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
    'FALLBACK_ELIGIBLE_CATEGORIES',
    'build_chain',
    'is_fallback_eligible',
    'next_tier',
    'build_explain_system_prompt',
    'build_explain_user_prompt',
]
