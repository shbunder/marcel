"""Tests for the edit guard. Run: python3 -m unittest discover .claude/hooks"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "guard-restricted.py"


def run(path, cwd):
    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": path}})
    return subprocess.run([sys.executable, str(HOOK)], input=payload, text=True, capture_output=True, cwd=cwd)


class Guard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self.tmp.name)
        (self.cwd / ".claude").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_blocks_contracts_claude_md_and_env_files(self):
        for path in ("contracts/app-api.yaml", "CLAUDE.md", "hub/CLAUDE.md", "deploy/.env.local", ".env"):
            r = run(path, self.cwd)
            self.assertEqual(r.returncode, 2, path)
            self.assertIn("unlock-safety", r.stderr)

    def test_allows_ordinary_files(self):
        for path in ("hub/src/marcel_hub/app.py", "plan/features/F04-hub.md", "docs/contracts.md"):
            self.assertEqual(run(path, self.cwd).returncode, 0, path)

    def test_unlock_flag_allows_restricted_edit(self):
        (self.cwd / ".claude" / ".unlock-safety").touch()
        self.assertEqual(run("contracts/app-api.yaml", self.cwd).returncode, 0)


if __name__ == "__main__":
    unittest.main()
