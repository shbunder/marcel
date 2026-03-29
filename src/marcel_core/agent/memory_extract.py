"""Background memory extraction — identifies new facts from a conversation turn.

Runs as a fire-and-forget asyncio task after each agent response. Never raises;
logs failures instead so a bad extraction never breaks the conversation.
"""

import logging
import re

import claude_agent_sdk
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock

from marcel_core import storage

log = logging.getLogger(__name__)

_EXTRACT_PROMPT = """\
Review this conversation exchange and identify any new facts about the user \
that should be saved for future reference.

User said:
{user_text}

Assistant responded:
{assistant_text}

If there are new facts worth remembering, output them in this exact format \
(repeat the block for multiple topics):

TOPIC: <topic name, e.g. calendar, family, preferences, work>
CONTENT: <the facts to remember, as bullet points or short prose>

If there are no new facts worth saving, output exactly: NO_NEW_FACTS
"""


async def extract_and_save_memories(
    user_slug: str,
    user_text: str,
    assistant_text: str,
    conversation_id: str,
) -> None:
    """Extract facts from a turn and persist them to the user's memory files.

    Designed to run as a background asyncio task. All exceptions are caught
    and logged so a failed extraction never surfaces to the user.

    Args:
        user_slug: The user's slug.
        user_text: The user's message for this turn.
        assistant_text: Marcel's response for this turn.
        conversation_id: Filename stem of the conversation (for logging only).
    """
    prompt = _EXTRACT_PROMPT.format(user_text=user_text, assistant_text=assistant_text)

    options = ClaudeAgentOptions(
        system_prompt='You are a fact extractor. Follow the output format exactly.',
        tools=[],
        permission_mode='bypassPermissions',
        max_turns=1,
    )

    try:
        response_parts: list[str] = []
        async for msg in claude_agent_sdk.query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        response_parts.append(block.text)

        response = ''.join(response_parts).strip()

        if not response or response == 'NO_NEW_FACTS':
            return

        _parse_and_save(user_slug, response)

    except Exception:
        log.exception('Memory extraction failed for user=%s conversation=%s', user_slug, conversation_id)


def _parse_and_save(user_slug: str, response: str) -> None:
    """Parse TOPIC/CONTENT blocks from the extraction response and save each."""
    # Split on TOPIC: boundaries (first block may or may not start with TOPIC:)
    raw_blocks = re.split(r'(?:^|\n)TOPIC:', response)

    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue

        match = re.match(r'([^\n]+)\nCONTENT:\s*(.*)', block, re.DOTALL)
        if not match:
            continue

        topic = match.group(1).strip().lower().replace(' ', '_')
        content = match.group(2).strip()

        if not topic or not content:
            continue

        existing = storage.load_memory_file(user_slug, topic)
        if existing.strip():
            updated = existing.rstrip() + '\n' + content
        else:
            updated = f'# {topic.replace("_", " ").title()}\n\n{content}'

        storage.save_memory_file(user_slug, topic, updated)
        storage.update_memory_index(user_slug, f'{topic}.md', f'{topic.replace("_", " ")} facts')
        log.debug('Saved memory topic=%r for user=%s', topic, user_slug)
