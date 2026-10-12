"""The fake `claude` shim and a small helper for tests that drive it. See README.md.

Runner tests use it like this::

    fake = FakeClaude(tmp_path)
    fake.use('worker-needs-approval')
    # run the code under test with fake.env() (HOME and PATH) or fake.claude (absolute path)
    fake.wait_for(short_id, 'blocked')
    fake.answer(short_id, 'allow')

Standard library only: the `claude` executable imports this package outside any venv.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from fake_claude.shim import EXECUTABLE, FIXTURES, SCENARIOS, Store, answer, project_dir

__all__ = ['EXECUTABLE', 'FIXTURES', 'SCENARIOS', 'FakeClaude']


class FakeClaude:
    """A scratch HOME with the fake `claude` first on PATH."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.claude = EXECUTABLE
        self.store = Store(home)
        self.store.claim()

    def env(self, **extra: str) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith('FAKE_CLAUDE')}
        env['HOME'] = str(self.home)
        env['PATH'] = f'{EXECUTABLE.parent}{os.pathsep}{env.get("PATH", "")}'
        env.update(extra)
        return env

    def use(self, *scenarios: str) -> None:
        """The scenarios the next spawns play, in order; the last one repeats."""
        (self.store.root / 'scenario').write_text('\n'.join(scenarios) + '\n', encoding='utf-8')

    def run(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.claude), *args],
            cwd=cwd or self.home,
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=30,
        )

    def agents(self) -> list[dict[str, Any]]:
        done = self.run('agents', '--json', '--all')
        if done.returncode != 0:
            raise AssertionError(done.stderr)
        return json.loads(done.stdout)

    def agent(self, short: str) -> dict[str, Any] | None:
        return next((a for a in self.agents() if a['id'] == short), None)

    def wait_for(self, short: str, state: str, timeout: float = 10) -> dict[str, Any]:
        """Poll `agents --json --all` until the session reaches `state`; return its row."""
        deadline = time.monotonic() + timeout
        seen: list[str] = []
        while time.monotonic() < deadline:
            current = self.agent(short)
            if current is not None:
                if not seen or seen[-1] != current['state']:
                    seen.append(current['state'])
                if current['state'] == state:
                    return current
            time.sleep(0.05)
        raise AssertionError(f'{short} never reached {state!r}; saw {seen}')

    def wait_idle(self, short: str, timeout: float = 10) -> None:
        """Wait until the session's player has finished its steps (or is held at a gate)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.store.load(short).get('playing'):
                return
            time.sleep(0.05)
        raise AssertionError(f'{short} is still playing after {timeout}s')

    def answer(self, short: str, decision: str) -> None:
        answer(self.store, short, decision)

    def transcript(self, session_id: str, cwd: Path) -> Path:
        return self.home / '.claude' / 'projects' / project_dir(str(cwd)) / f'{session_id}.jsonl'

    def job(self, short: str) -> dict[str, Any]:
        path = self.home / '.claude' / 'jobs' / short / 'state.json'
        return json.loads(path.read_text('utf-8'))

    def stop_all(self) -> None:
        """Stop every player still running (test teardown)."""
        for s in self.store.sessions():
            if s.get('playing'):
                self.run('stop', s['short'])
