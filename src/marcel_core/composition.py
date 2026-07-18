"""The composition root — the one place capability lists are assembled.

Per ADR-260718-0cf8e8, an agent is a composition of capabilities and this
module is the only place that composition is built. Capability packages
under :mod:`marcel_core.capabilities` never import each other; they meet
here. The list grows as the FEAT-260718-* roadmap features land their
capabilities.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_ai.capabilities import AbstractCapability, Instrumentation
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
from marcel_core.harness.context import MarcelDeps
from marcel_core.tracing import get_instrumentation_settings

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])

# Tools the model may orchestrate from run_code (CodeMode/Monty — the
# code_exec successor, ADR-260718-d511f7). Conservative opt-in: read-only-ish,
# never approval-gated (an approval inside generated code surfaces as a raw
# retry). `toolkit` stays a direct tool until the Zoo v2 skills rewrite.
CODE_MODE_ELIGIBLE = frozenset({'web', 'generate_chart'})

# Restricted self-mod paths (mirrors the MarcelPolicy guard) plus the harness
# defaults — FileSystem refuses writes to these even before the policy layer.
FILESYSTEM_PROTECTED_PATTERNS = (
    '.git/*',
    '.env',
    '.env.*',
    '*.pem',
    '*.key',
    '**/secrets*',
    'CLAUDE.md',
    '.claude/**',
    'src/marcel_core/auth/**',
    'src/marcel_core/config.py',
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
) -> list[AbstractCapability[MarcelDeps]]:
    """Assemble the capability list for a Marcel agent.

    Today: the policy gate (always), step persistence (the store ignores
    runs without a Marcel conversation id — jobs, subagents, the explain
    tier), the compaction stack (clamp runaway parts first, then blank old
    tool results past the token trigger — replaces the pre-harness
    age-tier trimming), the Memory notebook (``memory=False`` for the lean
    paths — jobs until FEAT-260718-49a01a declares scoping, and the
    explain tier), CodeMode over the eligible tool set, admin execution
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
            # `marcel` results carry read_skill docs that must stay visible
            # across turns (the read_skills priming depends on it).
            exclude_tools=frozenset({'marcel'}),
        ),
    ]
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
                    allowed_tools=fs_allowed,
                )
            )

    instrumentation = get_instrumentation_settings()
    if instrumentation is not None:
        capabilities.append(Instrumentation(instrumentation))
    return capabilities
