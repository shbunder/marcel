"""Marcel agent wrapper around pydantic-ai Agent.

Provides a configured Agent instance with tools and instructions.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from pydantic_ai import Agent
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from marcel_core.composition import build_capabilities
from marcel_core.config import settings
from marcel_core.harness.context import MarcelDeps
from marcel_core.harness.model_chain import Tier, model_label
from marcel_core.jobs.capability import JOB_TOOLS
from marcel_core.marketplace.capability import MARKETPLACE_TOOLS
from marcel_core.tools.capability import DEV_TOOLS, UTILITY_TOOLS

log = logging.getLogger(__name__)

# Prefix for local (self-hosted, OpenAI-compatible) models. Strings of the
# shape ``local:<ollama_tag>`` are intercepted in :func:`create_marcel_agent`
# and routed to ``settings.marcel_local_llm_url`` via ``OpenAIChatModel``.
_LOCAL_PREFIX = 'local:'

# Sentinel: build the agent model-less. A ``model: inherit`` subagent is
# constructed this way so the harness SubAgents capability runs it on the
# parent's model at delegation time (FEAT-260718-b6d1da). Distinct from
# ``None``, which resolves to the configured default.
INHERIT_MODEL = 'inherit'

# Suggested models shown by list_models. Keys are pydantic-ai qualified strings
# (``provider:model``); values are human-readable display names. This is a
# curated suggestion list, not a whitelist — set_model accepts any qualified
# string, so any pydantic-ai-supported model works without a code change.
KNOWN_MODELS: dict[str, str] = {
    'anthropic:claude-sonnet-4-6': 'Claude Sonnet 4.6 (fast, recommended)',
    'anthropic:claude-opus-4-6': 'Claude Opus 4.6 (most capable)',
    'anthropic:claude-haiku-4-5-20251001': 'Claude Haiku 4.5 (fastest)',
    'openai:gpt-4o': 'GPT-4o (fast, multimodal)',
    'openai:gpt-4o-mini': 'GPT-4o mini (fastest, cheapest)',
    'openai:o1': 'o1 (reasoning)',
    'openai:o3-mini': 'o3-mini (fast reasoning)',
}


def default_model() -> str:
    """Return the currently-configured tier-1 model.

    Reads ``settings.marcel_standard_model`` at call time so tests can
    monkeypatch the attribute without reloading modules, and so ``.env``
    updates take effect on the next request without a restart.
    """
    return settings.marcel_standard_model


def all_models() -> dict[str, str]:
    """Return the curated list of suggested models for list_models UX.

    When ``marcel_local_llm_url`` and ``marcel_local_llm_model`` are both set,
    the local model is appended so the picker surfaces it. Otherwise the
    suggestion stays hidden to avoid pointing users at a broken route.
    """
    models = dict(KNOWN_MODELS)
    if settings.marcel_local_llm_url and settings.marcel_local_llm_model:
        key = f'{_LOCAL_PREFIX}{settings.marcel_local_llm_model}'
        models[key] = f'Local — {settings.marcel_local_llm_model} (self-hosted)'
    return models


def _build_local_model(model_string: str) -> OpenAIChatModel:
    """Resolve a ``local:<tag>`` string to a configured ``OpenAIChatModel``.

    Pydantic-ai doesn't know about a ``local`` provider, so we substitute an
    ``OpenAIChatModel`` instance pointed at ``settings.marcel_local_llm_url``
    before handing it to ``Agent()``. The tag after ``local:`` is the ollama
    model name (e.g. ``qwen3.5:4b``) which may itself contain a colon — we
    only split off the leading ``local:`` prefix.

    Raises:
        RuntimeError: If ``marcel_local_llm_url`` is not configured.
    """
    if not settings.marcel_local_llm_url:
        raise RuntimeError(
            f'Model {model_string!r} requires a local LLM server, but '
            'MARCEL_LOCAL_LLM_URL is not set. See docs/local-llm.md for setup.'
        )
    tag = model_string[len(_LOCAL_PREFIX) :]
    if not tag:
        raise RuntimeError(f'Empty local model tag in {model_string!r}.')
    provider = OpenAIProvider(base_url=settings.marcel_local_llm_url, api_key='ollama')
    log.info('resolving local model: tag=%s base_url=%s', tag, settings.marcel_local_llm_url)
    return OpenAIChatModel(tag, provider=provider)


# Aggregated tool registry — the role-gate source of truth.
#
# Since FEAT-260721-51f9e3 each domain declares its own ``(name, callable,
# role_required)`` triples next to its capability factory (domain-owns-
# factory, ADR-260720-f23f23); this aggregate preserves the single source of
# truth the role-gating rule requires: ``admin_tool_names()`` and
# ``available_tool_names()`` below read it, and composition attaches the
# same declarations as ``Capability`` tool bundles. Ordering here is the
# ordering tools are presented in when no filter is applied.
_TOOL_REGISTRY: list[tuple[str, object, str | None]] = [
    *DEV_TOOLS,
    *MARKETPLACE_TOOLS,
    *UTILITY_TOOLS,
    *JOB_TOOLS,
]


def available_tool_names(role: str) -> set[str]:
    """Return the set of tool names a given role can normally access.

    Used by the ``delegate`` tool (ISSUE-074) to compute the default pool
    for a subagent when its frontmatter omits ``tools:``, so the recursion
    guard and any ``disallowed_tools`` can be applied on top.
    """
    names = {name for name, _fn, required in _TOOL_REGISTRY if required is None or required == role}
    # `web` is a capability now (ADR-260720-9318b1), attached in composition
    # gated on tool_filter — but its name must stay in every role's pool so a
    # subagent frontmatter naming `web` still resolves (execution precedent).
    from marcel_core.capabilities.web import WEB_TOOL_NAME

    names |= {WEB_TOOL_NAME}
    if role == 'admin':
        # Shell and FileSystem are admin capabilities; their tool names join
        # the default pool so delegate's frontmatter-omitted children keep
        # shell/file powers (build_capabilities grants by filter match).
        from marcel_core.composition import FILESYSTEM_TOOL_NAMES, SHELL_TOOL_NAMES

        names |= SHELL_TOOL_NAMES | FILESYSTEM_TOOL_NAMES
    return names


def admin_tool_names() -> frozenset[str]:
    """Return the names of admin-tier (restricted) tools.

    The single source of truth for which tools require ``role == 'admin'``.
    Used by the event-bus role-gating handler
    (:mod:`marcel_core.harness.core_handlers`) as a defense-in-depth second
    layer behind the structural gate in :func:`create_marcel_agent` (which
    never registers these tools for a non-admin in the first place).
    """
    from marcel_core.composition import FILESYSTEM_TOOL_NAMES, SHELL_TOOL_NAMES

    registry_admin = frozenset(name for name, _fn, required in _TOOL_REGISTRY if required == 'admin')
    # Shell and FileSystem are admin-only capabilities (FEAT-260718-38235c);
    # their tool names join the gate so layer 2 covers them too. ``delegate``
    # is the SubAgents capability's tool (FEAT-260718-b6d1da) — admin-tier,
    # attached in composition only for admin builds; named here so the bus
    # gate covers it as the second layer.
    return registry_admin | SHELL_TOOL_NAMES | FILESYSTEM_TOOL_NAMES | {'delegate'}


def create_marcel_agent(
    model: str | Model | None = None,
    system_prompt: str = '',
    role: str = 'user',
    tool_filter: set[str] | None = None,
    memory: bool = True,
    cwd: str | None = None,
    code_mode: bool = True,
    user_slug: str | None = None,
    skills: bool = True,
    eager_skill: str | None = None,
    connectors: bool = True,
    subagents: bool = True,
    tier: 'Tier | None' = None,
    channel: str | None = None,
    extra_capabilities: Sequence[AbstractCapability[MarcelDeps]] | None = None,
) -> Agent[MarcelDeps, str]:
    """Create a configured Marcel agent with a role-appropriate tool set.

    Admin users receive the full suite of power tools (Shell, FileSystem,
    git, claude_code, delegate, marketplace). Regular users receive the
    all-users tools — the unified ``marcel`` utility, the ``web`` capability,
    chart rendering, and job management — enough for a household assistant
    without exposing arbitrary shell access.

    Args:
        model: Fully-qualified pydantic-ai model string, e.g.
               ``'anthropic:claude-sonnet-4-6'`` or ``'openai:gpt-4o'``.
               The special prefix ``'local:<tag>'`` routes to the self-hosted
               OpenAI-compatible server at ``settings.marcel_local_llm_url``
               (used by the job local-fallback path — see ISSUE-070). All
               other strings pass through to ``Agent()`` verbatim. A
               pydantic-ai ``Model`` *instance* is also accepted and used
               verbatim — the scenario-test seam (ADR-260706-b88015) that
               lets ``marcel_testing`` drive this factory with a scripted
               model, with no provider inference and no API key. When
               ``None`` (the default), resolves to :func:`default_model` at
               call time, i.e. ``settings.marcel_standard_model``.
        system_prompt: The system prompt string (must be provided).
        role: The user's role — ``'admin'`` or ``'user'``.
        tool_filter: If provided, only tools whose names appear in this set
            are registered. Used by ``delegate`` (ISSUE-074) to build
            constrained subagents. Role-gated tools (admin-only) still
            respect the role check — an explicit request for ``bash`` in a
            ``user`` role subagent is silently dropped. When ``None``, the
            default role-based pool is used.
        memory: Attach the Memory notebook capability (tools + bounded
            injection). ``False`` for the lean paths — headless jobs and
            the explain tier.
        cwd: The session working directory — roots the admin Shell and
            FileSystem capabilities (falls back to the project root).
        tier: The turn's resolved model tier — enables the turn-quality
            capabilities (Planning on STANDARD/POWER, LimitWarner sized to
            the tier's context budget). ``None`` (the lean paths: jobs,
            subagent children, explain) skips both (FEAT-260718-637764).
        extra_capabilities: Caller-assembled capabilities appended after
            the composition root's list. The generic seam for callers that
            compose their own surface — scoped job runs pass their eager
            skills and non-deferred connectors here (FEAT-260718-49a01a).

    Returns:
        Configured pydantic-ai Agent instance.
    """
    if not system_prompt:
        system_prompt = 'You are Marcel, a helpful AI assistant.'

    if model is None:
        model = default_model()

    model_arg: str | Model | None
    if isinstance(model, Model):
        model_arg = model
    elif model == INHERIT_MODEL:
        # Model-less build — the SubAgents capability supplies the parent's
        # model at delegation time.
        model_arg = None
    elif model.startswith(_LOCAL_PREFIX):
        model_arg = _build_local_model(model)
    else:
        model_arg = model

    # Registry tools attach as domain-owned Capability bundles inside
    # build_capabilities (FEAT-260721-51f9e3). The structural role gate is
    # unchanged in kind: an admin bundle is simply never composed for a
    # non-admin, so the model cannot see its tools. The event bus (via the
    # MarcelPolicy capability) remains the second, harness-level layer.
    capabilities = build_capabilities(
        role=role,
        cwd=cwd,
        tool_filter=tool_filter,
        memory=memory,
        code_mode=code_mode,
        user_slug=user_slug,
        skills=skills,
        eager_skill=eager_skill,
        connectors=connectors,
        subagents=subagents,
        tier=tier,
        channel=channel,
    )
    if extra_capabilities:
        capabilities.extend(extra_capabilities)

    agent: Agent[MarcelDeps, str] = Agent(
        model_arg,
        deps_type=MarcelDeps,
        instructions=system_prompt,
        retries=2,
        end_strategy='exhaustive',
        capabilities=capabilities,
    )

    log.info(
        'agent ready: model=%s role=%s capabilities=%d%s',
        model_label(model),
        role,
        len(capabilities),
        ' (filtered)' if tool_filter is not None else '',
    )
    return agent
