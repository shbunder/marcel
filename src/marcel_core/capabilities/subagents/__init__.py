"""Marcel subagents on the harness ``SubAgents`` capability (FEAT-260718-b6d1da).

Delegation rides :class:`pydantic_ai_harness.subagents.SubAgents`: the parent
agent gets one ``delegate`` tool listing the available subagents, and each
delegation spawns a scoped child run. This package owns everything Marcel-
specific about that:

* **Doc discovery + parsing.** Agent markdown files resolve through the same
  chain as skills — ``<zoo>/agents/`` (global) → ``<data>/agents/`` (legacy
  install-wide customizations) → ``<zoo>/users/<slug>/agents/`` →
  ``<data>/users/<slug>/agents/`` — most specific wins on a name collision.
  Marcel's frontmatter is richer than the harness disk format (which reads
  only ``name``/``description``/``tools``): ``model`` tier sentinels,
  ``disallowed_tools``, ``max_requests`` and ``timeout_seconds`` cannot be
  expressed there, so docs are parsed here and lifted into explicit
  :class:`~pydantic_ai_harness.subagents.SubAgent` wrappers
  (``agent_folders=None`` — the harness's own disk conventions stay off).

* **Child assembly.** Each child is a full ``create_marcel_agent`` build —
  role-filtered tool pool, MarcelPolicy, persistence, compaction — so child
  tool calls are policy-gated and recorder-visible exactly like the parent's.
  ``model: inherit`` builds the child model-less; the harness then runs it on
  the parent's model at delegation time. Tier sentinels resolve per build
  (i.e. per turn), so env-var changes apply without a restart; an agent whose
  tier is unconfigured is skipped with a warning and simply absent from the
  catalog.

* **Recursion rule.** A child is built with ``subagents=False`` unless its
  frontmatter allowlist explicitly names ``delegate`` — the capability (and
  therefore the tool) never reaches an un-opted-in child.

* **Fresh turn state.** The harness forwards the parent's ``deps`` verbatim;
  :class:`FreshTurnAgent` swaps in a derived conversation id and a fresh
  ``TurnState`` so per-turn flags never leak between parent and child, and
  the child's persistence never interleaves with the parent conversation.

Budget note (FR3, amended): ``forward_usage=True`` folds a child's tokens
into the parent run **only** for agents without ``max_requests`` — the
harness makes a per-agent ``usage_limits`` an isolated budget (its exhaustion
is a soft, readable tool result instead of aborting the turn). Marcel's stock
agents all set ``max_requests``, preserving today's isolated-budget behavior.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from marcel_core.harness.model_chain import make_tier_sentinel

if TYPE_CHECKING:
    from pydantic_ai import Agent
    from pydantic_ai_harness.subagents import SubAgents

    from marcel_core.harness.context import MarcelDeps

log = logging.getLogger(__name__)

# The delegation tool keeps its historical name — prompts, docs and the
# admin_tool_names() bus gate all reference ``delegate``.
DELEGATE_TOOL_NAME = 'delegate'

# Children are built eagerly at capability construction, so an agent doc that
# opts into `delegate` triggers a nested capability build for its children.
# This bounds that nesting: a doc cycle (an opted-in agent that can reach
# itself) would otherwise recurse at build time, not run time.
MAX_DELEGATION_DEPTH = 2
_build_depth = 0

# Wall-clock default for a delegated run when the doc omits timeout_seconds.
DEFAULT_TIMEOUT_SECONDS = 300


@dataclass
class SubagentDoc:
    """A parsed subagent definition (successor of the retired ``AgentDoc``)."""

    name: str
    description: str
    system_prompt: str
    source: str  # 'zoo-global' | 'data-global' | 'zoo-user' | 'data-user'
    model: str | None = None  # qualified string, tier sentinel, or None = inherit
    tools: list[str] | None = None  # allowlist; None = role default pool
    disallowed_tools: list[str] = field(default_factory=list)
    max_requests: int | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    @property
    def wants_delegate(self) -> bool:
        """Whether this agent explicitly opted into further delegation."""
        return self.tools is not None and DELEGATE_TOOL_NAME in self.tools


def _agent_dirs(user_slug: str | None) -> list[tuple[Path, str]]:
    """``(path, source)`` roots in least→most-specific order.

    Mirrors the skills chain with one extra root: ``<data>/agents/`` predates
    per-user scoping as the install-wide customization surface and keeps
    working, slotted between the global and per-user roots.
    """
    from marcel_core.config import settings

    dirs: list[tuple[Path, str]] = []
    zoo = settings.zoo_dir
    if zoo is not None and (zoo / 'agents').is_dir():
        dirs.append((zoo / 'agents', 'zoo-global'))
    if (settings.data_dir / 'agents').is_dir():
        dirs.append((settings.data_dir / 'agents', 'data-global'))
    if user_slug:
        from marcel_core.auth import valid_user_slug

        if not valid_user_slug(user_slug):
            log.warning('subagents: refusing per-user discovery for invalid slug')
            return dirs
        if zoo is not None and (zoo / 'users' / user_slug / 'agents').is_dir():
            dirs.append((zoo / 'users' / user_slug / 'agents', 'zoo-user'))
        data_user = settings.data_dir / 'users' / user_slug / 'agents'
        if data_user.is_dir():
            dirs.append((data_user, 'data-user'))
    return dirs


def _parse_agent_file(path: Path, source: str) -> SubagentDoc | None:
    """Parse one ``<name>.md`` — ``None`` (with a warning) when nonconformant.

    Frontmatter contract (unknown keys tolerated with a debug note, for
    forward-compat with other runtimes' agent files):

    - ``name`` (defaults to the file stem), ``description``
    - ``model`` — ``inherit``/absent → parent's model; a bare tier name
      (``local``/``fast``/``standard``/``power``) → tier sentinel; anything else a
      qualified pydantic-ai string. The removed ``backup`` tier is rejected.
    - ``tools`` / ``disallowed_tools`` (camelCase aliases accepted)
    - ``max_requests`` (aliases ``maxRequests``/``maxTurns``) → per-child
      isolated request budget; ``timeout_seconds``/``timeoutSeconds``
    """
    from marcel_core.skills.loader import _parse_frontmatter

    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        log.warning('subagents: could not read %s', path, exc_info=True)
        return None

    fm, body = _parse_frontmatter(text)
    name = str(fm.get('name') or path.stem)

    known = {
        'name',
        'description',
        'model',
        'tools',
        'disallowed_tools',
        'disallowedTools',
        'max_requests',
        'maxRequests',
        'maxTurns',
        'timeout_seconds',
        'timeoutSeconds',
    }
    for key in fm.keys() - known:
        log.debug('subagents: %s has unrecognized frontmatter key %r (ignored)', path, key)

    model_raw = fm.get('model')
    if model_raw in (None, '', 'inherit'):
        model: str | None = None
    elif isinstance(model_raw, str) and model_raw == 'backup':
        log.warning(
            "subagents: %s uses removed tier 'backup' — skipping; migrate to model: fast|standard|power.",
            path,
        )
        return None
    elif isinstance(model_raw, str) and (sentinel := make_tier_sentinel(model_raw)) is not None:
        model = sentinel
    else:
        model = str(model_raw)

    def _first_present(*keys: str) -> object | None:
        # NOT an `or`-chain: a legitimate falsy value (`timeout_seconds: 0`)
        # must win over a later alias, not silently fall through to the
        # default (the old loader had exactly that bug).
        for key in keys:
            if (value := fm.get(key)) is not None:
                return value
        return None

    tools_raw = fm.get('tools')
    tools = [str(t) for t in tools_raw] if isinstance(tools_raw, list) else None
    disallowed_raw = _first_present('disallowed_tools', 'disallowedTools') or []
    disallowed = [str(t) for t in disallowed_raw] if isinstance(disallowed_raw, list) else []
    max_requests_raw = _first_present('max_requests', 'maxRequests', 'maxTurns')
    timeout_raw = _first_present('timeout_seconds', 'timeoutSeconds')

    return SubagentDoc(
        name=name,
        description=str(fm.get('description', '')),
        system_prompt=body.strip(),
        source=source,
        model=model,
        tools=tools,
        disallowed_tools=disallowed,
        max_requests=int(str(max_requests_raw)) if max_requests_raw is not None else None,
        timeout_seconds=int(str(timeout_raw)) if timeout_raw is not None else DEFAULT_TIMEOUT_SECONDS,
    )


def load_agent_docs(user_slug: str | None = None) -> list[SubagentDoc]:
    """Discover every subagent doc visible to *user_slug*, sorted by name."""
    docs: dict[str, SubagentDoc] = {}
    for agents_path, source in _agent_dirs(user_slug):
        for entry in sorted(agents_path.iterdir()):
            if not entry.is_file() or not entry.name.endswith('.md') or entry.name.startswith(('_', '.')):
                continue
            doc = _parse_agent_file(entry, source)
            if doc is not None:
                docs[doc.name] = doc
    return sorted(docs.values(), key=lambda d: d.name)


class SubagentNotFoundError(LookupError):
    """Raised by :func:`load_agent_doc` for an unknown agent name."""


def load_agent_doc(name: str, user_slug: str | None = None) -> SubagentDoc:
    """Load one subagent doc by name, or raise with the available names."""
    docs = load_agent_docs(user_slug)
    for doc in docs:
        if doc.name == name:
            return doc
    raise SubagentNotFoundError(f'No subagent named {name!r}. Available: {[d.name for d in docs]}')


def _tool_filter(doc: SubagentDoc, role: str) -> set[str]:
    """The child's tool pool, with the recursion guard applied.

    An explicit ``tools`` allowlist is honored minus ``disallowed_tools``;
    an absent one expands to the role-default pool. ``delegate`` is discarded
    unless the doc opted in: it names the capability tool, not a registry
    tool, so keeping it is harmless for tool registration — but the
    composition gate requires it in the filter before attaching the nested
    SubAgents capability, so discarding it for an opted-in doc would render
    ``subagents=doc.wants_delegate`` inert (pre-close finding,
    FEAT-260718-b6d1da).
    """
    from marcel_core.harness.agent import available_tool_names

    pool = set(doc.tools) if doc.tools is not None else available_tool_names(role)
    pool -= set(doc.disallowed_tools)
    if not doc.wants_delegate:
        pool.discard(DELEGATE_TOOL_NAME)
    return pool


def build_child_agent(
    doc: SubagentDoc,
    *,
    role: str,
    cwd: str | None = None,
    user_slug: str | None = None,
    memory: bool = True,
    code_mode: bool = True,
) -> Agent[MarcelDeps, str]:
    """Build the child agent for one doc — the shared seam for the capability
    and for jobs' SUBAGENT dispatch (FR4).

    ``memory``/``code_mode`` default on to match delegation-path parity
    (interactive children always carried both); the jobs path passes
    ``False`` for each, preserving its historical lean build.

    Raises:
        marcel_core.harness.model_chain.TierNotConfigured: The doc pins a
            tier whose env var is unset — callers skip (capability) or fail
            readably (jobs).
    """
    from marcel_core.harness.agent import INHERIT_MODEL, create_marcel_agent
    from marcel_core.harness.model_chain import is_tier_sentinel, resolve_tier_sentinel

    model: str = doc.model if doc.model is not None else INHERIT_MODEL
    if is_tier_sentinel(model):
        model = resolve_tier_sentinel(model)

    return create_marcel_agent(
        model=model,
        system_prompt=doc.system_prompt or 'You are a Marcel subagent.',
        role=role,
        tool_filter=_tool_filter(doc, role),
        cwd=cwd,
        user_slug=user_slug,
        memory=memory,
        code_mode=code_mode,
        skills=False,
        connectors=False,
        subagents=doc.wants_delegate,
    )


def _fresh_turn_wrapper(agent: Any, agent_name: str) -> Any:
    """Wrap *agent* so each delegation runs with derived, isolated deps.

    The harness forwards the parent's deps verbatim; Marcel's contract is a
    derived conversation id (child persistence never interleaves with the
    parent conversation) and a fresh ``TurnState`` (per-turn flags like
    ``notified`` don't leak in either direction).
    """
    import dataclasses

    from pydantic_ai.agent import WrapperAgent

    from marcel_core.harness.context import TurnState

    class FreshTurnAgent(WrapperAgent):
        async def run(self, *args: Any, deps: Any = None, **kwargs: Any) -> Any:
            if deps is not None:
                deps = dataclasses.replace(
                    deps,
                    conversation_id=f'{deps.conversation_id}:delegate:{agent_name}',
                    turn=TurnState(),
                )
            return await self.wrapped.run(*args, deps=deps, **kwargs)

    return FreshTurnAgent(agent)


def build_subagents_capability(
    *,
    user_slug: str | None,
    role: str = 'user',
    cwd: str | None = None,
) -> SubAgents | None:
    """The ``SubAgents`` capability for this build, or ``None`` when no
    agent docs are visible (the tool then never appears) or the delegation
    depth bound is reached."""
    from pydantic_ai.usage import UsageLimits
    from pydantic_ai_harness.subagents import SubAgent, SubAgents

    from marcel_core.harness.model_chain import TierNotConfigured

    global _build_depth
    if _build_depth >= MAX_DELEGATION_DEPTH:
        return None

    wrappers: list[SubAgent] = []
    for doc in load_agent_docs(user_slug):
        try:
            _build_depth += 1
            try:
                child = build_child_agent(doc, role=role, cwd=cwd, user_slug=user_slug)
            finally:
                _build_depth -= 1
        except TierNotConfigured as exc:
            log.warning(
                'subagents: %s requires tier %r but MARCEL_%s_MODEL is unset — not offered',
                doc.name,
                exc.tier,
                exc.tier.upper(),
            )
            continue
        except Exception:
            log.exception('subagents: could not build %s — not offered', doc.name)
            continue
        wrappers.append(
            SubAgent(
                agent=_fresh_turn_wrapper(child, doc.name),
                name=doc.name,
                description=doc.description,
                usage_limits=UsageLimits(request_limit=doc.max_requests) if doc.max_requests else None,
                timeout_seconds=float(doc.timeout_seconds),
            )
        )
    if not wrappers:
        return None
    return SubAgents(
        agents=wrappers,
        agent_folders=None,  # Marcel's scoping chain replaces the disk conventions
        forward_usage=True,
        tool_name=DELEGATE_TOOL_NAME,
    )
