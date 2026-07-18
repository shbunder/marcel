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

The default classification targets the **command-execution surface** —
``bash`` today, ``code_exec`` when it lands in F3. It **tokenizes** the
command with :func:`shlex.split` before matching, so quote-splitting
(``cat C"L"AUDE.md``) and flag-splitting (``rm -r -f``) do not evade it, and
it inspects command heads so a risky name only matters in command position.

**This is an advisory speed-bump, not a containment boundary.** A blocklist
over shell text cannot be made complete — e.g. a relative path reached via a
prior ``cd`` is not resolved here. The real containment is the OS sandbox
(ADR-260628-0fc1e2, layer 1); until it lands, an unmatched command runs with
the session's full privileges. Do **not** cite this policy as the reason
``bash`` is safe.

Custom rules (from ``allow_always`` and ``add_rule``) are evaluated first
(so an approved command auto-runs), then the built-in token-aware checks,
then the policy default (``allow``).
"""

from __future__ import annotations

import os
import re
import shlex
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
    """A custom rule: tool + arg + regex → verdict (used by allow_always / add_rule).

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


# The shell-command surface (FEAT-260718-38235c): the harness Shell
# capability's foreground and background spawn tools. `run_code` (CodeMode)
# is deliberately absent — it carries Python, not shell, and its authority
# is limited to the code-mode-eligible tools (ADR-260718-d511f7).
_COMMAND_TOOLS = frozenset({'run_command', 'start_command'})

# The self-modification boundary — checked against each token *and* the raw
# command, so quote-splitting cannot hide it (``restart_requested".prod"`` →
# token ``restart_requested.prod``). Direct writes under ``.git/`` are a hook
# /ref injection vector.
_SELF_MOD_PATH = re.compile(
    r'(^|/)(CLAUDE\.md|\.env(\.|$)|restart_requested\.|src/marcel_core/auth/|src/marcel_core/config\.py)',
)
_DOTGIT = re.compile(r'(^|/)\.git(/|$)')

# Risky command *names* (matter only in command position).
_RISKY_HEADS = frozenset({'sudo', 'dd', 'mkfs', 'fdisk', 'parted', 'shutdown', 'reboot', 'halt', 'poweroff'})

# Shell operators that begin a new command segment.
_SEPARATORS = frozenset({'|', '||', '&&', ';', '&', '|&', '(', ')', '{', '}'})

# Raw-text patterns for constructs that are not a single command name.
_RISKY_RAW = re.compile(
    r"""(
        \|\s*(sudo\s+)?(ba)?sh\b          # pipe into a shell (curl … | sh)
      | \bchmod\s+(-\S*\s+)*(-\S*R\S*\s+)?0*777 | \bchmod\s+0*777\s+/   # world-writable
      | :\(\)\s*\{                        # fork bomb
      | \bsystemctl\s+(stop|disable|mask) # disabling services
      | \bdocker\s+(rm|kill|stop|rmi|system\s+prune)   # destroying containers
      | \bfind\b[^\n]*\s-delete           # find … -delete
      | >\s*/dev/(sd|nvme|vd|hd)          # writing a block device
    )""",
    re.VERBOSE,
)


def _tokenize(command: str) -> list[str] | None:
    """Tokenize a shell command; ``None`` if quoting is unbalanced (suspicious)."""
    try:
        return shlex.split(command)
    except ValueError:
        return None


def _command_heads(tokens: list[str]) -> list[str]:
    """Return the first token of each command segment (command-position tokens).

    ``sudo`` is transparent — the token after ``sudo`` is also a head — so
    ``sudo rm -rf`` exposes both ``sudo`` and ``rm``.
    """
    heads: list[str] = []
    expect_head = True
    for tok in tokens:
        if tok in _SEPARATORS:
            expect_head = True
            continue
        if expect_head:
            heads.append(tok)
            # sudo/env wrappers pass through to the real command.
            expect_head = tok in {'sudo', 'env', 'command', 'nice', 'nohup', 'time', 'xargs'}
    return heads


def _safe_realpath(token: str) -> str:
    try:
        return os.path.realpath(token)
    except (OSError, ValueError):
        return token


def _self_mod_reason(command: str, tokens: list[str]) -> str | None:
    candidates = [command, *tokens, *(_safe_realpath(t) for t in tokens)]
    if any(_SELF_MOD_PATH.search(c) for c in candidates):
        return 'the self-modification boundary (CLAUDE.md, auth, core config, .env, or the restart flag)'
    if any(_DOTGIT.search(c) for c in candidates):
        return 'the .git directory'
    return None


def _rm_recursive_force(tokens: list[str]) -> bool:
    if 'rm' not in _command_heads(tokens):
        return False
    short = ''.join(t[1:] for t in tokens if len(t) > 1 and t[0] == '-' and t[1] != '-')
    longs = {t for t in tokens if t.startswith('--')}
    recursive = 'r' in short.lower() or '--recursive' in longs
    force = 'f' in short or '--force' in longs
    return recursive and force


def _destructive_git(tokens: list[str]) -> bool:
    """True for history/tree-rewriting git ops reachable via bash."""
    if 'git' not in tokens:
        return False
    rest = tokens[tokens.index('git') + 1 :]
    # Skip global options (``git -C x reset --hard``).
    sub = next((t for t in rest if not t.startswith('-')), '')
    flags = set(rest)
    if sub == 'push' and (flags & {'-f', '--force', '--force-with-lease'}):
        return True
    if sub == 'reset' and '--hard' in flags:
        return True
    if sub == 'clean' and any('f' in t for t in rest if t.startswith('-') and not t.startswith('--')):
        return True
    return False


def _risky_reason(command: str, tokens: list[str]) -> str | None:
    if _rm_recursive_force(tokens):
        return 'recursive/forced file removal (rm -rf)'
    heads = _command_heads(tokens)
    # `mkfs.ext4` / `mkfs.xfs` count as `mkfs`.
    hit = next((h for h in heads if h in _RISKY_HEADS or h.split('.', 1)[0] in _RISKY_HEADS), None)
    if hit is not None:
        return f'{hit!r} in command position'
    if _destructive_git(tokens):
        return 'a destructive git operation (force-push / reset --hard / clean -f)'
    if _RISKY_RAW.search(command):
        return 'a potentially destructive shell construct'
    return None


def _classify_command(command: str) -> PolicyDecision | None:
    """Built-in token-aware classification for a command string, or ``None``."""
    tokens = _tokenize(command)
    if tokens is None:
        return PolicyDecision(
            Verdict.ASK,
            'command has unbalanced quotes and cannot be classified safely',
            'malformed-command',
        )
    reason = _self_mod_reason(command, tokens)
    if reason is not None:
        return PolicyDecision(
            Verdict.DENY,
            f'shell command touches {reason}; use the guarded write_file/edit_file path with the unlock flag instead',
            'self-mod-boundary-in-shell',
        )
    reason = _risky_reason(command, tokens)
    if reason is not None:
        return PolicyDecision(Verdict.ASK, f'{reason} — requires approval', 'risky-shell-command')
    return None


class CommandPolicy:
    """Classifies actions allow / ask / deny — custom rules, then built-ins.

    Amendable at runtime: ``allow_always`` prepends an exact-match ``allow``
    rule for an approved command, so a later identical action auto-runs (the
    execpolicy-style allow-always from ADR-260628-ca8f39). Amendments are
    ordinary :class:`Rule` objects, inspectable and auditable, and are checked
    *before* the built-in risk classification.
    """

    def __init__(self, rules: list[Rule] | None = None, default: Verdict = Verdict.ALLOW) -> None:
        self._rules: list[Rule] = list(rules) if rules is not None else []
        self._default = default

    @property
    def rules(self) -> list[Rule]:
        """A copy of the current custom rule list (for inspection/audit)."""
        return list(self._rules)

    def classify(self, tool_name: str, args: dict) -> PolicyDecision:
        """Return the verdict for ``(tool_name, args)``.

        Custom rules first (first match wins), then the built-in token-aware
        checks for command tools, then the policy default.
        """
        for rule in self._rules:
            if rule.matches(tool_name, args):
                return PolicyDecision(rule.verdict, rule.reason, rule.name)

        if tool_name in _COMMAND_TOOLS:
            command = str(args.get('command', '') or '')
            if command:
                decision = _classify_command(command)
                if decision is not None:
                    return decision

        return PolicyDecision(self._default, 'no policy rule matched', 'default')

    def allow_always(self, tool_name: str, args: dict, arg: str = 'command') -> Rule:
        """Amend the policy to always allow this exact action, and return the rule.

        Prepends an ``allow`` rule matching the exact ``args[arg]`` string
        (``re.escape``\\d, ``^…$``-anchored — no widening) so it wins over the
        built-in ``ask``/``deny`` classification. Returns the new rule so the
        caller can persist it for audit.
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
        """Insert a custom rule (front by default so amendments take precedence)."""
        if front:
            self._rules.insert(0, rule)
        else:
            self._rules.append(rule)


def default_policy() -> CommandPolicy:
    """Return a fresh policy with Marcel's built-in command classification."""
    return CommandPolicy()
