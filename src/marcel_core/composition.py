"""The composition root — the one place capability lists are assembled.

Per ADR-260718-0cf8e8, an agent is a composition of capabilities and this
module is the only place that composition is built. Capability packages
under :mod:`marcel_core.capabilities` never import each other; they meet
here. The list grows as the FEAT-260718-* roadmap features land their
capabilities.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_ai.capabilities import AbstractCapability, Instrumentation, ToolSearch
from pydantic_ai_harness.code_mode import CodeMode
from pydantic_ai_harness.compaction import ClampOversizedMessages, ClearToolResults
from pydantic_ai_harness.memory import Memory
from pydantic_ai_harness.overflowing_tool_output import (
    Band,
    OverflowingToolOutput,
    Spill,
    Truncate,
)
from pydantic_ai_harness.step_persistence import StepPersistence

from marcel_core.capabilities.execution import FilteredFileSystem, SandboxedShell
from marcel_core.capabilities.memory import MEMORY_GUIDANCE, memory_store_for
from marcel_core.capabilities.persistence import persistence_store
from marcel_core.capabilities.persistence.overflow import PasteOverflowStore
from marcel_core.capabilities.policy import MarcelPolicy
from marcel_core.config import settings
from marcel_core.connectors.models import Discovery as ConnectorDiscovery
from marcel_core.harness.context import MarcelDeps
from marcel_core.tracing import get_instrumentation_settings

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])

# Tools the model may orchestrate from run_code (CodeMode/Monty — the
# code_exec successor, ADR-260718-d511f7). Conservative opt-in, and the bar
# is that the tool's own body is safe to invoke from model-written code:
# never approval-gated (an approval inside generated code surfaces as a raw
# retry), never a dispatcher (`toolkit`/`marcel`), and never a tool that
# itself executes model-controlled code. `generate_chart` is deliberately
# EXCLUDED — it runs `exec()` on model input outside every sandbox, so
# exposing it here would defeat Monty's isolation (STORY-260718-a243ee
# tracks the underlying exec hardening).
CODE_MODE_ELIGIBLE = frozenset({'web'})

# Restricted self-mod paths (mirrors the MarcelPolicy guard) — FileSystem
# refuses WRITES to these even before the policy layer. Every pattern is
# depth-agnostic (`**/` prefix): the admin FileSystem is rooted at the session
# cwd, which for the primary Telegram admin path is $HOME, so the self-mod
# files sit several directories deep (`$HOME/projects/marcel/…`). Root-anchored
# patterns would silently no-op there (STORY-260718-38235c security review).
FILESYSTEM_PROTECTED_PATTERNS = (
    '**/.git/**',
    '**/.git',
    '**/.claude/**',
    '**/.env',
    '**/.env.*',
    '**/*.pem',
    '**/*.key',
    '**/secrets*',
    '**/CLAUDE.md',
    '**/src/marcel_core/auth/**',
    '**/src/marcel_core/config.py',
)

# Paths FileSystem refuses to READ — protected_patterns only gate writes, so
# without this a rooted-at-$HOME read_file could disclose SSH keys, .env
# secrets, or another user's encrypted credential blob.
FILESYSTEM_DENIED_READ_PATTERNS = (
    '**/.env',
    '**/.env.*',
    '**/*.pem',
    '**/*.key',
    '**/.ssh/**',
    '**/secrets*',
    '**/.marcel/users/**/credentials*',
    '**/credentials.enc',
)

# Tool names contributed by the admin-only capabilities — unioned into
# admin_tool_names() so the event-bus role gate (layer 2) covers them.
SHELL_TOOL_NAMES = frozenset({'run_command', 'start_command', 'check_command', 'stop_command'})
FILESYSTEM_TOOL_NAMES = frozenset(
    {
        'read_file',
        'write_file',
        'edit_file',
        'list_directory',
        'search_files',
        'find_files',
        'create_directory',
        'file_info',
    }
)


def build_capabilities(
    *,
    role: str = 'user',
    cwd: str | None = None,
    tool_filter: set[str] | None = None,
    memory: bool = True,
    code_mode: bool = True,
    user_slug: str | None = None,
    skills: bool = True,
    eager_skill: str | None = None,
    connectors: bool = True,
) -> list[AbstractCapability[MarcelDeps]]:
    """Assemble the capability list for a Marcel agent.

    Today: the policy gate (always), step persistence (the store ignores
    runs without a Marcel conversation id — jobs, subagents, the explain
    tier), the compaction stack (clamp runaway parts first, then blank old
    tool results past the token trigger — replaces the pre-harness
    age-tier trimming), the Memory notebook (``memory=False`` for the lean
    paths — jobs until FEAT-260718-49a01a declares scoping, and the
    explain tier), the user's skills as deferred capabilities
    (``skills=False``/no ``user_slug`` for the lean paths; ``eager_skill``
    force-loads a ``/<skill>`` override), CodeMode over the eligible tool
    set (``code_mode=False`` for the lean paths), admin execution
    capabilities (Shell through bubblewrap, FileSystem rooted at the
    session cwd — attached only when the role is admin AND the
    ``tool_filter`` either is absent or names them, so constrained
    subagents and the explain tier stay lean), and instrumentation (when
    tracing is enabled).
    """
    capabilities: list[AbstractCapability[MarcelDeps]] = [
        MarcelPolicy(),
        StepPersistence(store=persistence_store(), agent_name='marcel'),
        OverflowingToolOutput(
            bands=[Band(over=settings.marcel_overflow_spill_chars, action=Spill(then=Truncate()))],
            store=PasteOverflowStore(),
        ),
        ClampOversizedMessages(max_part_tokens=settings.marcel_clamp_max_part_tokens),
        ClearToolResults(
            max_tokens=settings.marcel_compaction_max_tokens,
            keep_pairs=settings.marcel_compaction_keep_pairs,
            # `marcel` results carry conversation/skill utility output;
            # keep them out of clearing so the model retains context.
            exclude_tools=frozenset({'marcel'}),
        ),
    ]
    # Connectors are loaded once and shared: the catalog feeds both the
    # connector capabilities and the skill→connector bundling below, so a
    # skill naming a connector in `marcel-connectors` activates it as part of
    # that skill's load_capability step (FEAT-260718-230bf8).
    connector_docs = None
    if connectors and user_slug is not None:
        from marcel_core.connectors.loader import load_connectors
        from marcel_core.connectors.toolset import build_connector_capabilities

        connector_docs = load_connectors(user_slug, role)
        capabilities.extend(build_connector_capabilities(user_slug, role, docs=connector_docs))
        # Deferred connectors hide their tool schemas until the model reaches
        # for them; ToolSearch is how it reaches when no skill names them.
        if any(d.config.discovery is ConnectorDiscovery.DEFERRED for d in connector_docs):
            capabilities.append(ToolSearch())
    if skills and user_slug is not None:
        from marcel_core.skills.capability import build_skill_capabilities

        capabilities.extend(
            build_skill_capabilities(user_slug, role, eager_skill=eager_skill, connector_docs=connector_docs)
        )
    if memory:
        capabilities.append(
            Memory(
                store_resolver=lambda ctx: memory_store_for(ctx.deps.user_slug),
                # Per-user store roots make the namespace; 'memory' as the
                # agent segment lands files at users/{slug}/memory/*.md —
                # the pre-capability distilled-memory location, unchanged.
                agent_name='memory',
                max_tokens=settings.marcel_memory_inject_max_tokens,
                guidance=MEMORY_GUIDANCE,
            )
        )

    if code_mode:
        capabilities.append(CodeMode(tools=sorted(CODE_MODE_ELIGIBLE)))

    if role == 'admin':
        workspace = cwd or _PROJECT_ROOT

        def _wants(names: frozenset[str]) -> bool:
            # An absent filter grants the full admin set; an explicit empty
            # filter (the explain tier) or a non-matching one grants none.
            return tool_filter is None or bool(names & tool_filter)

        # 'bash' as a legacy filter name still grants the Shell successor so
        # existing agent-doc frontmatter keeps working. When a filter names
        # specific tools, the capability toolset is narrowed to that subset
        # so a read-only subagent (``tools: [read_file]``) never gains
        # write_file — the role-gating "keep allowlists tight" contract.
        if _wants(SHELL_TOOL_NAMES | {'bash'}):
            shell_allowed = None
            if tool_filter is not None and 'bash' not in tool_filter:
                shell_allowed = frozenset(SHELL_TOOL_NAMES & tool_filter)
            capabilities.append(
                SandboxedShell(
                    cwd=workspace,
                    # Marcel's command policy (MarcelPolicy → allow/ask/deny +
                    # approval) is the single classifier; the harness-level
                    # denylist would double-veto commands the policy already
                    # approved (e.g. an allow_once'd rm).
                    denied_commands=[],
                    allowed_tools=shell_allowed,
                )
            )
        if _wants(FILESYSTEM_TOOL_NAMES):
            fs_allowed = None if tool_filter is None else frozenset(FILESYSTEM_TOOL_NAMES & tool_filter)
            capabilities.append(
                FilteredFileSystem(
                    root_dir=workspace,
                    protected_patterns=list(FILESYSTEM_PROTECTED_PATTERNS),
                    denied_patterns=list(FILESYSTEM_DENIED_READ_PATTERNS),
                    allowed_tools=fs_allowed,
                )
            )

    instrumentation = get_instrumentation_settings()
    if instrumentation is not None:
        capabilities.append(Instrumentation(instrumentation))
    return capabilities
