---
description: Hand this Claude Code session to Marcel, so it shows in the Marcel app and Marcel follows it.
argument-hint: "[short title]"
allowed-tools: Bash(python3 *scripts/adopt.py*)
---

Hand this session over to Marcel.

1. Choose a title. If this is not empty, use it as it is: "$ARGUMENTS". Otherwise write one short
   line, at most 8 words, that says what this session is working on.
2. Run this one command, with the title between single quotes (write any `'` in it as `’`):

   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/adopt.py" --session-id "${CLAUDE_SESSION_ID}" --title '<title>'
   ```

3. Tell me what it printed, in one line. If it starts with "Not adopted", do not retry or work
   around it: its message says what I need to do.
