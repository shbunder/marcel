"""What a repo lacks for a Claude Code session to work well in it (B-45, S-20.1).

    python3 missing.py <repo folder>

Prints one JSON object and exits 0:

    {"missing": ["claude_md", "check_command", "ci", "pr_template"], "check_command": null}

- `claude_md`: no `CLAUDE.md` (at the top or in `.claude/`) that names the check command;
- `check_command`: no check command found (a `check` target in a Makefile, makefile or
  GNUmakefile, a package.json `check` or `test` script, `cargo test` for a Cargo.toml,
  `go test ./...` for a go.mod, `pytest` for a pytest.ini, or a pyproject.toml, setup.cfg or
  tox.ini that configures pytest);
- `ci`: no workflow under `.github/workflows/` that runs the check command;
- `pr_template`: no pull-request template (`.github/pull_request_template.md`, the same at the top
  or in `docs/`, or a `.github/PULL_REQUEST_TEMPLATE/` folder; any case).

`check_command` is the command found, so the kickoff quest names that one rather than adding
another. Standard library only: the quest runs it with the machine's python3.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ORDER = ('claude_md', 'check_command', 'ci', 'pr_template')
PLACEHOLDER_TEST = 'no test specified'  # npm init's default "test" script


def _read(path: Path) -> str:
    try:
        return path.read_text(errors='replace')
    except OSError:
        return ''


def check_command(repo: Path) -> str | None:
    """The repo's one check command, or None."""
    for name in ('GNUmakefile', 'makefile', 'Makefile'):  # make's own order
        # A rule `check:`, never a variable `check := …` or `check ::= …`.
        if re.search(r'^check\s*:(?![:=])', _read(repo / name), re.M):
            return 'make check'
    package = repo / 'package.json'
    if package.is_file():
        try:
            scripts = json.loads(_read(package)).get('scripts') or {}
        except (ValueError, AttributeError):
            scripts = {}
        if isinstance(scripts, dict):
            if 'check' in scripts:
                return 'npm run check'
            test = scripts.get('test')
            if isinstance(test, str) and PLACEHOLDER_TEST not in test:
                return 'npm test'
    if (repo / 'Cargo.toml').is_file():
        return 'cargo test'
    if (repo / 'go.mod').is_file():
        return 'go test ./...'
    if (
        (repo / 'pytest.ini').is_file()
        or '[tool.pytest' in _read(repo / 'pyproject.toml')
        or '[tool:pytest]' in _read(repo / 'setup.cfg')
        or '[pytest]' in _read(repo / 'tox.ini')
    ):
        return 'pytest'
    return None


def _has_pr_template(repo: Path) -> bool:
    for folder in (repo / '.github', repo, repo / 'docs'):
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            name = entry.name.lower()
            if entry.is_file() and name in (
                'pull_request_template.md',
                'pull_request_template.txt',
            ):
                return True
            if entry.is_dir() and name == 'pull_request_template' and any(entry.iterdir()):
                return True
    return False


def _ci_runs(repo: Path, command: str | None) -> bool:
    workflows = repo / '.github' / 'workflows'
    if not workflows.is_dir():
        return False
    texts = [_read(p) for p in workflows.iterdir() if p.suffix in ('.yml', '.yaml')]
    if command is None:
        return False
    return any(command in text for text in texts)


def missing(repo: Path) -> dict[str, object]:
    command = check_command(repo)
    claude_md = _read(repo / 'CLAUDE.md') + _read(repo / '.claude' / 'CLAUDE.md')
    lacks = {
        'claude_md': command is None or command not in claude_md,
        'check_command': command is None,
        'ci': not _ci_runs(repo, command),
        'pr_template': not _has_pr_template(repo),
    }
    return {'missing': [item for item in ORDER if lacks[item]], 'check_command': command}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not Path(argv[1]).is_dir():
        print('Usage: python3 missing.py <repo folder>', file=sys.stderr)
        return 2
    print(json.dumps(missing(Path(argv[1]))))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
