"""The brain-routing eval, against a real `claude` on the NUC (S-05.5). Never part of the gate.

Run it on the NUC, logged in to the Max plan:

    make -C plugins/marcel/tools test-live

Each case starts `claude -p` (Opus) with the orchestrate skill and a fake brain-tools server that
answers from canned results, so no task is started and the hub is never called. The tool calls
of every case are written to `.eval/<case>.jsonl` next to this file; record the result in
`project/lessons/brain-skills-eval.md`.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from skills_eval import (
    check_case,
    claude_command,
    claude_env,
    load_cases,
    mcp_config,
    read_log,
)

pytestmark = pytest.mark.live
OUT = Path(__file__).resolve().parent / '.eval'


@pytest.mark.parametrize('case', load_cases(), ids=lambda c: c['id'])
def test_request_leads_to_the_expected_tool_calls(case: dict[str, Any], tmp_path: Path) -> None:
    claude = shutil.which('claude')
    if claude is None:
        pytest.skip('needs the real claude CLI (run on the NUC)')
    OUT.mkdir(exist_ok=True)
    log = OUT / f'{case["id"]}.jsonl'
    log.unlink(missing_ok=True)
    config = tmp_path / 'mcp.json'
    config.write_text(json.dumps(mcp_config(case['id'], log)))
    run = subprocess.run(
        claude_command(case, config, claude),
        cwd=tmp_path, env=claude_env(os.environ), capture_output=True, text=True, timeout=600,
        check=False,
    )  # fmt: skip
    calls = read_log(log)
    (OUT / f'{case["id"]}.out.json').write_text(run.stdout)
    assert run.returncode == 0, run.stderr[-2000:]
    assert check_case(case, calls) == []
