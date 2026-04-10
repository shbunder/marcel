"""Background memory extraction — uses a pydantic-ai agent to update memory files.

Runs as a fire-and-forget asyncio task after each agent response.  A cheap
model (Haiku) reviews the conversation exchange and returns a JSON array of
memory operations (create/update). The caller applies them to disk.

Inspired by Claude Code's ``extractMemories.ts``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic_ai import Agent

from marcel_core.storage.memory import (
    format_memory_manifest,
    scan_memory_headers,
)

log = logging.getLogger(__name__)

_EXTRACTOR_MODEL = 'claude-haiku-4-5-20251001'

_EXTRACT_SYSTEM_PROMPT = """\
You are a memory extraction agent for Marcel, a personal assistant. Your job is \
to review a conversation exchange and decide which facts are worth remembering.

## Rules

1. Only save facts that will be useful in future conversations — skip ephemeral \
details like "the weather today" or transient task status.
2. Check existing memory files before writing — update an existing file if the \
new fact belongs there, rather than creating a duplicate.
3. Every memory file MUST have YAML frontmatter with at least `name` and `type`.
4. For `schedule` type memories, always set `expires` to the event date so they \
can be auto-pruned after the date passes.
5. Keep memory files focused — one topic per file. Use descriptive filenames \
like `dentist_april.md`, `coffee_preference.md`, `sister_emily.md`.
6. If there are NO new facts worth saving, return an empty array: []

## Output format

Return a JSON array of operations. Each operation is an object with:
- "action": "create" or "update"
- "filename": e.g. "coffee_preference.md"
- "content": full file content including YAML frontmatter

Example frontmatter:
```
---
name: coffee_preference
description: How the user likes their coffee
type: preference
---

Prefers oat milk flat white, no sugar.
```

Valid types: schedule, preference, person, reference, household

Return ONLY the JSON array, no other text."""


async def extract_and_save_memories(
    user_slug: str,
    user_text: str,
    assistant_text: str,
    conversation_id: str,
) -> None:
    """Extract facts from a turn and persist them as memory files.

    Designed to run as a background asyncio task.  All exceptions are caught
    and logged so a failed extraction never surfaces to the user.

    Uses a pydantic-ai Agent (Haiku) to decide which facts to save, then
    writes the files directly to the user's memory directory.

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

    prompt = (
        f'Review this conversation exchange and save any new facts to memory files.\n\n'
        f'Existing memories:\n{manifest}\n\n'
        f'User said:\n{user_text}\n\n'
        f'Assistant responded:\n{assistant_text}'
    )

    extractor: Agent[None, str] = Agent(
        _EXTRACTOR_MODEL,
        system_prompt=_EXTRACT_SYSTEM_PROMPT,
        retries=2,
    )

    try:
        result = await extractor.run(prompt)
        response = result.output.strip()
        operations = _parse_operations(response)

        for op in operations:
            filename = op.get('filename', '')
            content = op.get('content', '')
            if not filename or not content:
                continue
            # Sanitize filename — only allow safe characters
            safe_name = Path(filename).name
            if not safe_name.endswith('.md'):
                safe_name += '.md'
            filepath = mem_dir / safe_name
            filepath.write_text(content, encoding='utf-8')
            log.debug(
                'memory_extract: %s memory %s for user=%s',
                op.get('action', 'wrote'),
                safe_name,
                user_slug,
            )

        if operations:
            log.info(
                'memory_extract: saved %d memories for user=%s conversation=%s',
                len(operations),
                user_slug,
                conversation_id,
            )

    except Exception:
        log.exception(
            'memory_extract: extraction failed for user=%s conversation=%s',
            user_slug,
            conversation_id,
        )


def _parse_operations(response: str) -> list[dict]:
    """Parse a JSON array of memory operations from the extractor response."""
    response = response.strip()
    if response.startswith('```'):
        lines = response.split('\n')
        response = '\n'.join(lines[1:-1] if lines[-1].strip() == '```' else lines[1:])
    try:
        result = json.loads(response)
        if isinstance(result, list):
            return [op for op in result if isinstance(op, dict)]
    except json.JSONDecodeError:
        log.warning('memory_extract: extractor returned non-JSON: %s', response[:200])
    return []
