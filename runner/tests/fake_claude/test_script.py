"""`scripts/fake-claude` (run by `make fake-claude`): the fake first on PATH, a scratch HOME."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from fake_claude import EXECUTABLE

SCRIPT = Path(__file__).resolve().parents[3] / 'scripts' / 'fake-claude'


def run(*args: str, home: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, 'FAKE_CLAUDE_HOME': str(home)}
    return subprocess.run([str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=30)


def test_the_fake_is_first_on_path_in_a_scratch_home(tmp_path: Path) -> None:
    home = tmp_path / 'scratch'
    probe = 'command -v claude; echo "$HOME"; claude --bg -- go; claude agents --json --all'

    done = run('worker-fails', 'sh', '-c', probe, home=home)

    assert done.returncode == 0, done.stderr
    which, seen_home, spawned, listed = done.stdout.splitlines()
    assert (which, seen_home) == (str(EXECUTABLE), str(home))
    assert spawned.startswith('backgrounded · ')
    [row] = json.loads(listed)
    session = json.loads((home / '.claude/fake-claude/sessions' / f'{row["id"]}.json').read_text())
    assert Path(session['scenario']).stem == 'worker-fails'
    subprocess.run([str(EXECUTABLE), 'stop', row['id']], env={**os.environ, 'HOME': str(home)})


def test_an_unknown_scenario_lists_the_real_ones(tmp_path: Path) -> None:
    done = run('no-such-scenario', 'true', home=tmp_path)

    assert done.returncode == 1
    assert "no scenario 'no-such-scenario'" in done.stderr
    assert 'worker-happy-path' in done.stderr
