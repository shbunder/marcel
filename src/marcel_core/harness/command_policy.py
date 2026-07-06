"""Declarative command-execution policy — allow / ask / deny.

Classifies an agent action (a tool call) into one of three verdicts,
implementing the policy layer of ADR-260628-ca8f39. It runs as a
``tool_call`` event-bus handler (see :mod:`marcel_core.harness.core_handlers`),
so it composes with role-gating and the self-modification path guard rather
than being a parallel gate:

- **allow** — the action runs.
- **ask** — the action pauses for human approval (Telegram-forwarded; see
  :mod:`marcel_core.harness.approval`), and runs only on an explicit allow.
- **deny** — the action is blocked with a human-readable reason.

The default rule set focuses on the **command-execution surface** — ``bash``
today, ``code_exec`` when it lands in F3. It complements the self-mod path
guard (which covers ``write_file`` / ``edit_file`` path arguments) by
catching the same self-mod boundary when reached through a *shell command*,
and it asks for approval before genuinely dangerous commands run.

Rules are evaluated in order; the first match wins; an unmatched action
falls through to the policy's default verdict (``allow``). A rule matches on
the tool name plus a regex over one string argument.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Verdict(str, Enum):
    """The three policy outcomes."""

    ALLOW = 'allow'
    ASK = 'ask'
    DENY = 'deny'


@dataclass(frozen=True)
class PolicyDecision:
    """The result of classifying one action — carries the matched rule for audit."""

    verdict: Verdict
    reason: str
    rule: str


@dataclass(frozen=True)
class Rule:
    """One policy rule: a tool + arg + regex → verdict.

    ``tools`` is the set of tool names the rule applies to; ``arg`` is the
    argument whose string value is tested against ``pattern``. A rule with an
    empty ``tools`` set never matches (guards against an accidental match-all).
    """

    name: str
    verdict: Verdict
    reason: str
    tools: frozenset[str]
    arg: str
    pattern: re.Pattern[str]

    def matches(self, tool_name: str, args: dict) -> bool:
        if tool_name not in self.tools:
            return False
        value = str(args.get(self.arg, '') or '')
        return bool(self.pattern.search(value))


# --- default rule set -----------------------------------------------------

# The self-modification boundary, reachable through a shell command (the
# path guard already covers write_file/edit_file arguments). Kept in sync
# with marcel_core.harness.core_handlers._RESTRICTED conceptually.
_SELF_MOD_IN_SHELL = re.compile(
    r'(^|/|\s)(CLAUDE\.md|\.env|src/marcel_core/auth/|src/marcel_core/config\.py|restart_requested\.)',
)

# Genuinely dangerous shell patterns that warrant a human before they run.
_RISKY_SHELL = re.compile(
    r"""(
        \brm\s+-[a-z]*r[a-z]*f | \brm\s+-[a-z]*f[a-z]*r      # rm -rf / -fr
      | \bsudo\s                                             # privilege escalation
      | \bdd\s                                               # raw disk writes
      | \bmkfs\b | \bfdisk\b | \bparted\b                    # partitioning
      | >\s*/dev/(sd|nvme|vd)                                # writing a block device
      | \bchmod\s+-[a-z]*R[a-z]*\s+777 | \bchmod\s+777\s+/   # world-writable recursive / root
      | \bcurl\b[^\n|]*\|\s*(sudo\s+)?(ba)?sh | \bwget\b[^\n|]*\|\s*(ba)?sh   # curl|sh
      | :\(\)\s*\{                                           # fork bomb
      | \bgit\s+push\b[^\n]*--force                          # force push
      | \bsystemctl\s+(stop|disable|mask)                    # disabling services
      | \bdocker\s+(rm|kill|stop|rmi|system\s+prune)         # destroying containers
      | \b(shutdown|reboot|halt|poweroff)\b                  # power state
    )""",
    re.VERBOSE,
)

_COMMAND_TOOLS = frozenset({'bash', 'code_exec'})


def _default_rules() -> list[Rule]:
    return [
        Rule(
            name='self-mod-boundary-in-shell',
            verdict=Verdict.DENY,
            reason=(
                'shell command touches the self-modification boundary '
                '(CLAUDE.md, auth, core config, .env, or the restart flag); '
                'use the guarded write_file/edit_file path with the unlock flag instead'
            ),
            tools=_COMMAND_TOOLS,
            arg='command',
            pattern=_SELF_MOD_IN_SHELL,
        ),
        Rule(
            name='risky-shell-command',
            verdict=Verdict.ASK,
            reason='potentially destructive shell command — requires approval',
            tools=_COMMAND_TOOLS,
            arg='command',
            pattern=_RISKY_SHELL,
        ),
    ]


class CommandPolicy:
    """Ordered rule set that classifies actions allow / ask / deny.

    Amendable at runtime: ``allow_always`` prepends an ``allow`` rule for the
    exact command that was approved, so a later identical action auto-runs
    (the execpolicy-style "allow-always" from ADR-260628-ca8f39). Amendments
    are ordinary :class:`Rule` objects, so they are inspectable and auditable.
    """

    def __init__(self, rules: list[Rule] | None = None, default: Verdict = Verdict.ALLOW) -> None:
        self._rules: list[Rule] = list(rules) if rules is not None else _default_rules()
        self._default = default

    @property
    def rules(self) -> list[Rule]:
        """A copy of the current rule list (for inspection/audit)."""
        return list(self._rules)

    def classify(self, tool_name: str, args: dict) -> PolicyDecision:
        """Return the verdict for ``(tool_name, args)`` — first match wins."""
        for rule in self._rules:
            if rule.matches(tool_name, args):
                return PolicyDecision(rule.verdict, rule.reason, rule.name)
        return PolicyDecision(self._default, 'no policy rule matched', 'default')

    def allow_always(self, tool_name: str, args: dict, arg: str = 'command') -> Rule:
        """Amend the policy to always allow this exact action, and return the rule.

        Prepends an ``allow`` rule matching the exact ``args[arg]`` string so
        it wins over the ``ask``/``deny`` rules below it. Returns the new rule
        so the caller can persist it for audit and reload it next session.
        """
        value = str(args.get(arg, '') or '')
        rule = Rule(
            name=f'allow-always:{tool_name}:{value[:60]}',
            verdict=Verdict.ALLOW,
            reason='approved with allow-always by the user',
            tools=frozenset({tool_name}),
            arg=arg,
            pattern=re.compile(f'^{re.escape(value)}$'),
        )
        self._rules.insert(0, rule)
        return rule

    def add_rule(self, rule: Rule, *, front: bool = True) -> None:
        """Insert a rule (front by default so amendments take precedence)."""
        if front:
            self._rules.insert(0, rule)
        else:
            self._rules.append(rule)


def default_policy() -> CommandPolicy:
    """Return a fresh policy with Marcel's conservative default rules."""
    return CommandPolicy()
