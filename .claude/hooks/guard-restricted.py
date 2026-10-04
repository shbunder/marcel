#!/usr/bin/env python3
"""PreToolUse guard. Blocks edits to Marcel's restricted paths unless an unlock flag exists.

Restricted paths:
- CLAUDE.md (any directory): project instructions
- contracts/**: the interfaces every lane builds against; only the lead changes them
- .env* and deploy/.env*: environment and secret files

Unlock: create .claude/.unlock-safety, make the edit, commit, then delete the flag.

Stdin: JSON hook payload (tool_name, tool_input, ...).
Exit 0 allows the tool call; exit 2 blocks it and shows the stderr message to Claude.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Anchored to the repo, not the working directory, so the flag works from any subfolder.
UNLOCK_FLAG = Path(__file__).resolve().parent.parent / '.unlock-safety'
GUARDED_TOOLS = {'Edit', 'Write', 'NotebookEdit', 'MultiEdit'}

RESTRICTED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'(^|/)CLAUDE\.md$'), 'project instructions (CLAUDE.md)'),
    (re.compile(r'(^|/)contracts/'), 'a contract: implementers file project/contract-requests/'),
    (re.compile(r'(^|/)\.env(\.|$)'), 'environment file'),
]


def main() -> int:
    # Fail open on malformed input so a broken hook never blocks all editing.
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, ValueError):
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

    if UNLOCK_FLAG.exists():
        return 0

    for pattern, label in RESTRICTED:
        if any(pattern.search(c) for c in candidates):
            sys.stderr.write(
                f'\n🛑 Blocked edit to restricted path: {file_path}\n'
                f'   Reason: {label}.\n'
                f'   If this edit is yours to make, unlock, edit, commit, and lock again:\n'
                f'     touch {UNLOCK_FLAG}\n'
                f'     … edit and commit …\n'
                f'     rm {UNLOCK_FLAG}\n'
            )
            return 2

    return 0


if __name__ == '__main__':
    sys.exit(main())
