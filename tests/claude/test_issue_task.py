"""Tests for .claude/scripts/issue-task — structured issue-file updater."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / '.claude' / 'scripts' / 'issue-task'


def _load_module():
    """Load the extensionless `issue-task` script as a module for direct calls."""
    loader = SourceFileLoader('issue_task', str(_SCRIPT))
    spec = importlib.util.spec_from_loader('issue_task', loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


issue_task = _load_module()


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """An isolated repo skeleton with a single WIP issue file in place.

    Scrubs GIT_* env vars (per ISSUE-83ee76) and points the script's
    cwd at tmp_path so `git rev-parse --show-toplevel` returns it.
    """
    for var in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
        monkeypatch.delenv(var, raising=False)
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    subprocess.run(['git', 'config', 'user.email', 't@e.com'], cwd=tmp_path, check=True)
    subprocess.run(['git', 'config', 'user.name', 'T'], cwd=tmp_path, check=True)
    (tmp_path / 'project' / 'issues' / 'wip').mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_issue(repo: Path, body: str) -> Path:
    path = repo / 'project' / 'issues' / 'wip' / 'ISSUE-260422-aaaaaa-fake.md'
    path.write_text(body, encoding='utf-8')
    return path


_BASE = """# ISSUE-aaaaaa: fake

**Status:** WIP
**Created:** 2026-04-22

## Tasks

- [ ] first task
- [⚒] second task in progress
- [✓] third task done

## Implementation Log
<!-- issue-task:log-append -->
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned

### What worked well
-
"""


# --------------------------------------------------------------------------
# locate_wip / show
# --------------------------------------------------------------------------


class TestLocate:
    def test_show_prints_path(self, fake_repo, capsys):
        path = _write_issue(fake_repo, _BASE)
        issue_task.main(['show'])
        out = capsys.readouterr().out.strip()
        assert Path(out) == path

    def test_no_wip_file_exits_2(self, fake_repo):
        with pytest.raises(SystemExit) as exc:
            issue_task.main(['show'])
        assert exc.value.code == 2

    def test_multiple_wip_files_exits_3(self, fake_repo):
        _write_issue(fake_repo, _BASE)
        (fake_repo / 'project' / 'issues' / 'wip' / 'ISSUE-260422-bbbbbb-other.md').write_text(_BASE, encoding='utf-8')
        with pytest.raises(SystemExit) as exc:
            issue_task.main(['show'])
        assert exc.value.code == 3


# --------------------------------------------------------------------------
# check / start / reopen
# --------------------------------------------------------------------------


class TestCheck:
    def test_flips_unchecked_to_done(self, fake_repo):
        path = _write_issue(fake_repo, _BASE)
        issue_task.main(['check', 'first task'])
        assert '- [✓] first task' in path.read_text(encoding='utf-8')

    def test_flips_wip_to_done(self, fake_repo):
        path = _write_issue(fake_repo, _BASE)
        issue_task.main(['check', 'in progress'])
        assert '- [✓] second task in progress' in path.read_text(encoding='utf-8')

    def test_idempotent_on_already_done(self, fake_repo, capsys):
        path = _write_issue(fake_repo, _BASE)
        before = path.read_text(encoding='utf-8')
        issue_task.main(['check', 'third task done'])
        assert path.read_text(encoding='utf-8') == before
        assert 'already' in capsys.readouterr().out.lower()

    def test_no_match_exits_4(self, fake_repo):
        _write_issue(fake_repo, _BASE)
        with pytest.raises(SystemExit) as exc:
            issue_task.main(['check', 'nonexistent task name'])
        assert exc.value.code == 4

    def test_ambiguous_match_exits_3(self, fake_repo):
        _write_issue(fake_repo, _BASE)
        with pytest.raises(SystemExit) as exc:
            issue_task.main(['check', 'task'])  # matches all three
        assert exc.value.code == 3

    def test_case_insensitive(self, fake_repo):
        path = _write_issue(fake_repo, _BASE)
        issue_task.main(['check', 'FIRST'])
        assert '- [✓] first task' in path.read_text(encoding='utf-8')


class TestStart:
    def test_flips_unchecked_to_wip(self, fake_repo):
        path = _write_issue(fake_repo, _BASE)
        issue_task.main(['start', 'first'])
        assert '- [⚒] first task' in path.read_text(encoding='utf-8')


class TestReopen:
    def test_flips_done_to_unchecked(self, fake_repo):
        path = _write_issue(fake_repo, _BASE)
        issue_task.main(['reopen', 'third'])
        assert '- [ ] third task done' in path.read_text(encoding='utf-8')


# --------------------------------------------------------------------------
# add
# --------------------------------------------------------------------------


class TestAdd:
    def test_appends_new_task(self, fake_repo):
        path = _write_issue(fake_repo, _BASE)
        issue_task.main(['add', 'brand new task'])
        text = path.read_text(encoding='utf-8')
        assert '- [ ] brand new task' in text
        # Preserves section order (not inserted after ## Implementation Log)
        tasks_idx = text.index('## Tasks')
        log_idx = text.index('## Implementation Log')
        new_idx = text.index('- [ ] brand new task')
        assert tasks_idx < new_idx < log_idx


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


class TestStatus:
    def test_flips_wip_to_closed(self, fake_repo):
        path = _write_issue(fake_repo, _BASE)
        issue_task.main(['status', 'Closed'])
        assert '**Status:** Closed' in path.read_text(encoding='utf-8')

    def test_idempotent(self, fake_repo, capsys):
        path = _write_issue(fake_repo, _BASE)
        before = path.read_text(encoding='utf-8')
        issue_task.main(['status', 'WIP'])
        assert path.read_text(encoding='utf-8') == before
        assert 'already' in capsys.readouterr().out.lower()

    def test_rejects_invalid_value(self, fake_repo):
        _write_issue(fake_repo, _BASE)
        # argparse rejects before reaching our validator
        with pytest.raises(SystemExit):
            issue_task.main(['status', 'Bogus'])


# --------------------------------------------------------------------------
# log
# --------------------------------------------------------------------------


class TestLog:
    def test_appends_entry_after_stable_anchor(self, fake_repo):
        path = _write_issue(fake_repo, _BASE)
        issue_task.main(['log', 'did the thing', '--files', 'a.py', 'b.py'])
        text = path.read_text(encoding='utf-8')
        assert '### ' in text
        assert '**Action**: did the thing' in text
        assert '- `a.py`' in text
        assert '- `b.py`' in text
        # Entry sits between the anchor and the Lessons Learned header
        anchor_idx = text.index('<!-- issue-task:log-append -->')
        entry_idx = text.index('**Action**: did the thing')
        lessons_idx = text.index('## Lessons Learned')
        assert anchor_idx < entry_idx < lessons_idx

    def test_appends_without_files(self, fake_repo):
        path = _write_issue(fake_repo, _BASE)
        issue_task.main(['log', 'just a note'])
        text = path.read_text(encoding='utf-8')
        assert '**Action**: just a note' in text
        assert '**Files Modified**' not in text.split('**Action**: just a note')[1].split('##')[0]

    def test_appends_after_existing_entry(self, fake_repo):
        path = _write_issue(fake_repo, _BASE)
        issue_task.main(['log', 'first entry'])
        issue_task.main(['log', 'second entry'])
        text = path.read_text(encoding='utf-8')
        # Newest entry is closest to the anchor (inserted at top of section)
        anchor_idx = text.index('<!-- issue-task:log-append -->')
        second_idx = text.index('**Action**: second entry')
        first_idx = text.index('**Action**: first entry')
        assert anchor_idx < second_idx < first_idx

    def test_works_with_legacy_anchor_only(self, fake_repo):
        """Old TEMPLATE.md had only the legacy placeholder comment."""
        legacy = _BASE.replace('<!-- issue-task:log-append -->\n', '')
        path = _write_issue(fake_repo, legacy)
        issue_task.main(['log', 'legacy placement'])
        text = path.read_text(encoding='utf-8')
        assert '**Action**: legacy placement' in text

    def test_works_without_any_anchor(self, fake_repo):
        """Fallback: insert right after ## Implementation Log header."""
        stripped = _BASE.replace('<!-- issue-task:log-append -->\n', '').replace(
            '<!-- Append entries here when performing development work on this issue -->\n', ''
        )
        path = _write_issue(fake_repo, stripped)
        issue_task.main(['log', 'bare placement'])
        text = path.read_text(encoding='utf-8')
        assert '**Action**: bare placement' in text
        # Still inside the Log section, not leaking into Lessons Learned
        log_idx = text.index('## Implementation Log')
        entry_idx = text.index('**Action**: bare placement')
        lessons_idx = text.index('## Lessons Learned')
        assert log_idx < entry_idx < lessons_idx


# --------------------------------------------------------------------------
# CLI smoke tests via subprocess (end-to-end, exercises the script's shebang)
# --------------------------------------------------------------------------


class TestCLIEntrypoint:
    def test_script_is_executable(self):
        assert _SCRIPT.is_file()
        assert _SCRIPT.stat().st_mode & 0o111

    def test_help_exits_zero(self):
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), '--help'],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0
        assert 'check' in result.stdout
        assert 'status' in result.stdout

    def test_subprocess_show(self, fake_repo):
        _write_issue(fake_repo, _BASE)
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), 'show'],
            capture_output=True, text=True, check=False, cwd=fake_repo,
        )
        assert result.returncode == 0
        assert 'ISSUE-260422-aaaaaa-fake.md' in result.stdout
