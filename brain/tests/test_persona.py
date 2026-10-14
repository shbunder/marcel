"""brain/CLAUDE.md: short, names tools only by their exact names, and keeps the hard rules."""

import re

import pytest
from conftest import BRAIN, ROOT
from marcel_tools.tools import TOOLS

PERSONA = (BRAIN / 'CLAUDE.md').read_text()
MAX_LINES = 45
MAX_BYTES = 3_500

BRAIN_TOOLS = {tool.name for tool in TOOLS}
CHANNEL_TOOLS = {'reply'}  # the brain's channel tool; `report` is for workers
SKILLS = {p.parent.name for p in (ROOT / 'plugins' / 'marcel' / 'skills').glob('*/SKILL.md')}
RUNNER_API = (ROOT / 'contracts' / 'runner-api.yaml').read_text()
RUNNER_ALLOWED = set(re.findall(r'mcp__plugin_marcel_(?:tools|marcel)__(\w+)', RUNNER_API))


def code_words(text: str) -> set[str]:
    """Every `lower_case` word in backticks: each must be a real tool or skill."""
    return set(re.findall(r'`([a-z][a-z0-9_]*)`', text))


def test_short() -> None:
    lines = PERSONA.count('\n')
    size = len(PERSONA.encode())
    assert lines <= MAX_LINES, f'CLAUDE.md is {lines} lines; keep it at {MAX_LINES} or fewer'
    assert size <= MAX_BYTES, f'CLAUDE.md is {size} bytes; keep it at {MAX_BYTES} or fewer'


def test_every_tool_is_named_exactly() -> None:
    unknown = code_words(PERSONA) - BRAIN_TOOLS - CHANNEL_TOOLS - SKILLS
    assert not unknown, f'CLAUDE.md names tools or skills that do not exist: {sorted(unknown)}'
    assert 'mcp__' not in PERSONA, 'name tools by their short name, as the skills do'


def test_every_tool_named_is_one_the_runner_allows() -> None:
    named = code_words(PERSONA) & (BRAIN_TOOLS | CHANNEL_TOOLS)
    assert named, 'the persona names no tools'
    assert named <= RUNNER_ALLOWED, f'not pre-allowed by the runner: {sorted(named - RUNNER_ALLOWED)}'


def test_the_linter_catches_a_wrong_tool() -> None:
    assert code_words('Start it with `task_spawn`.') - BRAIN_TOOLS - CHANNEL_TOOLS - SKILLS == {'task_spawn'}


def test_points_at_orchestrate_instead_of_repeating_it() -> None:
    assert '`orchestrate`' in PERSONA
    assert 'B-0' not in PERSONA, 'the behaviours live in the orchestrate skill, not here'


@pytest.mark.parametrize(
    'rule',
    [
        'Use your tools',
        'Never pretend',
        'Read `MEMORY.md` first',
        'Never call a model or an API yourself',
        'Never restart anything',
        'pull request',
    ],
)
def test_hard_rules(rule: str) -> None:
    assert rule in PERSONA


def test_the_voice() -> None:
    for words in ('{{ANIMAL}}', '{{PERSONALITY}}', 'Lead with the outcome', 'plain words', 'phone'):
        assert words in PERSONA


def test_points_at_the_digest_skill_for_a_digest_event() -> None:
    assert '`digest` skill' in PERSONA, 'a digest_due event must lead to the digest skill (S-09.2)'
