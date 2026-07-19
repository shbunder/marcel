"""Marcel agent wrapper around pydantic-ai Agent.

Provides a configured Agent instance with tools and instructions.
"""

from __future__ import annotations

import logging

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.toolsets import FunctionToolset

from marcel_core.composition import build_capabilities
from marcel_core.config import settings
from marcel_core.harness.context import MarcelDeps
from marcel_core.harness.model_chain import model_label
from marcel_core.jobs import tool as job_tools
from marcel_core.tools import (
    charts as chart_tools,
    claude_code as claude_code_tool,
    core as core_tools,
    delegate as delegate_tool,
    marcel as marcel_tools,
    toolkit as toolkit_tools,
)
from marcel_core.tools.web import web as web_tool

log = logging.getLogger(__name__)

# Prefix for local (self-hosted, OpenAI-compatible) models. Strings of the
# shape ``local:<ollama_tag>`` are intercepted in :func:`create_marcel_agent`
# and routed to ``settings.marcel_local_llm_url`` via ``OpenAIChatModel``.
_LOCAL_PREFIX = 'local:'

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

# Historical constant — retained for backwards compatibility with any
# external imports. Prefer :func:`default_model` in new code so runtime
# changes to ``settings.marcel_standard_model`` (env var updates, test
# monkeypatches) are honoured.
DEFAULT_MODEL = 'anthropic:claude-sonnet-4-6'


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


# Name ↔ tool function mapping used by the registration loop below. Keeping
# this as a single source of truth makes it trivial for ``tool_filter`` (and
# the ``delegate`` tool's agent frontmatter) to reference tools by stable
# short names like ``'bash'`` or ``'read_file'`` without knowing which module
# they live in. Ordering here is the ordering they get registered in when no
# filter is applied.
#
# Entries are ``(name, callable, role_required)`` where ``role_required`` is
# either ``'admin'`` (restricted) or ``None`` (available to every role).
_TOOL_REGISTRY: list[tuple[str, object, str | None]] = [
    # Web: search + browser actions unified behind one dispatcher. Always
    # available — the dispatcher returns a clean error for browser actions
    # when playwright isn't installed, so ``search`` still works bare.
    ('web', web_tool, None),
    # Admin power tools. Shell (run_command …) and FileSystem (read_file …)
    # are capabilities now — attached in composition.build_capabilities for
    # admin roles, gated as layer 2 via admin_tool_names() below.
    ('git_status', core_tools.git_status, 'admin'),
    ('git_diff', core_tools.git_diff, 'admin'),
    ('git_log', core_tools.git_log, 'admin'),
    ('git_add', core_tools.git_add, 'admin'),
    ('git_commit', core_tools.git_commit, 'admin'),
    ('git_push', core_tools.git_push, 'admin'),
    ('claude_code', claude_code_tool.claude_code, 'admin'),
    ('delegate', delegate_tool.delegate, 'admin'),
    # All-user tools
    ('generate_chart', chart_tools.generate_chart, None),
    ('toolkit', toolkit_tools.toolkit, None),
    ('marcel', marcel_tools.marcel, None),
    # Job management
    ('create_job', job_tools.create_job, None),
    ('list_jobs', job_tools.list_jobs, None),
    ('get_job', job_tools.get_job, None),
    ('update_job', job_tools.update_job, None),
    ('delete_job', job_tools.delete_job, None),
    ('run_job_now', job_tools.run_job_now, None),
    ('job_templates', job_tools.job_templates, None),
    ('job_cache_write', job_tools.job_cache_write, None),
    ('job_cache_read', job_tools.job_cache_read, None),
]


def available_tool_names(role: str) -> set[str]:
    """Return the set of tool names a given role can normally access.

    Used by the ``delegate`` tool (ISSUE-074) to compute the default pool
    for a subagent when its frontmatter omits ``tools:``, so the recursion
    guard and any ``disallowed_tools`` can be applied on top.
    """
    names = {name for name, _fn, required in _TOOL_REGISTRY if required is None or required == role}
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
    # their tool names join the gate so layer 2 covers them too.
    return registry_admin | SHELL_TOOL_NAMES | FILESYSTEM_TOOL_NAMES


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
) -> Agent[MarcelDeps, str]:
    """Create a configured Marcel agent with a role-appropriate tool set.

    Admin users receive the full suite of power tools (bash, file I/O, git,
    claude_code, delegate). Regular users receive only integration and the
    unified marcel utils tool — enough for a household assistant without
    exposing arbitrary shell access.

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
            injection). ``False`` for the lean paths — headless jobs
            (until FEAT-260718-49a01a declares scoping) and the explain
            tier.
        cwd: The session working directory — roots the admin Shell and
            FileSystem capabilities (falls back to the project root).

    Returns:
        Configured pydantic-ai Agent instance.
    """
    if not system_prompt:
        system_prompt = 'You are Marcel, a helpful AI assistant.'

    if model is None:
        model = default_model()

    model_arg: str | Model
    if isinstance(model, Model):
        model_arg = model
    elif model.startswith(_LOCAL_PREFIX):
        model_arg = _build_local_model(model)
    else:
        model_arg = model

    # Build the tool set into a FunctionToolset, then wrap it so every call
    # routes through the turn's event bus (tool_call / tool_result). The
    # structural role gate below stays the *primary* defense — an admin tool
    # is simply never added for a non-admin, so the model cannot see it. The
    # bus (via the MarcelPolicy capability) is a second, harness-level
    # enforcement layer.
    toolset: FunctionToolset[MarcelDeps] = FunctionToolset()
    registered: list[str] = []
    for name, fn, required_role in _TOOL_REGISTRY:
        # Role gate — admin-only tools are always stripped for non-admin agents,
        # even if the caller explicitly allowlists them via ``tool_filter``.
        if required_role == 'admin' and role != 'admin':
            continue
        # Name gate — when a filter is supplied, drop anything not in it.
        if tool_filter is not None and name not in tool_filter:
            continue
        toolset.add_function(fn)  # type: ignore[arg-type]
        registered.append(name)

    agent: Agent[MarcelDeps, str] = Agent(
        model_arg,
        deps_type=MarcelDeps,
        instructions=system_prompt,
        retries=2,
        end_strategy='exhaustive',
        capabilities=build_capabilities(
            role=role,
            cwd=cwd,
            tool_filter=tool_filter,
            memory=memory,
            code_mode=code_mode,
            user_slug=user_slug,
            skills=skills,
            eager_skill=eager_skill,
            connectors=connectors,
        ),
        toolsets=[toolset],
    )

    log.info(
        'agent ready: model=%s role=%s tools=%s%s',
        model_label(model),
        role,
        len(registered),
        ' (filtered)' if tool_filter is not None else '',
    )
    return agent
