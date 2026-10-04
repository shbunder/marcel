#!/usr/bin/env python3
"""PreToolUse guard. Blocks edits to Marcel's restricted paths unless an unlock flag exists.

Restricted paths, relative to the repo root:
- CLAUDE.md (the root one only; brain/CLAUDE.md is Marcel's persona and edited by stories)
- contracts/**: the interfaces every lane builds against; only the lead changes them
- .env* in any folder (deploy/.env.local holds the secrets)

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

# Anchored to the repo, not the working directory, so both work from any subfolder.
ROOT = Path(__file__).resolve().parents[2]
UNLOCK_FLAG = ROOT / '.claude' / '.unlock-safety'
GUARDED_TOOLS = {'Edit', 'Write', 'NotebookEdit', 'MultiEdit'}

RESTRICTED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'^CLAUDE\.md$'), 'project instructions (CLAUDE.md)'),
    (re.compile(r'^contracts/'), 'a contract: implementers file project/contract-requests/'),
    (re.compile(r'(^|/)\.env(\.|$)'), 'environment file'),
]


def repo_relative(file_path: str) -> set[str]:
    """The path as written and resolved, each relative to the repo root (POSIX separators)."""
    found: set[str] = set()
    for raw in {file_path, os.path.realpath(file_path)}:
        absolute = Path(raw) if os.path.isabs(raw) else Path.cwd() / raw
        try:
            found.add(absolute.resolve().relative_to(ROOT).as_posix())
        except ValueError:
            found.add(Path(raw).as_posix())  # outside the repo: only the .env rule can match
    return found


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

    candidates = repo_relative(file_path)

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
