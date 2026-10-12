"""Done when: the runner's tests can run the four worker scenarios with no network.

These drive the real `claude` executable on PATH, the path the runner takes in production
(a subprocess, then `agents --json --all` polls, the transcript file and the job files).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from fake_claude import EXECUTABLE, FIXTURES, FakeClaude
from fake_claude.shim import load_scenario

REPO = Path(__file__).resolve().parents[3]
SPAWN = (
    '--bg',
    '--name',
    'worker',
    '--model',
    'sonnet',
    '--permission-mode',
    'manual',
    '--channels',
    'plugin:marcel@marcel-local',
    '--allowedTools',
    'mcp__plugin_marcel_marcel__reply',
    'mcp__plugin_marcel_marcel__report',
)


def _normalizer() -> ModuleType:
    """The contract's reference mapping, to prove what the shim writes is a real transcript."""
    path = REPO / 'contracts' / 'tests' / 'reference_normalizer.py'
    spec = importlib.util.spec_from_file_location('reference_normalizer', path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault('reference_normalizer', module)
    spec.loader.exec_module(module)
    return module


def spawn(fake: FakeClaude, scenario: str, work: Path) -> tuple[str, str]:
    fake.use(scenario)
    done = fake.run(*SPAWN, '--debug-file', str(work / 'debug.log'), '--', 'do it', cwd=work)
    assert done.returncode == 0, done.stderr
    prefix, short, name = done.stdout.strip().split(' · ')
    assert (prefix, name) == ('backgrounded', 'worker')
    row = fake.agent(short)
    assert row is not None
    return short, row['sessionId']


def events(fake: FakeClaude, session_id: str, work: Path) -> list[dict[str, Any]]:
    text = fake.transcript(session_id, work.resolve()).read_text('utf-8')
    return _normalizer().normalize_file(text, session_id)


@pytest.fixture
def work(tmp_path: Path) -> Path:
    path = tmp_path / 'work_tree.1'
    path.mkdir()
    return path


def test_worker_happy_path(fake: FakeClaude, work: Path) -> None:
    short, session_id = spawn(fake, 'worker-happy-path', work)

    row = fake.wait_for(short, 'done')

    assert row['status'] == 'idle'
    assert row['cwd'] == str(work.resolve())
    transcript = fake.transcript(session_id, work.resolve())
    lines = transcript.read_text('utf-8').splitlines()
    assert len(lines) == 78
    assert '"sessionId": "bf4bd301' not in transcript.read_text('utf-8')
    assert all(json.loads(line).get('sessionId', session_id) == session_id for line in lines)
    kinds = {e['type'] for e in events(fake, session_id, work)}
    assert {'text', 'tool_call', 'tool_result', 'diff', 'subagent_start', 'turn_end'} <= kinds
    subagents = transcript.with_suffix('') / 'subagents'
    assert (subagents / 'agent-a918dd7c41e7ee8b8.meta.json').is_file()
    job = fake.job(short)
    assert (job['state'], job['daemonShort'], job['sessionId']) == ('done', short, session_id)
    assert job['output'] == {
        'result': 'fixed sub() bug in calc.py (commit 772d9f4); verified with check'
    }
    assert 'Channel notifications registered' in (work / 'debug.log').read_text('utf-8')


def test_worker_needs_approval_then_allowed(fake: FakeClaude, work: Path) -> None:
    short, session_id = spawn(fake, 'worker-needs-approval', work)

    row = fake.wait_for(short, 'blocked')

    assert (row['status'], row['waitingFor']) == ('waiting', 'permission prompt')
    assert fake.job(short)['needs'] == 'approve Bash: touch made-by-t2.txt && ls'
    # SP2: the pending tool call is not in the transcript while the prompt is open.
    assert 'tool_call' not in {e['type'] for e in events(fake, session_id, work)}

    fake.answer(short, 'allow')
    row = fake.wait_for(short, 'done')

    assert 'waitingFor' not in row
    assert 'needs' not in fake.job(short)
    assert 'tool_call' in {e['type'] for e in events(fake, session_id, work)}


def test_worker_needs_approval_then_denied(fake: FakeClaude, work: Path) -> None:
    short, _ = spawn(fake, 'worker-needs-approval', work)
    fake.wait_for(short, 'blocked')

    fake.answer(short, 'deny')
    fake.wait_idle(short)

    row = fake.agent(short)
    assert row is not None
    assert (row['state'], row['status']) == ('working', 'idle')  # SP2: idle + working after "No"


def test_worker_fails(fake: FakeClaude, work: Path) -> None:
    short, session_id = spawn(fake, 'worker-fails', work)

    row = fake.wait_for(short, 'failed')

    assert 'status' not in row and 'pid' not in row
    assert fake.job(short)['needs'] == 'API error'
    errors = [e['data'] for e in events(fake, session_id, work) if e['type'] == 'error']
    assert errors and errors[-1]['usage_limit'] is False
    assert errors[-1]['api_error_status'] == 500


def test_limit_reached(fake: FakeClaude, work: Path) -> None:
    short, session_id = spawn(fake, 'limit-reached', work)

    row = fake.wait_for(short, 'blocked')

    assert 'waitingFor' not in row  # SP4: a limit is blocked WITHOUT a permission prompt
    assert fake.job(short)['needs'] == 'usage limit reached — check plan'
    errors = [e['data'] for e in events(fake, session_id, work) if e['type'] == 'error']
    assert errors and errors[-1]['usage_limit'] is True
    assert errors[-1]['api_error_status'] == 429


def test_stop_kills_a_session_held_at_the_gate(fake: FakeClaude, work: Path) -> None:
    short, _ = spawn(fake, 'worker-needs-approval', work)
    fake.wait_for(short, 'blocked')

    done = fake.run('stop', short)

    assert done.returncode == 0 and done.stdout.strip() == f'stopped {short}'
    assert fake.run('agents', '--json').stdout.strip() == '[]'
    row = fake.agent(short)
    assert row is not None and row['state'] == 'stopped' and 'pid' not in row
    fake.answer(short, 'allow')  # nobody is listening any more
    assert fake.store.load(short)['playing'] is False


def test_happy_path_replays_the_recorded_lifecycle() -> None:
    """The happy path walks the same (state, status) sequence SP2 recorded for t1."""
    recorded: list[tuple[str, str | None]] = []
    for line in (FIXTURES / 'agents' / 'lifecycle.jsonl').read_text('utf-8').splitlines():
        for agent in json.loads(line)['agents']:
            pair = (agent['state'], agent.get('status'))
            if agent['name'] == 't1' and agent['id'] == 'bf4bd301' and pair not in recorded:
                recorded.append(pair)
    played: list[tuple[str, str | None]] = []
    for step in load_scenario('worker-happy-path')['steps']:
        pair = (step['state'], step.get('status')) if 'state' in step else None
        if pair and pair not in played:
            played.append(pair)

    assert played == recorded[: len(played)]
    assert played == [('working', None), ('working', 'busy'), ('done', 'idle')]


def test_the_shim_never_imports_a_network_module() -> None:
    sources = [*Path(__file__).parent.glob('*.py'), Path(__file__).parent / 'bin' / 'claude']
    banned = ('socket', 'urllib', 'http', 'ssl', 'requests', 'httpx', 'anthropic')
    for path in sources:
        if path.name.startswith(('test_', 'conftest')) or path.name == 'netguard.py':
            continue  # netguard imports socket only to switch it off
        for line in path.read_text('utf-8').splitlines():
            words = line.split()
            if words[:1] in (['import'], ['from']) and len(words) > 1:
                assert words[1].split('.')[0] not in banned, f'{path.name}: {line}'


# The fake run as its own process, with a network call planted inside `claude --bg`, the way a
# careless change to the shim would add one. bin/claude's guard must stop it.
PLANTED = """
import runpy, socket, sys, urllib.request
sys.path.insert(0, sys.argv[1])
from fake_claude import shim

def phone_home(*_args):
    try:
        urllib.request.urlopen('http://127.0.0.1:9/', timeout=2)
    except OSError as e:
        print('REFUSED' if 'fake claude: no network' in str(e) else f'OTHER {e!r}')
    try:
        socket.create_connection(('127.0.0.1', 9), timeout=2)
    except OSError as e:
        print('REFUSED' if 'fake claude: no network' in str(e) else f'OTHER {e!r}')

shim.start_player = lambda *_args: phone_home()
sys.argv = [sys.argv[2], '--bg', '--', 'go']
runpy.run_path(sys.argv[0], run_name='__main__')
"""


def test_the_fake_process_refuses_the_network(fake: FakeClaude) -> None:
    done = subprocess.run(
        [sys.executable, '-c', PLANTED, str(EXECUTABLE.parents[2]), str(EXECUTABLE)],
        env=fake.env(),
        cwd=fake.home,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[:2] == ['REFUSED', 'REFUSED']
