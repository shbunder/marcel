"""S-15.1's live check: the brain lists Harry's tools. Never part of the gate.

Run it on the NUC, logged in to the Max plan, with Harry running and `MARCEL_HARRY_TOKEN` set:

    make -C plugins/marcel/tools test-live

It starts one short `claude -p` turn in the brain workspace (`MARCEL_BRAIN_CWD`, default
`~/marcel/brain`, where the plugin is installed) and reads the session's first event, which lists
its MCP servers and tools. Without the real claude or the token, it is skipped.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.live
PREFIX = 'mcp__plugin_marcel_harry__'


def runner_env() -> dict[str, str]:
    """The environment as the runner gives it: no `CLAUDE*` or `ANTHROPIC_*` variable."""
    return {k: v for k, v in os.environ.items() if not k.startswith(('CLAUDE', 'ANTHROPIC_'))}


def test_the_brain_lists_harrys_tools() -> None:
    claude = shutil.which('claude')
    if claude is None:
        pytest.skip('needs the real claude CLI (run on the NUC)')
    if not os.environ.get('MARCEL_HARRY_TOKEN'):
        pytest.skip('needs MARCEL_HARRY_TOKEN, as the runner has it')
    cwd = Path(os.environ.get('MARCEL_BRAIN_CWD') or Path.home() / 'marcel' / 'brain')
    if not cwd.is_dir():
        pytest.skip(f'no brain workspace at {cwd}')
    out = subprocess.run(
        [claude, '-p', 'Reply with the word ok.', '--model', 'haiku', '--max-turns', '1',
         '--output-format', 'stream-json', '--verbose'],
        cwd=cwd, env=runner_env(), capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    init = next(
        (
            e
            for e in map(json.loads, filter(None, out.stdout.splitlines()))
            if e.get('subtype') == 'init'
        ),
        None,
    )
    assert init is not None, f'claude printed no init event: {out.stderr[-500:]}'
    harry = [s for s in init.get('mcp_servers', []) if s.get('name', '').endswith('harry')]
    assert harry and harry[0].get('status') == 'connected', f'Harry is not connected: {harry}'
    tools = [t for t in init.get('tools', []) if t.startswith(PREFIX)]
    assert tools, f'no {PREFIX}* tool in the brain: {init.get("tools")}'
