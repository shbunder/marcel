"""`plugins/marcel/skills/project-kickoff/scripts/missing.py` (S-20.1, B-45): what a repo lacks for
a Claude Code session. A standalone file (standard library only), loaded by path."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2] / 'skills' / 'project-kickoff' / 'scripts' / 'missing.py'
)
ALL = ['claude_md', 'check_command', 'ci', 'pr_template']


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location('marcel_kickoff_missing', SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


missing = load()


def complete(repo: Path, command: str = 'make check') -> Path:
    (repo / 'Makefile').write_text('check:\n\truff check . && pytest\n')
    (repo / 'CLAUDE.md').write_text(f'# Garden planner\n\nRun `{command}` before every commit.\n')
    workflows = repo / '.github' / 'workflows'
    workflows.mkdir(parents=True)
    (workflows / 'check.yml').write_text(f'jobs:\n  check:\n    steps:\n      - run: {command}\n')
    (repo / '.github' / 'pull_request_template.md').write_text('## What changed\n')
    return repo


def test_a_bare_repo_lacks_all_four(tmp_path: Path) -> None:
    assert missing.missing(tmp_path) == {'missing': ALL, 'check_command': None}


def test_a_complete_repo_lacks_nothing_so_no_pr(tmp_path: Path) -> None:
    assert missing.missing(complete(tmp_path)) == {'missing': [], 'check_command': 'make check'}


def test_a_claude_md_that_does_not_name_the_command_is_missing(tmp_path: Path) -> None:
    complete(tmp_path)
    (tmp_path / 'CLAUDE.md').write_text('# Garden planner\n\nBe nice.\n')
    assert missing.missing(tmp_path)['missing'] == ['claude_md']


def test_ci_that_does_not_run_the_command_is_missing(tmp_path: Path) -> None:
    complete(tmp_path)
    (tmp_path / '.github' / 'workflows' / 'check.yml').write_text('jobs: {}\n')
    (tmp_path / '.github' / 'workflows' / 'notes.txt').write_text('make check\n')  # not a workflow
    assert missing.missing(tmp_path)['missing'] == ['ci']


@pytest.mark.parametrize(
    ('files', 'command'),
    [
        ({'package.json': '{"scripts": {"check": "tsc && vitest"}}'}, 'npm run check'),
        ({'package.json': '{"scripts": {"test": "vitest"}}'}, 'npm test'),
        ({'Cargo.toml': '[package]\nname = "x"\n'}, 'cargo test'),
        ({'go.mod': 'module x\n'}, 'go test ./...'),
        ({'pyproject.toml': '[tool.pytest.ini_options]\n'}, 'pytest'),
        ({'pytest.ini': '[pytest]\n'}, 'pytest'),
        ({'setup.cfg': '[tool:pytest]\n'}, 'pytest'),
        ({'tox.ini': '[pytest]\n'}, 'pytest'),
        ({'GNUmakefile': 'check:\n\tpytest\n'}, 'make check'),
        ({'makefile': 'check: lint\n\tpytest\n'}, 'make check'),
    ],
)
def test_the_check_command_is_found(tmp_path: Path, files: dict[str, str], command: str) -> None:
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    assert missing.check_command(tmp_path) == command


@pytest.mark.parametrize(
    'package',
    [
        '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}',  # npm init's
        '{"scripts": "not a mapping"}',
        'not json',
        '["a list"]',
    ],
)
def test_no_real_check_command_is_none(tmp_path: Path, package: str) -> None:
    (tmp_path / 'package.json').write_text(package)
    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "x"\n')
    assert missing.check_command(tmp_path) is None


def test_a_make_variable_named_check_is_no_target(tmp_path: Path) -> None:
    (tmp_path / 'Makefile').write_text('check := ruff\ncheck ::= x\nall:\n\techo hi\n')
    assert missing.check_command(tmp_path) is None


def test_a_claude_md_in_the_claude_folder_counts(tmp_path: Path) -> None:
    complete(tmp_path)
    (tmp_path / 'CLAUDE.md').unlink()
    (tmp_path / '.claude').mkdir()
    (tmp_path / '.claude' / 'CLAUDE.md').write_text('Run `make check` first.\n')
    assert missing.missing(tmp_path)['missing'] == []


def test_workflows_without_a_check_command_do_not_count_as_ci(tmp_path: Path) -> None:
    workflows = tmp_path / '.github' / 'workflows'
    workflows.mkdir(parents=True)
    (workflows / 'deploy.yml').write_text('jobs: {}\n')
    assert missing.missing(tmp_path)['missing'] == ALL


@pytest.mark.parametrize(
    'where',
    [
        '.github/PULL_REQUEST_TEMPLATE.md',
        'pull_request_template.md',
        'docs/pull_request_template.md',
        '.github/PULL_REQUEST_TEMPLATE/feature.md',
    ],
)
def test_a_pr_template_is_found_where_github_looks(tmp_path: Path, where: str) -> None:
    path = tmp_path / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('## What changed\n')
    assert 'pr_template' not in missing.missing(tmp_path)['missing']


def test_an_empty_template_folder_is_no_template(tmp_path: Path) -> None:
    (tmp_path / '.github' / 'PULL_REQUEST_TEMPLATE').mkdir(parents=True)
    assert 'pr_template' in missing.missing(tmp_path)['missing']


def test_the_command_line_prints_json(tmp_path: Path) -> None:
    complete(tmp_path)
    out = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True, text=True, check=True
    )
    assert json.loads(out.stdout) == {'missing': [], 'check_command': 'make check'}


def test_a_wrong_argument_is_a_usage_line(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert missing.main(['missing.py']) == 2
    assert missing.main(['missing.py', str(tmp_path / 'nope')]) == 2
    assert 'Usage' in capsys.readouterr().err
    assert missing.main(['missing.py', str(tmp_path)]) == 0
