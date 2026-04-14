"""Subagent document loader — discovers agent markdown files from the data root.

Agents live at ``<data_root>/agents/`` (``~/.marcel/agents/`` or
``$MARCEL_DATA_DIR/agents/`` in Docker). Each ``<name>.md`` is a markdown
file with YAML frontmatter:

.. code-block:: markdown

   ---
   name: explore
   description: Fast codebase explorer
   model: inherit                    # or e.g. anthropic:claude-haiku-4-5-20251001
   tools: [web, read_file, marcel]   # allowlist (optional)
   disallowed_tools: []              # denylist applied after allowlist
   max_requests: 20                  # pydantic-ai request limit
   timeout_seconds: 300
   ---

   # System prompt body goes here.

Frontmatter fields map to :class:`AgentDoc`. The body (after the second
``---``) becomes the agent's system prompt verbatim.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from pathlib import Path

from marcel_core.skills.loader import _parse_frontmatter

log = logging.getLogger(__name__)


class AgentNotFoundError(LookupError):
    """Raised by :func:`load_agent` when the requested agent name is unknown."""


def _agents_dir() -> Path:
    """Return the agents directory under the data root."""
    from marcel_core.config import settings

    return settings.data_dir / 'agents'


@dataclass
class AgentDoc:
    """A loaded subagent definition ready to be invoked by ``delegate``."""

    name: str
    description: str
    system_prompt: str
    source: str
    model: str | None = None
    """Fully-qualified pydantic-ai model string, or ``None`` to inherit from the parent.

    Supports the same values as the main agent's ``model`` parameter — any
    ``provider:model`` string plus the ``local:<tag>`` prefix.
    """

    tools: list[str] | None = None
    """Tool name allowlist. ``None`` means "use the role default minus the recursion guard".

    Names must match the registered tool function names in
    :func:`marcel_core.harness.agent.create_marcel_agent` (e.g. ``'bash'``,
    ``'read_file'``, ``'web'``, ``'integration'``).
    """

    disallowed_tools: list[str] = field(default_factory=list)
    """Tool names to strip from the resolved pool *after* the allowlist is applied."""

    max_requests: int | None = None
    """pydantic-ai ``UsageLimits.request_limit`` — caps total model calls per run."""

    timeout_seconds: int = 300
    """Wall-clock budget for a single delegated run."""


def _load_agent_file(path: Path, source: str) -> AgentDoc | None:
    """Parse a single ``<name>.md`` into an :class:`AgentDoc`.

    Returns ``None`` when the file is unreadable or missing the required
    ``name`` field — we log a warning and skip rather than raise so a
    single broken file can never take down the whole registry.
    """
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        log.warning('agents: could not read %s', path, exc_info=True)
        return None

    fm, body = _parse_frontmatter(text)
    name = fm.get('name') or path.stem
    if not name:
        log.warning('agents: %s has no name — skipping', path)
        return None

    model_raw = fm.get('model')
    # "inherit" is an explicit way to say "use parent's model" — map to None.
    # Single-word tier names (standard, backup, fallback, power) are rewritten
    # to ``tier:<name>`` sentinels which the delegate tool resolves against
    # settings at call time. See ISSUE-076.
    if model_raw in (None, '', 'inherit'):
        model: str | None = None
    elif model_raw in ('standard', 'backup', 'fallback', 'power'):
        model = f'tier:{model_raw}'
    else:
        model = str(model_raw)

    tools_raw = fm.get('tools')
    tools = list(tools_raw) if isinstance(tools_raw, list) else None

    disallowed_raw = fm.get('disallowed_tools') or fm.get('disallowedTools') or []
    disallowed = list(disallowed_raw) if isinstance(disallowed_raw, list) else []

    max_requests_raw = fm.get('max_requests') or fm.get('maxRequests') or fm.get('maxTurns')
    max_requests = int(max_requests_raw) if max_requests_raw is not None else None

    timeout_raw = fm.get('timeout_seconds') or fm.get('timeoutSeconds')
    timeout = int(timeout_raw) if timeout_raw is not None else 300

    return AgentDoc(
        name=str(name),
        description=str(fm.get('description', '')),
        system_prompt=body.strip(),
        source=source,
        model=model,
        tools=tools,
        disallowed_tools=disallowed,
        max_requests=max_requests,
        timeout_seconds=timeout,
    )


def load_agents() -> list[AgentDoc]:
    """Discover and load all agents from the data-root agents directory.

    Returns agents sorted by name. Returns an empty list if the directory
    does not exist (fresh install before seeding, tests without a data
    root, etc.).
    """
    agents: dict[str, AgentDoc] = {}

    agents_path = _agents_dir()
    if not agents_path.is_dir():
        return []

    for entry in sorted(agents_path.iterdir()):
        if not entry.is_file() or not entry.name.endswith('.md'):
            continue
        if entry.name.startswith(('_', '.')):
            continue
        doc = _load_agent_file(entry, source='data')
        if doc:
            agents[doc.name] = doc

    return sorted(agents.values(), key=lambda a: a.name)


def load_agent(name: str) -> AgentDoc:
    """Load a single agent by name.

    Raises:
        AgentNotFoundError: if no agent with the given name exists in the
            data root.
    """
    for agent in load_agents():
        if agent.name == name:
            return agent
    raise AgentNotFoundError(f'No subagent named {name!r}. Available: {[a.name for a in load_agents()]}')


def format_agent_index(agents: list[AgentDoc]) -> str:
    """Format a compact one-line-per-agent index for the parent system prompt.

    Only name + description — the full system prompt is not exposed to the
    parent. This is what the parent sees when deciding *which* subagent to
    delegate to.
    """
    if not agents:
        return ''
    return '\n'.join(f'- **{a.name}** — {a.description}' for a in agents)


def _asdict(doc: AgentDoc) -> dict:
    """Expose AgentDoc as a plain dict for structured logging/tests."""
    return dataclasses.asdict(doc)
