"""Turn router — resolve the tier and dispatch intent for a single user turn (ISSUE-6a38cd).

One pure function — :func:`resolve_turn` — answers *both* questions for the
message about to hit the runner:

1. What does this turn run as? A model call, a skill dispatch, or a
   rejection?
2. If a model call, which tier: LOCAL, FAST, STANDARD, or POWER?

The function has no I/O. Its inputs (``active_skill_tier``, ``session_tier``,
``admin_config``, ``known_skills``) are values the channel adapter or turn
runner resolves first and passes in. That keeps the routing logic trivially
unit-testable — every precedence rule is one assertion.

Slash prefixes
--------------

* ``/local``, ``/fast``, ``/standard`` — one-shot tier override for this
  turn. Does **not** persist to the session tier. Prefix is stripped from
  the cleaned text.
* ``/power`` — rejected. POWER is reserved for skills / subagents that
  declare ``preferred_tier: power`` or ``model: power``; users cannot
  force it.
* ``/<skillname>`` — dispatch to the named skill (parity with Claude
  Code's own ``/new-issue`` / ``/finish-issue`` pattern). The remaining
  text becomes the skill input. Unknown ``/`` prefixes fall through to
  normal routing with the original text intact.

Tier precedence (highest wins)
------------------------------

1. ``/local`` / ``/fast`` / ``/standard`` user prefix.
2. ``active_skill_tier`` — the highest ``preferred_tier`` among skills in
   this turn's context. Only path to POWER.
3. ``session_tier`` — per-channel persisted tier (written by the
   classifier on the first turn, bumped on frustration).
4. ``admin_config.default_tier`` — fresh-session fallback when no session
   tier has been stored yet.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Final

from marcel_core.harness.model_chain import Tier, tier_from_index

# Slash-prefix → Tier. User-selectable tiers only.
_TIER_PREFIX: Final[dict[str, Tier]] = {
    'local': Tier.LOCAL,
    'fast': Tier.FAST,
    'standard': Tier.STANDARD,
}

# Reserved words a skill must not adopt — they are all either tier prefixes
# or the rejected ``power`` prefix.
RESERVED_PREFIXES: Final[frozenset[str]] = frozenset({'local', 'fast', 'standard', 'power'})

POWER_REJECT_MESSAGE: Final[str] = (
    'The `power` tier is reserved for subagents and skills that declare it. '
    'Use `/local`, `/fast`, or `/standard`, or leave it off and let Marcel choose.'
)


class TierSource(str, Enum):
    """Which rule produced the resolved tier. Emitted in log messages."""

    USER_PREFIX = 'user_prefix'
    ACTIVE_SKILL = 'active_skill'
    SESSION = 'session'
    DEFAULT = 'default'


@dataclass(frozen=True)
class AdminTierConfig:
    """Admin-configurable tier defaults.

    * ``default_tier`` biases the classifier / fresh-session fallback.
    * ``fallback_tier`` is the tier used as the cloud-outage explainer at
      the tail of higher-tier chains (consumed elsewhere, declared here so
      both admin knobs live in one place).

    POWER is never admin-selectable — both knobs must be LOCAL, FAST, or
    STANDARD. The class rejects POWER at construction to catch typos in
    tests and skill setup; the env-var path is additionally fenced at the
    pydantic-settings layer (see ``Settings.marcel_*_tier``).
    """

    fallback_tier: Tier = Tier.LOCAL
    default_tier: Tier = Tier.FAST

    def __post_init__(self) -> None:
        if self.fallback_tier == Tier.POWER:
            raise ValueError('fallback_tier cannot be POWER — power is not admin-selectable')
        if self.default_tier == Tier.POWER:
            raise ValueError('default_tier cannot be POWER — power is not admin-selectable')

    @classmethod
    def from_settings(cls) -> AdminTierConfig:
        """Build from the global settings object.

        The raw ints are range-checked by pydantic-settings (``ge=0, le=2``);
        ``tier_from_index`` provides the symbolic conversion.
        """
        from marcel_core.config import settings

        return cls(
            fallback_tier=tier_from_index(settings.marcel_fallback_tier),
            default_tier=tier_from_index(settings.marcel_default_tier),
        )


@dataclass(frozen=True)
class TurnPlan:
    """Resolved plan for a single user turn.

    Exactly one of these paths applies:

    * ``reject_reason`` set → caller sends that text back to the user and
      does **not** invoke the model. Used for ``/power``.
    * ``skill_override`` set → caller dispatches to that skill with
      ``cleaned_text`` as input. No model call for this turn.
    * otherwise → caller runs the model at ``tier`` with ``cleaned_text``.

    ``source`` is always populated so logs show *why* the tier was chosen.
    ``tier`` is always a valid tier — even on a reject path it reflects the
    tier that *would* have run, which is useful when the reject message
    itself needs rendering (future: localization based on tier).
    """

    tier: Tier
    cleaned_text: str
    source: TierSource
    skill_override: str | None = None
    reject_reason: str | None = None


def _parse_slash_command(text: str) -> tuple[str | None, str]:
    """Return ``(command_lower, remainder)`` for a ``/cmd ...`` message.

    ``command`` is lowercased for case-insensitive dispatch. ``remainder`` is
    the text after the first whitespace run that followed the command, with
    no leading whitespace. If ``text`` doesn't start with a valid ``/cmd``,
    returns ``(None, text)`` — the caller falls through unchanged.

    A "valid" command name is ``[A-Za-z_][A-Za-z0-9_-]*``. This rejects
    messages like ``/`` alone, ``/ foo`` (space after slash), or ``/123``
    (leading digit) — those are treated as plain text.
    """
    if not text.startswith('/') or len(text) < 2:
        return None, text

    rest = text[1:]
    # Extract the command up to the first whitespace char (or end of string).
    name_end = 0
    for ch in rest:
        if ch.isspace():
            break
        name_end += 1
    name = rest[:name_end]

    if not name or not (name[0].isalpha() or name[0] == '_'):
        return None, text
    if not all(ch.isalnum() or ch in '-_' for ch in name):
        return None, text

    remainder = rest[name_end:].lstrip()
    return name.lower(), remainder


def resolve_turn(
    user_text: str,
    *,
    active_skill_tier: Tier | None,
    session_tier: Tier | None,
    admin_config: AdminTierConfig,
    known_skills: Iterable[str] = (),
) -> TurnPlan:
    """Resolve the tier and dispatch intent for ``user_text``.

    Pure function — see module docstring for precedence rules and prefix
    semantics.

    Args:
        user_text: The raw message from the user. May start with a slash
            prefix (``/local``, ``/fast``, ``/standard``, ``/power``, or
            ``/<skillname>``), or be plain text.
        active_skill_tier: The highest ``preferred_tier`` among skills
            whose docs are in this turn's system prompt, or ``None``. Only
            path to POWER.
        session_tier: The per-channel stored tier, or ``None`` for a fresh
            session.
        admin_config: Admin-configurable tier defaults.
        known_skills: Skill names available to this user. Used to validate
            ``/<skillname>`` prefixes. Unknown names fall through to normal
            routing without error.

    Returns:
        A :class:`TurnPlan` describing how to run this turn.
    """
    known_skill_set = frozenset(name.lower() for name in known_skills)

    command, remainder = _parse_slash_command(user_text)

    if command == 'power':
        return TurnPlan(
            tier=admin_config.default_tier,
            cleaned_text=remainder,
            source=TierSource.DEFAULT,
            reject_reason=POWER_REJECT_MESSAGE,
        )

    if command in _TIER_PREFIX:
        return TurnPlan(
            tier=_TIER_PREFIX[command],
            cleaned_text=remainder,
            source=TierSource.USER_PREFIX,
        )

    if command is not None and command in known_skill_set:
        tier = _tier_from_context(active_skill_tier, session_tier, admin_config)
        source = _source_from_context(active_skill_tier, session_tier)
        return TurnPlan(
            tier=tier,
            cleaned_text=remainder,
            source=source,
            skill_override=command,
        )

    # Unknown ``/cmd`` or plain text — fall through with the original text.
    tier = _tier_from_context(active_skill_tier, session_tier, admin_config)
    source = _source_from_context(active_skill_tier, session_tier)
    return TurnPlan(tier=tier, cleaned_text=user_text, source=source)


def _tier_from_context(
    active_skill_tier: Tier | None,
    session_tier: Tier | None,
    admin_config: AdminTierConfig,
) -> Tier:
    """Apply tier precedence 2 → 3 → 4 (user prefix handled separately)."""
    if active_skill_tier is not None:
        return active_skill_tier
    if session_tier is not None:
        return session_tier
    return admin_config.default_tier


def _source_from_context(
    active_skill_tier: Tier | None,
    session_tier: Tier | None,
) -> TierSource:
    if active_skill_tier is not None:
        return TierSource.ACTIVE_SKILL
    if session_tier is not None:
        return TierSource.SESSION
    return TierSource.DEFAULT


__all__ = [
    'AdminTierConfig',
    'POWER_REJECT_MESSAGE',
    'RESERVED_PREFIXES',
    'TierSource',
    'TurnPlan',
    'resolve_turn',
]
