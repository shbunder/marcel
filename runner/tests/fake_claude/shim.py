"""The fake `claude`: a stand-in for the Claude Code CLI that replays recorded spike output.

It fakes only what the spikes recorded (SP1, SP2, SP3, SP4, SP7): `--bg` (and `--resume`,
`--fork-session`), `agents --json [--all]`, `stop`, `attach` (a no-op), `--cloud`,
`-p --cloud <id>`, `-p /usage` and `--version`. Anything else exits 2 with "not faked", so a
runner that drifts from what was recorded fails loudly instead of passing against a guess.

A background session is played by a detached player process (`claude __play <short-id>`) that
walks the scenario's steps over time and writes what real `claude` would: the transcript
under `~/.claude/projects/`, the job files under `~/.claude/jobs/<short-id>/`, and the row that
`agents --json` prints. The shim never imports a network module; see README.md.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import signal
import string
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from fake_claude import miniyaml

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / 'fixtures'
SCENARIOS = HERE / 'scenarios'
EXECUTABLE = HERE / 'bin' / 'claude'
VERSION = '2.1.289'
MARKER = '.fake-claude'
DEFAULT_SCENARIO = 'worker-happy-path'
STATES = ('working', 'blocked', 'done', 'failed', 'stopped')
STATUSES = ('busy', 'waiting', 'idle')
POLL = 0.05

# What real claude printed, verbatim (SP1, SP2, SP3).
UNTRUSTED = 'Workspace not trusted. Run claude in {cwd} once and accept the trust prompt'
CHANNEL_REGISTERED = 'MCP server "plugin:marcel:marcel": Channel notifications registered'
CHANNEL_SKIPPED = (
    'MCP server "plugin:marcel:marcel": Channel notifications skipped: '
    'server plugin:marcel:marcel not in --channels list for this session'
)
CLOUD_NO_TTY = (
    'Error: --cloud requires an interactive terminal.\n'
    'Non-interactive invocations (piped stdout, --init-only, --sdk-url) run locally and would '
    'silently ignore --cloud. Drop --cloud, or run from a TTY.'
)
CLOUD_WITH_PRINT = (
    'Error: --cloud cannot be combined with --print.\n'
    'Starting a new cloud session with --cloud is interactive only: drop --print, or drop --cloud '
    'to run locally. To message an existing cloud session instead, pass its ID: '
    '`claude -p "message" --cloud <session-id>` (find IDs at claude.ai/code).'
)
CLOUD_URL = 'https://claude.ai/code/{id}?from=cli&m=0'
# The pty showed these control bytes after the line (SP3 evidence/create-pty-output.txt).
CLOUD_TAIL = ' \x0f\x1b7\x1b8\n'

VALUE_FLAGS = {
    '--name',
    '-n',
    '--model',
    '--permission-mode',
    '--debug-file',
    '--resume',
    '-r',
    '--settings',
    '--setting-sources',
    '--output-format',
    '--mcp-config',
    '--append-system-prompt',
}
VARIADIC_FLAGS = {
    '--channels',
    '--allowedTools',
    '--allowed-tools',
    '--disallowedTools',
    '--disallowed-tools',
    '--dangerously-load-development-channels',
    '--add-dir',
}
BOOL_FLAGS = {
    '--bg',
    '-p',
    '--print',
    '--fork-session',
    '--no-session-persistence',
    '--json',
    '--all',
    '--verbose',
}
TOP_KEYS = {
    'description',
    'name',
    'session_id',
    'transcript',
    'job',
    'trusted',
    'channel',
    'resume_channel',
    'gate_timeout',
    'steps',
    'denied',
    'resume',
    'cloud',
    'usage',
}
STEP_KEYS = {
    'wait',
    'state',
    'status',
    'waiting_for',
    'transcript',
    'subagents',
    'job',
    'text',
    'gate',
}
JOB_KEYS = {'state', 'tempo', 'detail', 'needs', 'result'}
CLOUD_KEYS = {'session_id', 'title', 'output'}
DEFAULT_RESUME = [
    {'wait': 0.05, 'state': 'working', 'status': 'busy'},
    {'wait': 0.1, 'state': 'done', 'status': 'idle'},
]

Session = dict[str, Any]
Scenario = dict[str, Any]


class Refused(Exception):
    """Exit with a readable message on stderr, like real claude does."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ScenarioError(ValueError):
    pass


def not_faked(argv: list[str]) -> Refused:
    shown = ' '.join(argv) or '(no arguments)'
    return Refused(
        f'fake claude: `claude {shown}` is not faked. It fakes only what the spikes recorded; '
        'see runner/tests/fake_claude/README.md.',
        2,
    )


# --- arguments ------------------------------------------------------------------------------


@dataclass
class Args:
    flags: dict[str, Any] = field(default_factory=dict)
    words: list[str] = field(default_factory=list)

    def has(self, *names: str) -> bool:
        return any(n in self.flags for n in names)

    def value(self, *names: str) -> Any:
        for n in names:
            if n in self.flags:
                return self.flags[n]
        return None


def parse(argv: list[str]) -> Args:
    args = Args()
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == '--':
            args.words.extend(argv[i + 1 :])
            break
        if token == '--cloud':
            nxt = argv[i + 1] if i + 1 < len(argv) else ''
            has_id = nxt.startswith('session_')
            args.flags['--cloud'] = nxt if has_id else True
            i += 2 if has_id else 1
            continue
        if token in VALUE_FLAGS:
            if i + 1 >= len(argv):
                raise Refused(f'error: option {token} needs a value', 1)
            args.flags[token] = argv[i + 1]
            i += 2
            continue
        if token in VARIADIC_FLAGS:
            values: list[str] = []
            i += 1
            while i < len(argv) and not argv[i].startswith('-'):
                values.append(argv[i])
                i += 1
            args.flags[token] = values
            continue
        if token in BOOL_FLAGS:
            args.flags[token] = True
        elif token.startswith('-'):
            raise not_faked(argv)
        else:
            args.words.append(token)
        i += 1
    return args


# --- scenarios ------------------------------------------------------------------------------


def scenario_path(ref: str) -> Path:
    if ref.endswith(('.yaml', '.yml')) or '/' in ref:
        return Path(ref)
    return SCENARIOS / f'{ref}.yaml'


def load_scenario(ref: str) -> Scenario:
    path = scenario_path(ref)
    if not path.is_file():
        raise ScenarioError(f'no scenario {ref!r} (looked for {path})')
    data = miniyaml.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ScenarioError(f'{path.name}: a scenario is a mapping')
    _check_keys(path.name, 'scenario', data, TOP_KEYS)
    for key in ('steps', 'denied', 'resume'):
        steps = data.get(key) or []
        if not isinstance(steps, list):
            raise ScenarioError(f'{path.name}: `{key}` is a list of steps')
        for n, step in enumerate(steps, 1):
            _check_step(f'{path.name} {key}[{n}]', step)
    if isinstance(data.get('cloud'), dict):
        _check_keys(path.name, 'cloud', data['cloud'], CLOUD_KEYS)
    for key in ('transcript', 'usage'):
        if data.get(key) and not (FIXTURES / data[key]).is_file():
            raise ScenarioError(f'{path.name}: no fixture {data[key]!r}')
    if data.get('job') and not (FIXTURES / 'jobs' / data['job'] / 'state.json').is_file():
        raise ScenarioError(f'{path.name}: no job fixture {data["job"]!r}')
    data['ref'] = str(path)
    return data


def _check_keys(where: str, what: str, data: Any, allowed: set[str]) -> None:
    if not isinstance(data, dict):
        raise ScenarioError(f'{where}: {what} must be a mapping')
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ScenarioError(f'{where}: unknown {what} key(s) {", ".join(unknown)}')


def _check_step(where: str, step: Any) -> None:
    _check_keys(where, 'step', step, STEP_KEYS)
    if step.get('state') is not None and step['state'] not in STATES:
        raise ScenarioError(f'{where}: state {step["state"]!r} is not one of {", ".join(STATES)}')
    if step.get('status') is not None and step['status'] not in STATUSES:
        raise ScenarioError(f'{where}: status {step["status"]!r} is not one of {STATUSES}')
    if step.get('job') is not None:
        _check_keys(where, 'job', step['job'], JOB_KEYS)
    if 'gate' in step and step['gate'] != 'permission':
        raise ScenarioError(f'{where}: the only gate is `permission`')


# --- the fake ~/.claude ---------------------------------------------------------------------


def project_dir(cwd: str) -> str:
    """Claude Code's project folder name: every character that is not a letter or digit → `-`."""
    return re.sub(r'[^A-Za-z0-9]', '-', cwd)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(tmp, path)


class Store:
    """The fake's state, kept under `$HOME/.claude/` next to the files real claude writes."""

    def __init__(self, home: Path) -> None:
        self.claude = home / '.claude'
        self.root = self.claude / 'fake-claude'

    @classmethod
    def open(cls, env: dict[str, str]) -> Store:
        home = env.get('HOME')
        if not home:
            raise Refused('fake claude: HOME is not set, so there is nowhere to write.')
        store = cls(Path(home))
        store.claim()
        return store

    def claim(self) -> None:
        """Refuse a real Claude Code home: the fake must never write fake sessions into it."""
        marker = self.claude / MARKER
        if self.claude.is_dir() and not marker.exists() and any(self.claude.iterdir()):
            raise Refused(
                f'fake claude: {self.claude} looks like a real Claude Code home, so the fake will '
                'not write there. Point HOME at a scratch folder (scripts/fake-claude does this).'
            )
        self.root.mkdir(parents=True, exist_ok=True)
        marker.touch()

    # sessions
    def session_file(self, short: str) -> Path:
        return self.root / 'sessions' / f'{short}.json'

    def load(self, short: str) -> Session:
        return json.loads(self.session_file(short).read_text(encoding='utf-8'))

    def save(self, session: Session) -> None:
        _write_json(self.session_file(session['short']), session)

    def sessions(self) -> list[Session]:
        folder = self.root / 'sessions'
        found = [json.loads(p.read_text('utf-8')) for p in folder.glob('*.json')]
        return sorted(found, key=lambda s: (s['started_at'], s['short']))

    def find(self, ref: str) -> Session | None:
        for s in self.sessions():
            if ref in (s['id'], s['short']):
                return s
        return None

    # answers to a permission gate
    def answer_file(self, short: str) -> Path:
        return self.root / 'answers' / short

    # where real claude writes
    def transcript(self, session: Session) -> Path:
        return self.claude / 'projects' / project_dir(session['cwd']) / f'{session["id"]}.jsonl'

    def job_dir(self, short: str) -> Path:
        return self.claude / 'jobs' / short

    # cloud sessions
    def cloud_file(self, cloud_id: str) -> Path:
        return self.root / 'cloud' / f'{cloud_id}.json'

    # which scenario the next spawn plays
    def next_scenario(self, env: dict[str, str], consume: bool = True) -> str:
        """`FAKE_CLAUDE_SCENARIO`, else the first line of `fake-claude/scenario` (each spawn
        takes one line; the last line stays), else the default."""
        if env.get('FAKE_CLAUDE_SCENARIO'):
            return env['FAKE_CLAUDE_SCENARIO']
        queue = self.root / 'scenario'
        if not queue.is_file():
            return DEFAULT_SCENARIO
        lines = [ln.strip() for ln in queue.read_text('utf-8').splitlines() if ln.strip()]
        if not lines:
            return DEFAULT_SCENARIO
        if consume and len(lines) > 1:
            queue.write_text('\n'.join(lines[1:]) + '\n', encoding='utf-8')
        return lines[0]


# --- the agents --json row ------------------------------------------------------------------


def row(s: Session) -> dict[str, Any]:
    """One `agents --json` row, with the keys in the order real claude prints them (SP2)."""
    out: dict[str, Any] = {}
    if s.get('status'):
        out['pid'] = s['pid']
    out.update(
        id=s['short'],
        cwd=s['cwd'],
        kind='background',
        startedAt=s['started_at'],
        sessionId=s['id'],
        name=s['name'],
    )
    if s.get('status'):
        out['status'] = s['status']
    if s.get('waiting_for'):
        out['waitingFor'] = s['waiting_for']
    out['state'] = s['state']
    return out


def listed(s: Session, show_all: bool) -> bool:
    """Without `--all`, a session with no process that has finished or stopped is left out."""
    return show_all or bool(s.get('status')) or s['state'] in ('working', 'blocked')


# --- spawning -------------------------------------------------------------------------------


def start_player(store: Store, short: str, cwd: str) -> None:
    """Start the detached player that replays the session's scenario over time."""
    log = store.root / 'player.log'
    with log.open('ab') as err:
        subprocess.Popen(
            [sys.executable, str(EXECUTABLE), '__play', short],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err,
            start_new_session=True,
            close_fds=True,
        )


def _first_field(path: Path, key: str) -> str:
    with path.open(encoding='utf-8') as f:
        for line in f:
            value = json.loads(line).get(key)
            if isinstance(value, str):
                return value
    return ''


def _short_name(prompt: str) -> str:
    return ' '.join(prompt.split()[:4]) or 'session'


def new_session(sc: Scenario, cwd: str, name: str, prompt: str, store: Store) -> Session:
    wanted = sc.get('session_id')
    sid = wanted if wanted and not store.find(wanted) else str(uuid.uuid4())
    src = FIXTURES / sc['transcript'] if sc.get('transcript') else None
    return {
        'id': sid,
        'short': sid[:8],
        'name': name,
        'cwd': cwd,
        'prompt': prompt,
        'scenario': sc['ref'],
        'plan': 'steps',
        'started_at': int(time.time() * 1000),
        'state': 'working',
        'status': None,
        'waiting_for': None,
        'pid': None,
        'playing': True,
        'transcript_src': str(src) if src else None,
        'src_id': _first_field(src, 'sessionId') if src else '',
        'src_cwd': _first_field(src, 'cwd') if src else '',
        'written': 0,
    }


def write_debug_file(path: str, channels: list[str], sc: Scenario) -> None:
    registered = 'plugin:marcel@marcel-local' in channels and sc.get('channel') != 'skipped'
    line = CHANNEL_REGISTERED if registered else CHANNEL_SKIPPED
    debug = Path(path)
    debug.parent.mkdir(parents=True, exist_ok=True)
    with debug.open('a', encoding='utf-8') as f:
        f.write(f'{_now_iso()} [DEBUG] {line}\n')


def write_job(store: Store, s: Session, sc: Scenario) -> None:
    base: dict[str, Any] = {}
    if sc.get('job'):
        base = json.loads((FIXTURES / 'jobs' / sc['job'] / 'state.json').read_text('utf-8'))
    base.pop('needs', None)
    base.update(
        state='working',
        tempo='working',
        detail='',
        output=None,
        intent=s['prompt'],
        name=s['name'],
        sessionId=s['id'],
        resumeSessionId=s['id'],
        daemonShort=s['short'],
        cliVersion=VERSION,
        cwd=s['cwd'],
        linkScanPath=str(store.transcript(s)),
        createdAt=_now_iso(),
        updatedAt=_now_iso(),
        firstTerminalAt=None,
        # What Claude saves to start the session again (2.1.296); a resume without flags uses it.
        respawnFlags=s.get('saved') or ['--name', s['name']],
    )
    base.pop('lastTerminalAt', None)
    _write_json(store.job_dir(s['short']) / 'state.json', base)
    (store.job_dir(s['short']) / 'timeline.jsonl').touch()


def cmd_bg(args: Args, store: Store, env: dict[str, str], out: IO[str], err: IO[str]) -> int:
    cwd = os.getcwd()
    prompt = ' '.join(args.words)
    if args.has('--resume', '-r'):
        return cmd_resume(args, store, cwd, prompt, out, err)
    if not prompt:
        raise Refused(
            'fake claude: --bg needs a prompt. Put it after `--`: a prompt after --channels is '
            'read as a channel name (SP1).'
        )
    sc = load_scenario(store.next_scenario(env))
    if sc.get('trusted') is False:
        err.write(UNTRUSTED.format(cwd=cwd) + '\n')
        return 1
    name = args.value('--name', '-n') or sc.get('name') or _short_name(prompt)
    s = new_session(sc, cwd, name, prompt, store)
    # Claude keeps a session's options and reuses them on a resume without flags (2.1.296).
    s['debug_file'] = args.value('--debug-file')
    s['channels'] = args.value('--channels') or []
    s['saved'] = saved_options(args, name)
    if s['debug_file']:
        write_debug_file(s['debug_file'], s['channels'], sc)
    write_job(store, s, sc)
    store.save(s)
    start_player(store, s['short'], cwd)
    out.write(f'backgrounded · {s["short"]} · {name}\n')
    return 0


def saved_options(args: Args, name: str) -> list[str]:
    """What 2.1.296 saves to start a session again, in the order it lists them (recorded on the
    NUC: fixtures/jobs/respawn-2.1.296/state.json)."""
    out: list[str] = []
    for flag in SAVED_OPTIONS:
        value = name if flag == '--name' else args.value(flag)
        if value is None:
            continue
        out += [flag, *value] if isinstance(value, list) else [flag, str(value)]
    return out


SAVED_OPTIONS = (
    '--setting-sources',
    '--name',
    '--permission-mode',
    '--channels',
    '--allowedTools',
    '--debug-file',
    '--model',
)


def cmd_resume(args: Args, store: Store, cwd: str, prompt: str, out: IO[str], err: IO[str]) -> int:
    ref = args.value('--resume', '-r')
    old = store.find(ref)
    if old is None:
        raise Refused(f'No conversation found with session ID: {ref}')
    sc = load_scenario(old['scenario'])
    if args.value('--debug-file'):
        write_debug_file(args.value('--debug-file'), args.value('--channels') or [], sc)
    # 2.1.296: a stopped session keeps its own saved options, so flags on a plain resume start a
    # copy (recorded on the NUC, 2026-10-13; the note is on stderr).
    flagged = not args.has('--fork-session') and args.has(*SAVED_OPTIONS)
    if args.has('--fork-session') or old.get('status') or flagged:
        s = dict(old)
        s.update(
            id=str(uuid.uuid4()),
            cwd=cwd,
            prompt=prompt,
            plan='resume',
            started_at=int(time.time() * 1000),
            state='working',
            status=None,
            waiting_for=None,
            pid=None,
            playing=True,
        )
        s['short'] = s['id'][:8]
        s['src_id'] = old['src_id']
        if args.value('--debug-file'):  # a fork or a copy keeps the options it was given
            s['debug_file'] = args.value('--debug-file')
            s['channels'] = args.value('--channels') or []
        s['saved'] = saved_options(args, args.value('--name', '-n') or s['name'])
        if store.transcript(old).is_file():
            history = store.transcript(old).read_text('utf-8').replace(old['id'], s['id'])
            store.transcript(s).parent.mkdir(parents=True, exist_ok=True)
            store.transcript(s).write_text(history, encoding='utf-8')
        write_job(store, s, sc)
        store.save(s)
        start_player(store, s['short'], cwd)
        out.write(f'backgrounded · {s["short"]} · {s["name"]}\n')
        if flagged:
            err.write(
                f'note: background session {old["short"]} keeps its own saved options, so the '
                f'flags you passed started a copy as {s["short"]}. Without flags, the same '
                f'command continues {old["short"]} itself.\n'
            )
        elif not args.has('--fork-session'):
            err.write(
                f'note: session {old["short"]} is already running in the background, '
                f'so this started a copy as {s["short"]}\n'
            )
        return 0
    old.update(
        plan='resume', prompt=prompt, state='working', status=None, waiting_for=None, playing=True
    )
    if old.get('debug_file'):
        lost = sc.get('resume_channel') == 'skipped'
        write_debug_file(old['debug_file'], [] if lost else old.get('channels') or [], sc)
    store.save(old)
    start_player(store, old['short'], old['cwd'])
    out.write(f'backgrounded · {old["short"]} · {old["name"]}\n')
    err.write(
        f'note: woke session {old["short"]} with its saved options ({", ".join(SAVED_OPTIONS)}).\n'
    )
    return 0


# --- the player -----------------------------------------------------------------------------


def play(store: Store, short: str, sleep: Callable[[float], None] = time.sleep) -> None:
    """Walk the session's scenario: the steps on a spawn, the `resume` steps on a resume."""
    s = store.load(short)
    if not s.get('playing'):
        return  # stopped before the player started
    sc = load_scenario(s['scenario'])
    s['pid'] = os.getpid()
    store.save(s)
    try:
        if s['plan'] == 'resume':
            _run(store, short, sc, sc.get('resume') or DEFAULT_RESUME, sleep)
        else:
            _run(store, short, sc, sc.get('steps') or [], sleep)
    finally:
        s = store.load(short)
        s['playing'] = False
        store.save(s)


def _run(
    store: Store,
    short: str,
    sc: Scenario,
    steps: list[dict[str, Any]],
    sleep: Callable[[float], None],
) -> None:
    for step in steps:
        if 'gate' in step:
            answer = wait_answer(store, short, float(sc.get('gate_timeout') or 60), sleep)
            if answer is None:
                return
            if answer == 'deny':
                _run(store, short, sc, sc.get('denied') or [], sleep)
                return
            continue
        sleep(float(step.get('wait') or 0))
        apply(store, short, step)


def wait_answer(
    store: Store, short: str, timeout: float, sleep: Callable[[float], None]
) -> str | None:
    """Hold at a permission gate until `answer()` writes allow or deny, or give up."""
    path = store.answer_file(short)
    waited = 0.0
    while waited < timeout:
        if path.is_file():
            answer = path.read_text('utf-8').strip()
            path.unlink()
            return answer
        sleep(POLL)
        waited += POLL
    return None


def apply(store: Store, short: str, step: dict[str, Any]) -> None:
    s = store.load(short)
    if step.get('state'):
        s['state'] = step['state']
        s['status'] = step.get('status')
        s['waiting_for'] = step.get('waiting_for')
        if s['status'] and not s.get('pid'):
            s['pid'] = os.getpid()
    if step.get('transcript') is not None:
        write_transcript(store, s, step['transcript'])
    if step.get('subagents'):
        copy_subagents(store, s)
    if step.get('job') is not None or step.get('text'):
        update_job(store, s, step.get('job') or {}, step.get('text') or '')
    store.save(s)


def write_transcript(store: Store, s: Session, upto: Any) -> None:
    if not s.get('transcript_src'):
        return
    lines = Path(s['transcript_src']).read_text('utf-8').splitlines()
    end = len(lines) if upto == 'end' else min(int(upto), len(lines))
    if end <= s['written']:
        return
    old_cwd, new_cwd = json.dumps(s['src_cwd'])[1:-1], json.dumps(s['cwd'])[1:-1]
    chunk = ''.join(
        line.replace(s['src_id'], s['id']).replace(old_cwd, new_cwd) + '\n'
        for line in lines[s['written'] : end]
    )
    path = store.transcript(s)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(chunk)
    s['written'] = end


def copy_subagents(store: Store, s: Session) -> None:
    if not s.get('transcript_src'):
        return
    src = Path(s['transcript_src']).with_suffix('') / 'subagents'
    if src.is_dir():
        dest = store.transcript(s).with_suffix('') / 'subagents'
        shutil.copytree(src, dest, dirs_exist_ok=True)


def update_job(store: Store, s: Session, job: dict[str, Any], text: str) -> None:
    path = store.job_dir(s['short']) / 'state.json'
    state = json.loads(path.read_text('utf-8'))
    for key, value in job.items():
        if key == 'result':
            state['output'] = {'result': value}
        elif value is None:
            state.pop(key, None)
        else:
            state[key] = value
    state['updatedAt'] = _now_iso()
    _write_json(path, state)
    entry = {'at': state['updatedAt'], 'state': state.get('state'), 'detail': state.get('detail')}
    entry['text'] = text
    with (path.parent / 'timeline.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False, separators=(',', ':')) + '\n')


# --- stop, attach, answer -------------------------------------------------------------------


def cmd_stop(ref: str, store: Store, out: IO[str]) -> int:
    s = store.find(ref)
    if s is None:
        raise Refused(f'No background session matches {ref}')
    if s.get('playing') and s.get('pid'):
        _kill(int(s['pid']))
        s = store.load(s['short'])
    if s['state'] in ('working', 'blocked'):
        s['state'] = 'stopped'
    s.update(status=None, waiting_for=None, playing=False)
    store.save(s)
    out.write(f'stopped {s["short"]}\n')
    return 0


def _kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)


def answer(store: Store, ref: str, decision: str) -> None:
    """Answer the permission prompt a session is held at, as the channel relay would."""
    if decision not in ('allow', 'deny'):
        raise Refused(f'fake claude: answer with allow or deny, not {decision!r}', 2)
    s = store.find(ref)
    if s is None:
        raise Refused(f'No background session matches {ref}')
    path = store.answer_file(s['short'])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(decision, encoding='utf-8')


# --- cloud and print mode -------------------------------------------------------------------


def cmd_cloud(
    args: Args,
    store: Store,
    env: dict[str, str],
    out: IO[str],
    err: IO[str],
    isatty: Callable[[], bool],
) -> int:
    target = args.value('--cloud')
    if args.has('-p', '--print'):
        if target is True:
            err.write(CLOUD_WITH_PRINT + '\n')
            return 1
        path = store.cloud_file(target)
        if not path.is_file():
            raise Refused(f'fake claude: no cloud session {target} was created through the fake')
        if args.value('--output-format') != 'json':
            raise not_faked(['-p', '--cloud', target, '(without --output-format json)'])
        record = json.loads(path.read_text('utf-8'))
        record['messages'].append(' '.join(args.words))
        _write_json(path, record)
        ack = {'ok': True, 'session_id': target, 'url': record['url']}
        out.write(json.dumps(ack, separators=(',', ':')) + '\n')
        return 0
    if not isatty():
        err.write(CLOUD_NO_TTY + '\n')
        return 1
    prompt = ' '.join(args.words)
    sc = load_scenario(store.next_scenario(env))
    cloud = sc.get('cloud') or {}
    alphabet = string.ascii_letters + string.digits
    cloud_id = cloud.get('session_id') or 'session_01' + ''.join(random.choices(alphabet, k=22))
    title = cloud.get('title') or ' '.join(prompt.split()[:6]) or 'Cloud session'
    url = CLOUD_URL.format(id=cloud_id)
    record = {'id': cloud_id, 'title': title, 'url': url, 'prompt': prompt, 'messages': []}
    _write_json(store.cloud_file(cloud_id), record)
    if cloud.get('output') is not None:
        out.write(str(cloud['output']) + '\n')
    else:
        out.write(
            f'Created cloud session: {title} View: {url} '
            f'Resume with: claude --teleport {cloud_id}{CLOUD_TAIL}'
        )
    return 0


def cmd_print(args: Args, store: Store, env: dict[str, str], out: IO[str], argv: list[str]) -> int:
    if ' '.join(args.words).strip() != '/usage':
        raise not_faked(argv)
    usage = (
        load_scenario(store.next_scenario(env, consume=False)).get('usage')
        or 'usage/usage-p-text.txt'
    )
    text = (FIXTURES / usage).read_text('utf-8')
    if args.value('--output-format') == 'json':
        record = json.loads((FIXTURES / 'usage' / 'usage-p-json.json').read_text('utf-8'))
        record['result'] = text.rstrip('\n')
        out.write(json.dumps(record) + '\n')
    else:
        out.write(text)
    return 0


# --- entry point ----------------------------------------------------------------------------


def main(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
    isatty: Callable[[], bool] | None = None,
) -> int:
    env = dict(os.environ) if env is None else env
    out = out or sys.stdout
    err = err or sys.stderr
    isatty = isatty or sys.stdout.isatty
    try:
        return _dispatch(argv, env, out, err, isatty)
    except Refused as e:
        err.write(e.message + '\n')
        return e.code
    except (ScenarioError, miniyaml.YamlError) as e:
        err.write(f'fake claude: bad scenario: {e}\n')
        return 2


def _dispatch(
    argv: list[str],
    env: dict[str, str],
    out: IO[str],
    err: IO[str],
    isatty: Callable[[], bool],
) -> int:
    if argv in (['--version'], ['-v']):
        out.write(f'{VERSION} (Claude Code)\n')
        return 0
    command = argv[0] if argv else ''
    if command == 'attach':
        return 0
    store = Store.open(env)
    if command == '__play' and len(argv) == 2:
        play(store, argv[1])
        return 0
    if command == '__answer' and len(argv) == 3:
        answer(store, argv[1], argv[2])
        return 0
    if command == 'agents':
        args = parse(argv[1:])
        if not args.has('--json') or args.words:
            raise not_faked(argv)
        rows = [row(s) for s in store.sessions() if listed(s, args.has('--all'))]
        out.write(json.dumps(rows) + '\n')
        return 0
    if command == 'stop' and len(argv) == 2:
        return cmd_stop(argv[1], store, out)
    args = parse(argv)
    if args.has('--cloud'):
        return cmd_cloud(args, store, env, out, err, isatty)
    if args.has('--bg'):
        return cmd_bg(args, store, env, out, err)
    if args.has('-p', '--print'):
        return cmd_print(args, store, env, out, argv)
    raise not_faked(argv)
