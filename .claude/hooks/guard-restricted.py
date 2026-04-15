#!/usr/bin/env python3
"""PreToolUse guard. Blocks edits to Marcel's restricted paths unless an unlock flag exists.

Restricted paths:
- CLAUDE.md (any directory) — project instructions
- src/marcel_core/auth/** — auth module
- src/marcel_core/config.py — core config
- .env* — environment files

Unlock: create .claude/.unlock-safety, make the edit, commit, delete the flag.

Stdin: JSON hook payload (tool_name, tool_input, ...).
Exit 0 allows the tool call; exit 2 blocks it with the stderr message shown to Claude.
"""

from __future__ import annotations

import json
import os
import re
import sys

UNLOCK_FLAG = '.claude/.unlock-safety'
GUARDED_TOOLS = {'Edit', 'Write', 'NotebookEdit', 'MultiEdit'}

RESTRICTED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'(^|/)CLAUDE\.md$'), 'project instructions (CLAUDE.md)'),
    (re.compile(r'(^|/)src/marcel_core/auth/'), 'auth module'),
    (re.compile(r'(^|/)src/marcel_core/config\.py$'), 'core config'),
    (re.compile(r'(^|/)\.env(\.|$)'), 'environment file'),
]


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    if data.get('tool_name', '') not in GUARDED_TOOLS:
        return 0

    file_path = (data.get('tool_input') or {}).get('file_path') or ''
    if not file_path:
        return 0

    candidates = {file_path}
    try:
        candidates.add(os.path.realpath(file_path))
    except OSError:
        pass

    if os.path.exists(UNLOCK_FLAG):
        return 0

    for pattern, label in RESTRICTED:
        if any(pattern.search(c) for c in candidates):
            sys.stderr.write(
                f'\n🛑 Blocked edit to restricted path: {file_path}\n'
                f'   Reason: {label} — Marcel self-modification safety rule.\n'
                f'   To unlock, run:\n'
                f'     touch {UNLOCK_FLAG}\n'
                f'   then retry the edit, commit, and:\n'
                f'     rm {UNLOCK_FLAG}\n'
                f'   See docs/claude-code-setup.md for the full workflow.\n'
            )
            return 2

    return 0


if __name__ == '__main__':
    sys.exit(main())
