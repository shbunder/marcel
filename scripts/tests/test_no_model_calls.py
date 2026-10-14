"""The no-model-calls rule as a test (.claude/rules/no-model-calls.md).

Marcel's own code never imports a model SDK, never depends on one and never calls a model API.
Naming `ANTHROPIC_API_KEY` is fine (the runner strips it from claude's environment); a real key
is not.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Where Marcel's own code lives. Tests are scanned for keys only: they name the SDKs to ban them.
CODE_DIRS = (
    'brain',
    'contracts',
    'hub',
    'ios',
    'mock-hub',
    'plugins',
    'runner',
    'shared',
    'scripts',
    'deploy',
)

SDK_USE = re.compile(
    r'^\s*(import|from)\s+(anthropic|claude_agent_sdk|openai)\b'  # Python imports
    r'|from\s+[\'"](@anthropic-ai/|openai\b)'  # TS imports
    r'|require\([\'"](@anthropic-ai/|openai\b)'
    r'|import\s+(Anthropic|OpenAI)\b'  # Swift packages
    r'|api\.anthropic\.com|api\.openai\.com',
    re.MULTILINE,
)
SDK_DEPENDENCY = re.compile(
    r'[\'"](anthropic|claude-agent-sdk|openai|@anthropic-ai/[\w-]+)[\'"\s<>=~!\[]'
)
REAL_KEY = re.compile(r'sk-ant-[A-Za-z0-9_-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}')


def tracked(*dirs: str) -> list[Path]:
    out = subprocess.run(
        ['git', 'ls-files', '-z', *dirs], cwd=ROOT, capture_output=True, check=True
    )
    return [ROOT / p for p in out.stdout.decode().split('\0') if p]


def is_test(path: Path) -> bool:
    parts = path.relative_to(ROOT).parts
    return 'tests' in parts or 'Tests' in parts or path.name.startswith('test_')


def text(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except (UnicodeDecodeError, OSError):
        return ''


def test_no_model_sdk_or_api_in_marcels_code() -> None:
    found = [
        f'{p.relative_to(ROOT)}: {m.group(0).strip()}'
        for p in tracked(*CODE_DIRS)
        if p.suffix in {'.py', '.ts', '.js', '.mjs', '.swift', '.sh'} and not is_test(p)
        for m in SDK_USE.finditer(text(p))
    ]
    assert not found, (
        'Model SDK or API call in Marcel code (see .claude/rules/no-model-calls.md):\n'
        + '\n'.join(found)
    )


def test_no_model_sdk_dependency() -> None:
    found = [
        f'{p.relative_to(ROOT)}: {m.group(1)}'
        for p in tracked(*CODE_DIRS)
        if p.name in {'pyproject.toml', 'package.json', 'Package.swift'}
        for m in SDK_DEPENDENCY.finditer(text(p))
    ]
    assert not found, 'Model SDK dependency (see .claude/rules/no-model-calls.md):\n' + '\n'.join(
        found
    )


def test_no_real_model_key_in_git() -> None:
    found = [str(p.relative_to(ROOT)) for p in tracked() if REAL_KEY.search(text(p))]
    assert not found, 'Something that looks like a real model API key is tracked:\n' + '\n'.join(
        found
    )


def test_the_patterns_catch_what_they_ban() -> None:
    # Without this, a typo in a pattern would turn the three tests above green for good.
    assert SDK_USE.search('import anthropic\n')
    assert SDK_USE.search('from claude_agent_sdk import query\n')
    assert SDK_USE.search("import { query } from '@anthropic-ai/claude-agent-sdk'\n")
    assert SDK_USE.search("fetch('https://api.anthropic.com/v1/messages')")
    assert not SDK_USE.search("env.pop('ANTHROPIC_API_KEY', None)")
    assert SDK_DEPENDENCY.search('dependencies = ["anthropic>=0.40"]')
    assert SDK_DEPENDENCY.search('"@anthropic-ai/claude-agent-sdk": "^0.2.0"')
    assert not SDK_DEPENDENCY.search('dependencies = ["fastapi>=0.115"]')
    assert REAL_KEY.search('sk-ant-api03-' + 'a' * 30)
    assert not REAL_KEY.search("'sk-ant-x'")
