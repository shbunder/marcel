"""Each fake command and each refusal, run in-process (see conftest.Inline)."""

from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path

import pytest

from fake_claude import FIXTURES, shim
from fake_claude.conftest import Inline


def scenario(tmp_path: Path, text: str) -> str:
    path = tmp_path / 'custom.yaml'
    path.write_text(text, encoding='utf-8')
    return str(path)


# --- simple commands and refusals -----------------------------------------------------------


def test_version_and_attach(inline: Inline) -> None:
    assert inline('--version').out == '2.1.289 (Claude Code)\n'
    attached = inline('attach', 'bf4bd301')
    assert (attached.code, attached.out, attached.err) == (0, '', '')


@pytest.mark.parametrize(
    'argv',
    [
        [],
        ['--model', 'sonnet', 'hello'],
        ['agents'],
        ['agents', '--json', 'extra'],
        ['--bg', '--frobnicate', '--', 'x'],
        ['-p', 'what is 6 times 7?'],
        ['stop'],
    ],
)
def test_anything_unrecorded_is_refused_loudly(inline: Inline, argv: list[str]) -> None:
    result = inline(*argv)
    assert result.code == 2
    assert 'is not faked' in result.err


def test_a_value_flag_without_its_value(inline: Inline) -> None:
    result = inline('--bg', '--name')
    assert (result.code, result.err) == (1, 'error: option --name needs a value\n')


def test_refuses_a_real_claude_home(inline: Inline) -> None:
    real = inline.home / '.claude'
    real.mkdir()
    (real / 'settings.json').write_text('{}')

    result = inline('agents', '--json')

    assert result.code == 1
    assert 'looks like a real Claude Code home' in result.err
    assert sorted(p.name for p in real.iterdir()) == ['settings.json']


def test_claims_an_empty_home_and_reuses_it(inline: Inline) -> None:
    (inline.home / '.claude').mkdir()
    assert inline('agents', '--json').out == '[]\n'
    assert (inline.home / '.claude' / '.fake-claude').is_file()
    assert inline('agents', '--json').code == 0


def test_needs_home() -> None:
    err = io.StringIO()
    assert shim.main(['agents', '--json'], env={}, out=io.StringIO(), err=err) == 1
    assert err.getvalue() == 'fake claude: HOME is not set, so there is nowhere to write.\n'


def test_untrusted_workspace(inline: Inline) -> None:
    result = inline('--bg', '--', 'x', FAKE_CLAUDE_SCENARIO='workspace-untrusted')

    assert result.code == 1
    assert result.err == (
        f'Workspace not trusted. Run claude in {os.getcwd()} once and accept the trust prompt\n'
    )
    assert inline.started == []
    assert inline('agents', '--json', '--all').out == '[]\n'


def test_bg_needs_a_prompt(inline: Inline) -> None:
    result = inline('--bg', '--channels', 'plugin:marcel@marcel-local', 'the prompt')
    assert result.code == 1
    assert 'Put it after `--`' in result.err


# --- spawning and the agents list -----------------------------------------------------------


def test_spawn_and_play_the_happy_path(inline: Inline) -> None:
    short = inline.spawn('--name', 't1')

    starting = inline('agents', '--json').json()
    assert starting == [
        {
            'id': short,
            'cwd': os.getcwd(),
            'kind': 'background',
            'startedAt': starting[0]['startedAt'],
            'sessionId': starting[0]['sessionId'],
            'name': 't1',
            'state': 'working',
        }
    ]
    inline.play()

    rows = inline('agents', '--json').json()
    assert list(rows[0]) == [
        'pid',
        'id',
        'cwd',
        'kind',
        'startedAt',
        'sessionId',
        'name',
        'status',
        'state',
    ]
    assert (rows[0]['pid'], rows[0]['state'], rows[0]['status']) == (os.getpid(), 'done', 'idle')
    timeline = inline.home / '.claude' / 'jobs' / short / 'timeline.jsonl'
    details = [json.loads(line)['detail'] for line in timeline.read_text().splitlines()]
    assert details[0] == 'Finding buggy function'
    assert details[-1] == 'sub() bug fixed in calc.py, committed as 772d9f4'


def test_name_falls_back_to_the_prompt(inline: Inline) -> None:
    result = inline('--bg', '--', 'Fix the flaky login test please')
    assert result.out.endswith(' · Fix the flaky login\n')


def test_scenario_queue_last_one_repeats(inline: Inline, tmp_path: Path) -> None:
    queue = inline.store.root
    inline.store.claim()
    (queue / 'scenario').write_text('worker-fails\nlimit-reached\n')

    played = []
    for _ in range(3):
        short = inline.spawn()
        played.append(Path(inline.session(short)['scenario']).stem)

    assert played == ['worker-fails', 'limit-reached', 'limit-reached']


def test_empty_queue_uses_the_default(inline: Inline) -> None:
    inline.store.claim()
    (inline.store.root / 'scenario').write_text('\n')
    short = inline.spawn()
    assert Path(inline.session(short)['scenario']).stem == 'worker-happy-path'


def test_fixed_session_id_is_used_once(inline: Inline, tmp_path: Path) -> None:
    path = scenario(tmp_path, 'session_id: 11111111-2222-4333-8444-555555555555\n')

    first = inline.spawn(FAKE_CLAUDE_SCENARIO=path)
    second = inline.spawn(FAKE_CLAUDE_SCENARIO=path)

    assert first == '11111111'
    assert second != first


def test_a_scenario_without_transcript_or_job(inline: Inline, tmp_path: Path) -> None:
    path = scenario(tmp_path, 'steps:\n  - state: done\n    status: idle\n    transcript: end\n')
    short = inline.spawn(FAKE_CLAUDE_SCENARIO=path)
    inline.play()
    assert inline.session(short)['state'] == 'done'
    assert json.loads((inline.home / '.claude/jobs' / short / 'state.json').read_text())['intent']


def test_all_shows_finished_sessions(inline: Inline) -> None:
    short = inline.spawn()
    inline.play()
    inline('stop', short)

    assert inline('agents', '--json').json() == []
    [stopped] = inline('agents', '--json', '--all').json()
    assert (stopped['state'], 'pid' in stopped, 'status' in stopped) == ('done', False, False)


def test_debug_file_says_registered_or_skipped(inline: Inline, tmp_path: Path) -> None:
    log = tmp_path / 'logs' / 'debug.log'
    channel = ('--channels', 'plugin:marcel@marcel-local')
    inline.spawn(*channel, '--debug-file', str(log))
    inline.spawn('--debug-file', str(log))
    inline.spawn(*channel, '--debug-file', str(log), FAKE_CLAUDE_SCENARIO='channel-skipped')

    lines = log.read_text().splitlines()
    assert [line.split('"plugin:marcel:marcel": ')[1].split(':')[0] for line in lines] == [
        'Channel notifications registered',
        'Channel notifications skipped',
        'Channel notifications skipped',
    ]
    assert 'not in --channels list for this session' in lines[1]


# --- stop, resume, fork ---------------------------------------------------------------------


def test_stop_mid_turn_and_unknown(inline: Inline) -> None:
    short = inline.spawn()
    assert inline('stop', short).out == f'stopped {short}\n'
    assert inline.session(short)['state'] == 'stopped'
    inline.play(short)  # the player starts after the stop: it must not play
    assert inline.session(short)['state'] == 'stopped'

    missing = inline('stop', 'deadbeef')
    assert (missing.code, missing.err) == (1, 'No background session matches deadbeef\n')


def test_stop_signals_a_running_player(inline: Inline, monkeypatch: pytest.MonkeyPatch) -> None:
    short = inline.spawn()
    session = inline.session(short)
    session['pid'] = 4242
    inline.store.save(session)
    signals: list[tuple[int, int]] = []

    def kill(pid: int, sig: int) -> None:
        signals.append((pid, sig))
        if sig == 0:
            raise ProcessLookupError

    monkeypatch.setattr(shim.os, 'kill', kill)
    inline('stop', short)

    assert signals == [(4242, shim.signal.SIGTERM), (4242, 0)]


def test_stop_a_player_that_already_exited(monkeypatch: pytest.MonkeyPatch) -> None:
    def gone(_pid: int, _sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(shim.os, 'kill', gone)
    shim._kill(4242)  # returns without waiting


def test_resume_a_live_session_starts_a_copy(inline: Inline) -> None:
    short = inline.spawn('--name', 't1')
    inline.play()
    original = inline.session(short)

    result = inline('--bg', '--resume', original['id'], '--', 'Follow-up: add mul')
    copy_short = inline.started[-1]

    assert result.out == f'backgrounded · {copy_short} · t1\n'
    assert result.err == (  # 2.1.296 prints its notes on stderr
        f'note: session {short} is already running in the background, '
        f'so this started a copy as {copy_short}\n'
    )
    copy = inline.session(copy_short)
    history = inline.store.transcript(copy).read_text()
    assert original['id'] not in history and copy['id'] in history
    assert len(history.splitlines()) == 78
    inline.play()
    assert len(inline.store.transcript(copy).read_text().splitlines()) == 94
    assert len(inline.store.transcript(original).read_text().splitlines()) == 78


def test_resume_a_stopped_session_wakes_it(inline: Inline, tmp_path: Path) -> None:
    short = inline.spawn('--name', 't1')
    inline.play()
    inline('stop', short)

    result = inline('--bg', '--resume', short, '--', 'Follow-up 3')

    assert result.out == f'backgrounded · {short} · t1\n'
    assert result.err.startswith(f'note: woke session {short} with its saved options (')
    inline.play()
    session = inline.session(short)
    assert (session['state'], session['status']) == ('done', 'idle')
    assert len(inline.store.transcript(session).read_text().splitlines()) == 94


def test_flags_on_a_resume_start_a_copy(inline: Inline) -> None:
    """2.1.296, recorded on the NUC: a stopped session keeps its saved options."""
    short = inline.spawn('--name', 't1')
    inline.play()
    inline('stop', short)
    result = inline('--bg', '--resume', short, '--permission-mode', 'auto', '--', 'again')
    copy_short = inline.started[-1]
    assert copy_short != short
    assert f'the flags you passed started a copy as {copy_short}' in result.err


def test_a_resume_writes_on_in_the_saved_debug_log(inline: Inline, tmp_path: Path) -> None:
    log = tmp_path / 'spawn-debug.log'
    short = inline.spawn('--name', 't1', '--debug-file', str(log))
    inline.play()
    inline('stop', short)
    before = log.read_text().count('\n')
    inline('--bg', '--resume', short, '--', 'again')
    assert log.read_text().count('\n') == before + 1


def test_fork_has_no_copy_note(inline: Inline) -> None:
    short = inline.spawn('--name', 'brain')
    result = inline('--bg', '--resume', short, '--fork-session', '--name', 'side', '--', 'hi')
    assert result.out.count('\n') == 1
    assert result.out.startswith('backgrounded · ')


def test_default_resume_steps(inline: Inline, tmp_path: Path) -> None:
    path = scenario(tmp_path, 'steps:\n  - state: done\n    status: idle\n')
    short = inline.spawn(FAKE_CLAUDE_SCENARIO=path)
    inline.play()
    inline('stop', short)
    inline('--bg', '--resume', short, '--', 'again')
    inline.play()
    assert inline.session(short)['state'] == 'done'


def test_resume_unknown(inline: Inline) -> None:
    result = inline('--bg', '--resume', 'nope', '--', 'x')
    assert (result.code, result.err) == (1, 'No conversation found with session ID: nope\n')


# --- the permission gate --------------------------------------------------------------------


def test_gate_allow_and_deny(inline: Inline) -> None:
    allowed = inline.spawn(FAKE_CLAUDE_SCENARIO='worker-needs-approval')
    denied = inline.spawn(FAKE_CLAUDE_SCENARIO='worker-needs-approval')
    shim.answer(inline.store, allowed, 'allow')
    assert inline('__answer', denied, 'deny').code == 0

    inline.play(allowed)
    inline.play(denied)

    assert inline.session(allowed)['state'] == 'done'
    assert (inline.session(denied)['state'], inline.session(denied)['status']) == (
        'working',
        'idle',
    )


def test_gate_gives_up_and_stays_blocked(inline: Inline, tmp_path: Path) -> None:
    path = scenario(
        tmp_path,
        'gate_timeout: 0.1\nsteps:\n'
        '  - state: blocked\n    status: waiting\n    waiting_for: permission prompt\n'
        '  - gate: permission\n  - state: done\n',
    )
    short = inline.spawn(FAKE_CLAUDE_SCENARIO=path)
    inline.play()
    session = inline.session(short)
    assert (session['state'], session['playing']) == ('blocked', False)


def test_answer_refusals(inline: Inline) -> None:
    short = inline.spawn()
    assert inline('__answer', short, 'maybe').code == 2
    assert inline('__answer', 'nope', 'allow').code == 1


def test_play_command_runs_the_player(inline: Inline, monkeypatch: pytest.MonkeyPatch) -> None:
    short = inline.spawn()
    monkeypatch.setattr(shim.time, 'sleep', lambda _s: None)
    assert inline('__play', short).code == 0
    assert inline.session(short)['state'] == 'done'


# --- cloud ----------------------------------------------------------------------------------


def test_cloud_needs_a_terminal(inline: Inline) -> None:
    result = inline('--cloud', 'Fix the login test')
    assert result.code == 1
    assert result.err == (FIXTURES / 'cloud' / 'create-no-tty-refused.out').read_text()


def test_cloud_cannot_combine_with_print(inline: Inline) -> None:
    result = inline('-p', 'Fix it', '--cloud', '--output-format', 'json')
    assert result.code == 1
    assert result.err == (FIXTURES / 'cloud' / 'create-p-refused.out').read_text()


def test_cloud_create_then_send(inline: Inline) -> None:
    inline.tty = True
    created = inline('--cloud', 'SP3 cloud spike: post a report')

    assert created.code == 0
    found = re.search(r'session_[A-Za-z0-9]+', created.out)
    assert found
    cloud_id = found.group(0)
    url = f'https://claude.ai/code/{cloud_id}?from=cli&m=0'
    assert created.out == (
        f'Created cloud session: SP3 cloud spike: post a report View: {url} '
        f'Resume with: claude --teleport {cloud_id} \x0f\x1b7\x1b8\n'
    )

    sent = inline('-p', 'POST again', '--cloud', cloud_id, '--output-format', 'json')
    ack = {'ok': True, 'session_id': cloud_id, 'url': url}
    assert sent.out == json.dumps(ack, separators=(',', ':')) + '\n'
    record = json.loads(inline.store.cloud_file(cloud_id).read_text())
    assert record['messages'] == ['POST again']


def test_cloud_send_matches_the_recorded_shape(inline: Inline, tmp_path: Path) -> None:
    inline.tty = True
    path = scenario(
        tmp_path,
        'cloud:\n  session_id: session_0168oBNCs8Q2BrHDFu6tYUdh\n  title: SP3 cloud spike\n',
    )
    created = inline('--cloud', 'x', FAKE_CLAUDE_SCENARIO=path)
    recorded = (FIXTURES / 'cloud' / 'create-pty-output.txt').read_text()
    assert created.out == recorded

    sent = inline(
        '-p', 'm', '--cloud', 'session_0168oBNCs8Q2BrHDFu6tYUdh', '--output-format', 'json'
    )
    assert sent.out == (FIXTURES / 'cloud' / 'followup-p-json.out').read_text()


def test_cloud_output_override_and_refusals(inline: Inline, tmp_path: Path) -> None:
    inline.tty = True
    path = scenario(tmp_path, 'cloud:\n  output: Something went sideways\n')
    garbled = inline('--cloud', 'x', FAKE_CLAUDE_SCENARIO=path)
    assert garbled.out == 'Something went sideways\n'

    unknown = inline('-p', 'm', '--cloud', 'session_nope', '--output-format', 'json')
    assert unknown.code == 1 and 'no cloud session session_nope' in unknown.err

    created = inline('--cloud', 'y')
    cloud_id = re.findall(r'session_[A-Za-z0-9]+', created.out)[0]
    text = inline('-p', 'm', '--cloud', cloud_id)
    assert text.code == 2 and 'is not faked' in text.err


# --- usage ----------------------------------------------------------------------------------


def test_usage_text_and_json(inline: Inline) -> None:
    recorded = (FIXTURES / 'usage' / 'usage-p-text.txt').read_text()

    text = inline('-p', '/usage', '--no-session-persistence')
    as_json = inline('-p', '/usage', '--output-format', 'json')

    assert text.out == recorded
    assert json.loads(as_json.out)['result'] == recorded.rstrip('\n')
    line = re.compile(
        r'^Current (session|week \(all models\)): (\d+)% used · resets (.+) \((\w+)\)$'
    )
    assert [m.group(2) for m in map(line.match, text.out.splitlines()) if m] == ['4', '66']


def test_usage_from_a_scenario(inline: Inline, tmp_path: Path) -> None:
    custom = FIXTURES / 'usage' / 'usage-p-text.txt'
    path = scenario(tmp_path, f'usage: {custom.relative_to(FIXTURES)}\n')
    inline.store.claim()
    (inline.store.root / 'scenario').write_text(f'{path}\nworker-fails\n')

    assert inline('-p', '/usage').out == custom.read_text()
    assert (inline.store.root / 'scenario').read_text() == f'{path}\nworker-fails\n'


# --- scenario validation --------------------------------------------------------------------


@pytest.mark.parametrize(
    ('text', 'message'),
    [
        ('- 1\n', 'a scenario is a mapping'),
        ('colour: red\n', 'unknown scenario key(s) colour'),
        ('steps: 3\n', '`steps` is a list of steps'),
        ('steps:\n  - 3\n', 'step must be a mapping'),
        ('steps:\n  - state: sleeping\n', "state 'sleeping' is not one of"),
        ('steps:\n  - state: done\n    status: napping\n', "status 'napping' is not one of"),
        ('steps:\n  - job:\n      colour: red\n', 'unknown job key(s) colour'),
        ('steps:\n  - gate: coffee\n', 'the only gate is `permission`'),
        ('cloud:\n  colour: red\n', 'unknown cloud key(s) colour'),
        ('transcript: transcripts/nope.jsonl\n', "no fixture 'transcripts/nope.jsonl'"),
        ('job: nope\n', "no job fixture 'nope'"),
        ('steps:\n  - state: [1]\n', 'quote this value'),
    ],
)
def test_bad_scenarios_are_readable(
    inline: Inline, tmp_path: Path, text: str, message: str
) -> None:
    result = inline('--bg', '--', 'x', FAKE_CLAUDE_SCENARIO=scenario(tmp_path, text))
    assert result.code == 2
    assert result.err.startswith('fake claude: bad scenario: ')
    assert message in result.err


def test_missing_scenario(inline: Inline) -> None:
    result = inline('--bg', '--', 'x', FAKE_CLAUDE_SCENARIO='no-such-scenario')
    assert result.code == 2 and "no scenario 'no-such-scenario'" in result.err


def test_project_dir_matches_claude_code() -> None:
    assert shim.project_dir('/home/shbunder/projects/marcel-src') == (
        '-home-shbunder-projects-marcel-src'
    )
    assert shim.project_dir('/tmp/work_tree.1') == '-tmp-work-tree-1'


def test_the_saved_options_have_the_recorded_shape(inline: Inline, tmp_path: Path) -> None:
    """The fake saves the options 2.1.296 saved on the NUC (fixtures/jobs/respawn-2.1.296)."""
    recorded = json.loads(
        (Path(__file__).parent / 'fixtures' / 'jobs' / 'respawn-2.1.296' / 'state.json').read_text()
    )['respawnFlags']
    argv = ['--setting-sources', 'project,local', '--name', 'w', '--permission-mode', 'manual']
    argv += ['--channels', 'plugin:marcel@marcel-local', '--allowedTools', 'a', 'b']
    argv += ['--debug-file', str(tmp_path / 'd.log'), '--model', 'haiku']
    short = inline.spawn(*argv)
    job = inline.store.job_dir(short) / 'state.json'
    flags = json.loads(job.read_text())['respawnFlags']
    assert [f for f in flags if f.startswith('--')] == [f for f in recorded if f.startswith('--')]
