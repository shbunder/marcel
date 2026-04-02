"""Background memory extraction — uses a tool-equipped agent to update memory files.

Runs as a fire-and-forget asyncio task after each agent response.  Instead of
regex-parsing structured text, we give a cheap model (Haiku) the ``claude_code``
tools preset so it can read existing memory files, write new ones, and edit
existing ones — all with proper frontmatter.

Inspired by Claude Code's ``extractMemories.ts``.
"""

from __future__ import annotations

import logging

import claude_agent_sdk
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage

from marcel_core.storage.memory import (
    format_memory_manifest,
    scan_memory_headers,
)

log = logging.getLogger(__name__)

_EXTRACTOR_MODEL = 'claude-haiku-4-5-20251001'

_EXTRACT_SYSTEM_PROMPT = """\
You are a memory extraction agent for Marcel, a personal assistant. Your job is \
to review a conversation exchange and save any new facts worth remembering to \
memory files in the current directory.

## Rules

1. Only save facts that will be useful in future conversations — skip ephemeral \
details like "the weather today" or transient task status.
2. Check existing memory files before writing — update an existing file if the \
new fact belongs there, rather than creating a duplicate.
3. Every memory file MUST have YAML frontmatter with at least `name` and `type`:

```
---
name: <short_snake_case_name>
description: <one-line description of what this memory contains>
type: <schedule|preference|person|reference|household>
expires: <YYYY-MM-DD, only for schedule type — set to the event date>
---

<memory content>
```

4. For `schedule` type memories, always set `expires` to the event date so they \
can be auto-pruned after the date passes.
5. Keep memory files focused — one topic per file. Use descriptive filenames \
like `dentist_april.md`, `coffee_preference.md`, `sister_emily.md`.
6. If there are NO new facts worth saving, do nothing — do not create empty files.

## Current directory

You are working in the user's memory directory. All file operations are relative \
to this directory.

## Existing memories

{manifest}
"""


async def extract_and_save_memories(
    user_slug: str,
    user_text: str,
    assistant_text: str,
    conversation_id: str,
) -> None:
    """Extract facts from a turn and persist them using a tool-equipped agent.

    Designed to run as a background asyncio task.  All exceptions are caught
    and logged so a failed extraction never surfaces to the user.

    The agent receives ``claude_code`` tools (Read, Write, Edit, Glob, Grep)
    with CWD set to the user's memory directory, so it can directly read and
    write memory files with proper frontmatter.

    Args:
        user_slug: The user's slug.
        user_text: The user's message for this turn.
        assistant_text: Marcel's response for this turn.
        conversation_id: Filename stem of the conversation (for logging only).
    """
    from marcel_core.storage.memory import _memory_dir

    mem_dir = _memory_dir(user_slug)
    mem_dir.mkdir(parents=True, exist_ok=True)

    # Build manifest of existing memories so the agent can avoid duplicates.
    headers = scan_memory_headers(user_slug)
    if headers:
        manifest = format_memory_manifest(headers)
    else:
        manifest = '(no existing memories)'

    system_prompt = _EXTRACT_SYSTEM_PROMPT.format(manifest=manifest)

    prompt = (
        f'Review this conversation exchange and save any new facts to memory files.\n\n'
        f'User said:\n{user_text}\n\n'
        f'Assistant responded:\n{assistant_text}'
    )

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        tools={'type': 'preset', 'preset': 'claude_code'},
        permission_mode='bypassPermissions',
        max_turns=3,
        model=_EXTRACTOR_MODEL,
        cwd=str(mem_dir),
    )

    try:
        async for msg in claude_agent_sdk.query(prompt=prompt, options=options):
            if isinstance(msg, ResultMessage):
                if msg.is_error:
                    log.warning(
                        'Memory extraction agent reported error for user=%s conversation=%s',
                        user_slug,
                        conversation_id,
                    )
                else:
                    log.debug(
                        'Memory extraction complete for user=%s conversation=%s (turns=%s, cost=$%s)',
                        user_slug,
                        conversation_id,
                        msg.num_turns,
                        msg.total_cost_usd,
                    )
    except Exception:
        log.exception(
            'Memory extraction failed for user=%s conversation=%s',
            user_slug,
            conversation_id,
        )
