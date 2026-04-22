#!/usr/bin/env python3
"""UserPromptSubmit hook: remind the agent to use `issue-task` for WIP issue mutations.

When a WIP issue file exists at `project/issues/wip/ISSUE-*.md`, emit a
short context message telling the agent to use the `.claude/scripts/issue-task`
CLI for checkbox/status/log changes — and to never `Write` a full rewrite.
Silent when no WIP file exists.

Modelled on Claude Code's plan-mode existence-branched reminder
(see ~/repos/clawcode/utils/messages.ts:3328-3329 — when a plan file exists,
the harness instructs the agent to make incremental edits instead of writes).

Stdin: JSON hook payload (ignored — we just read context from the filesystem).
Stdout: optional reminder text, included as turn context.
Exit 0 always — never block the prompt over a reminder.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path | None:
    try:
        out = subprocess.check_output(
            ['git', 'rev-parse', '--show-toplevel'],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=1,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return Path(out) if out else None


def main() -> int:
    # Drain stdin (the hook payload) defensively — never crash the prompt.
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, ValueError):
        pass

    root = _repo_root()
    if root is None:
        return 0

    wip = sorted((root / 'project' / 'issues' / 'wip').glob('ISSUE-*.md'))
    if len(wip) != 1:
        # Zero: no active issue — silent.
        # Multiple: ambiguous; the CLI itself will error loudly when invoked.
        return 0

    rel = wip[0].relative_to(root)
    sys.stdout.write(
        f'[issue-task] WIP issue: {rel}. '
        f'Use `.claude/scripts/issue-task` (see `--help`) for checkbox / status / log changes — '
        f'cheaper than Edit and dramatically cheaper than Write. '
        f'Reserve Edit for free-form prose; never Write a full rewrite of the issue file.\n'
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
