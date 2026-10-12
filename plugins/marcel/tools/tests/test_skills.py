"""Lint for the plugin's skills (`plugins/marcel/skills/*/SKILL.md`, S-05.5) and checks on the eval.

A skill must have `name` and `description` frontmatter, stay small (the brain re-reads it every
turn), name only tools that exist (the brain tools here, `reply` and `report` from the channel
contract) and only report kinds the contract knows, and quote the spec's behaviours word for word.
"""

import io
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from skills_eval import (
    CASES,
    REPLY,
    ROOT,
    SERVER_NAME,
    SKILLS,
    EvalServer,
    canned_caller,
    check_case,
    claude_command,
    claude_env,
    load_cases,
    mcp_config,
    read_log,
    tool_names,
)

from marcel_tools.tools import BY_NAME, TOOLS

MAX_LINES = 200
MAX_BYTES = 12_000
MAX_DESCRIPTION = 1024  # Claude Code's limit for a skill description
EXPECTED = {'orchestrate', 'worker-protocol', 'digest'}
# The behaviours each skill quotes from plan/01-functional-spec.md.
QUOTES = {
    'orchestrate': ['B-03', 'B-04', 'B-05', 'B-06', 'B-07', 'B-13', 'B-31'],
    'digest': ['B-17'],
}

CHANNEL = json.loads((ROOT / 'contracts' / 'channel.schema.json').read_text())['$defs']
CHANNEL_TOOLS = {CHANNEL[d]['properties']['type']['const'] for d in ('Reply', 'Report')}
REPORT_KINDS = set(CHANNEL['Report']['properties']['kind']['enum'])
ARTIFACT_KINDS = set(CHANNEL['Artifact']['properties']['kind']['enum'])
KNOWN_TOOLS = {tool.name for tool in TOOLS} | CHANNEL_TOOLS
FAMILIES = {name.split('_')[0] + '_' for name in BY_NAME}


def skill_files() -> list[Path]:
    return sorted(SKILLS.glob('*/SKILL.md'))


def split(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text()
    match = re.match(r'^---\n(.*?)\n---\n(.*)$', text, re.S)
    assert match, f'{path.parent.name}: SKILL.md must start with --- frontmatter ---'
    meta = yaml.safe_load(match.group(1))
    assert isinstance(meta, dict), f'{path.parent.name}: frontmatter must be a mapping'
    return meta, match.group(2)


def flat(text: str) -> str:
    text = re.sub(r'^\s*>\s?', '', text, flags=re.M)
    return re.sub(r'\s+', ' ', text).strip()


def spec_behaviour(bid: str) -> str:
    spec = (ROOT / 'plan' / '01-functional-spec.md').read_text()
    match = re.search(rf'^- \*\*{bid} [^*]+\*\*(.*?)(?=^- \*\*B-|^\S|\Z)', spec, re.S | re.M)
    assert match, f'{bid} is not in the functional spec'
    return flat(match.group(1))


def test_the_three_skills_exist() -> None:
    assert {p.parent.name for p in skill_files()} == EXPECTED
    for folder in SKILLS.iterdir():
        if folder.is_dir() and folder.name != 'evals':
            assert (folder / 'SKILL.md').is_file(), f'{folder.name} has no SKILL.md'


@pytest.mark.parametrize('path', skill_files(), ids=lambda p: p.parent.name)
def test_frontmatter(path: Path) -> None:
    meta, _ = split(path)
    assert meta.get('name') == path.parent.name, 'name must match the folder'
    assert re.fullmatch(r'[a-z0-9-]{1,64}', meta['name'])
    description = meta.get('description')
    assert isinstance(description, str), 'a skill needs a description'
    assert 40 <= len(description) <= MAX_DESCRIPTION
    assert 'Use ' in description, 'the description says when to use the skill'


@pytest.mark.parametrize('path', skill_files(), ids=lambda p: p.parent.name)
def test_size(path: Path) -> None:
    text = path.read_text()
    assert len(text.splitlines()) <= MAX_LINES, f'{path.parent.name} is over {MAX_LINES} lines'
    assert len(text.encode()) <= MAX_BYTES, f'{path.parent.name} is over {MAX_BYTES} bytes'


@pytest.mark.parametrize('path', skill_files(), ids=lambda p: p.parent.name)
def test_every_tool_named_exists(path: Path) -> None:
    _, body = split(path)
    called = set(re.findall(r'`([a-z_]+)\(', body))
    bare = {t for t in re.findall(r'`([a-z]+_[a-z_]+)`', body) if t.startswith(tuple(FAMILIES))}
    unknown = (called | bare) - KNOWN_TOOLS
    assert not unknown, f'{path.parent.name} names tools that do not exist: {sorted(unknown)}'


@pytest.mark.parametrize('path', skill_files(), ids=lambda p: p.parent.name)
def test_report_kinds_are_the_contracts(path: Path) -> None:
    _, body = split(path)
    kinds = set(re.findall(r'report\(([a-z_]+)', body))
    assert kinds <= REPORT_KINDS, f'unknown report kinds: {sorted(kinds - REPORT_KINDS)}'


def test_worker_protocol_covers_every_report_kind_and_next() -> None:
    _, body = split(SKILLS / 'worker-protocol' / 'SKILL.md')
    for kind in REPORT_KINDS:
        assert f'`{kind}`' in body, f'worker-protocol does not explain {kind}'
    for kind in ARTIFACT_KINDS:
        assert f'`{kind}`' in body, f'worker-protocol does not explain the {kind} artifact'
    assert 'give your remaining steps in `next`' in body.lower()
    assert 'report(done)' in body and 'report(failed)' in body


@pytest.mark.parametrize(('skill', 'bid'), [(s, b) for s, ids in QUOTES.items() for b in ids])
def test_behaviours_are_quoted_word_for_word(skill: str, bid: str) -> None:
    _, body = split(SKILLS / skill / 'SKILL.md')
    assert f'({bid})' in body, f'{skill} does not name {bid}'
    assert spec_behaviour(bid) in flat(body), f'{skill} does not quote {bid} as the spec says it'


def test_the_linter_catches_a_wrong_tool(tmp_path: Path) -> None:
    bad = tmp_path / 'oops' / 'SKILL.md'
    bad.parent.mkdir()
    bad.write_text('---\nname: oops\ndescription: x\n---\nCall `tasks_create(title)`.\n')
    with pytest.raises(AssertionError, match='tasks_create'):
        test_every_tool_named_exists(bad)
    with pytest.raises(AssertionError):
        test_frontmatter(bad)
    bad.write_text('no frontmatter')
    with pytest.raises(AssertionError, match='frontmatter'):
        split(bad)


# The eval's cases (run live on the NUC) are checked here, so a renamed tool breaks the gate.


def test_eight_cases_naming_real_tools_and_arguments() -> None:
    cases = load_cases()
    assert len(cases) == 8
    assert len({c['id'] for c in cases}) == 8
    schemas = {tool.name: tool.schema for tool in TOOLS} | {REPLY['name']: REPLY['inputSchema']}
    for case in cases:
        named = {s['tool'] for s in case['expect']} | set(case['forbid']) | set(case['answers'])
        assert named <= tool_names(), f'{case["id"]}: {sorted(named - tool_names())}'
        for step in case['expect']:
            props = schemas[step['tool']].get('properties', {})
            assert set(step['args']) <= set(props), f'{case["id"]}: {step}'
            for key, value in step['args'].items():
                assert value in props[key].get('enum', [value])
        assert case['request'].strip()


CASE = {
    'id': 'demo',
    'answers': {'tasks_list': 'one thread'},
    'expect': [
        {'tool': 'tasks_list', 'args': {}},
        {'tool': 'tasks_spawn', 'args': {'location': 'cloud', 'model': 'opus'}},
    ],
    'forbid': ['tasks_send'],
}


def call(tool: str, **args: Any) -> dict[str, Any]:
    return {'tool': tool, 'args': args}


def test_check_case_passes_in_order_with_extra_calls() -> None:
    calls = [call('tasks_list'), call('reply', text='hi'),
             call('tasks_spawn', location='cloud', model='claude-OPUS-4', title='x')]  # fmt: skip
    assert check_case(CASE, calls) == []


def test_check_case_names_what_went_wrong() -> None:
    wrong_place = [call('tasks_list'), call('tasks_spawn', location='nuc', model='opus')]
    assert 'expected tasks_spawn' in check_case(CASE, wrong_place)[0]
    out_of_order = [call('tasks_spawn', location='cloud', model='opus'), call('tasks_list')]
    assert check_case(CASE, out_of_order)
    no_model = [call('tasks_list'), call('tasks_spawn', location='cloud', model=3)]
    assert check_case(CASE, no_model)
    forbidden = [call('tasks_list'), call('tasks_send', id='t', text='x'),
                 call('tasks_spawn', location='cloud', model='opus')]  # fmt: skip
    assert check_case(CASE, forbidden) == [
        'demo: called tasks_send, which this request must not use.'
    ]


def test_the_fake_server_lists_logs_and_answers(tmp_path: Path) -> None:
    log = tmp_path / 'calls.jsonl'
    out = io.StringIO()
    server = EvalServer(canned_caller(CASE, log), lambda line: out.write(line + '\n'))

    def rpc(rid: int, method: str, params: Any = None) -> None:
        server.handle_line(
            json.dumps({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params})
        )

    rpc(1, 'tools/list')
    rpc(2, 'tools/call', {'name': 'tasks_list', 'arguments': {}})
    rpc(3, 'tools/call', {'name': 'tasks_spawn', 'arguments': 'x'})
    rpc(4, 'tools/call', {'name': 'nope', 'arguments': {}})
    rpc(5, 'ping')
    listed, first, second, third, pong = (json.loads(x) for x in out.getvalue().splitlines())
    assert {t['name'] for t in listed['result']['tools']} == tool_names()
    assert first['result']['content'][0]['text'] == 'one thread'
    assert second['result']['content'][0]['text'] == 'Done.'
    assert third['result']['isError'] is True
    assert pong['result'] == {}
    assert read_log(log) == [call('tasks_list'), call('tasks_spawn'), call('nope')]
    assert read_log(tmp_path / 'missing.jsonl') == []


def test_the_claude_command_uses_the_skill_and_only_the_fake_server(tmp_path: Path) -> None:
    case = load_cases()[0]
    cmd = claude_command(case, tmp_path / 'mcp.json')
    assert cmd[:3] == ['claude', '-p', case['request']]
    assert '--strict-mcp-config' in cmd
    allowed = cmd[cmd.index('--allowedTools') + 1].split()
    assert f'mcp__{SERVER_NAME}__tasks_spawn' in allowed
    blocked = cmd[cmd.index('--disallowedTools') + 1].split()
    assert {'Task', 'Agent', 'Bash'} <= set(blocked)
    assert '# Orchestrate' in cmd[cmd.index('--append-system-prompt') + 1]
    server = mcp_config(case['id'], tmp_path / 'log')['mcpServers'][SERVER_NAME]
    assert server['args'][1:] == ['serve', case['id'], str(tmp_path / 'log')]
    assert CASES.is_file()


def test_the_live_claude_gets_no_api_key() -> None:
    env = claude_env({
        'PATH': '/usr/bin', 'HOME': '/home/me', 'CLAUDE_CODE_SESSION_ID': 'abc',
        'ANTHROPIC_API_KEY': 'sk-ant-x', 'ANTHROPIC_AUTH_TOKEN': 't', 'ANTHROPIC_BASE_URL': 'u',
        'CLAUDE_CODE_OAUTH_TOKEN': 'o', 'CLAUDE_CODE_API_KEY_HELPER_TTL_MS': '1',
        'CLAUDE_CODE_USE_BEDROCK': '1',
    })  # fmt: skip
    assert 'ANTHROPIC_API_KEY' not in env
    assert env == {'PATH': '/usr/bin', 'HOME': '/home/me', 'CLAUDE_CODE_SESSION_ID': 'abc'}
