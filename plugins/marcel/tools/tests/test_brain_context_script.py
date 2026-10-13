"""`plugins/marcel/scripts/brain_context.py`, the plugin's `SessionStart` and `PreCompact` hooks
(S-06.8).

It is a standalone file (standard library only), so it is loaded by path. Its calls go to the real
hub app in-process (`world`); the summary route the hub does not have yet is faked on top of it.
Nothing reaches a network or `claude`."""

import importlib.util
import io
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from conftest import SECRET, ClientTransport, World

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'brain_context.py'
HOOKS = Path(__file__).resolve().parents[2] / 'hooks' / 'hooks.json'
TRANSCRIPT = (
    Path(__file__).resolve().parents[4] / 'contracts' / 'tests' / 'fixtures' / 'transcripts'
)


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location('marcel_brain_context_script', SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ctx = load()
NOTES = '# Marcel’s notes\n\nThe kids eat at six.'
NOW = datetime(2026, 10, 13, 20, 15, tzinfo=UTC)


class WithSummary:
    """The hub as it will be once `GET /brain/summary` exists (contract request S-06.8)."""

    def __init__(self, inner: ClientTransport) -> None:
        self.inner = inner
        self.paths: list[str] = []

    def request(self, method: str, path: str, **kw: Any) -> tuple[int, Any]:
        self.paths.append(path)
        if path == '/brain/summary':
            return 200, {'date': '2026-10-13', 'text': 'Shaun asked about the NUC disk.'}
        return self.inner.request(method, path, **kw)


class Down:
    def request(self, method: str, path: str, **kw: Any) -> tuple[int, Any]:
        raise ConnectionRefusedError(61, 'Connection refused')


class Answers:
    def __init__(self, status: int, body: Any) -> None:
        self.status, self.body = status, body

    def request(self, method: str, path: str, **kw: Any) -> tuple[int, Any]:
        return self.status, self.body


@pytest.fixture
def cwd(tmp_path: Path) -> Path:
    folder = tmp_path / 'brain'
    folder.mkdir()
    (folder / 'MEMORY.md').write_text(NOTES + '\n')
    return folder


def env(world: World) -> dict[str, str]:
    return {'CLAUDE_PLUGIN_DATA': str(world.data_dir)}


def start(session_id: str, cwd: Path, source: str = 'compact') -> dict[str, Any]:
    return {'hook_event_name': 'SessionStart', 'session_id': session_id, 'cwd': str(cwd),
            'source': source}  # fmt: skip


def pre(
    session_id: str, cwd: Path, transcript: Path | None, trigger: str = 'auto'
) -> dict[str, Any]:
    path = str(transcript) if transcript else None
    return {'hook_event_name': 'PreCompact', 'session_id': session_id, 'cwd': str(cwd),
            'trigger': trigger, 'transcript_path': path}  # fmt: skip


def spawn(world: World, title: str = 'Check disk usage') -> str:
    text = world.ok('tasks_spawn', {'title': title, 'location': 'nuc', 'prompt': 'Look.'})
    return json.loads(text.split('\n\n', 1)[1])['id']


def transcript(tmp_path: Path, lines: list[Any]) -> Path:
    path = tmp_path / 'session.jsonl'
    path.write_text('\n'.join(json.dumps(line) for line in lines) + '\n')
    return path


# SessionStart ---------------------------------------------------------------------------------


def test_the_brain_gets_its_notes_open_threads_and_today(world: World, cwd: Path) -> None:
    tid = spawn(world)
    transport = WithSummary(ClientTransport(world.client))
    out = ctx.compose(start(world.brain_session, cwd), env(world), transport)
    assert out.startswith('Your context was just compacted.')
    assert out.index(NOTES) < out.index('## Open threads') < out.index('## Today'), out
    assert f'{tid} starting Check disk usage' in out or f'{tid} working Check disk usage' in out
    assert 'Shaun asked about the NUC disk.' in out
    assert transport.paths == ['/brain/tasks', '/brain/summary']


@pytest.mark.parametrize(
    ('source', 'heading'),
    [('startup', 'Where things stand as this session starts:'),
     ('resume', 'This session was resumed. Where things stand:'),
     ('', 'Where things stand as this session starts:')],
)  # fmt: skip
def test_start_and_resume_get_the_same_context(
    world: World, cwd: Path, source: str, heading: str
) -> None:
    out = ctx.compose(start(world.brain_session, cwd, source), env(world), WithSummary(
        ClientTransport(world.client)))  # fmt: skip
    assert out.startswith(heading)
    assert NOTES in out and '## Open threads' in out and 'Shaun asked' in out


def test_without_the_summary_route_it_says_so(world: World, cwd: Path) -> None:
    # Today's hub has no GET /brain/summary (404): the hook still gives the rest.
    out = ctx.compose(start(world.brain_session, cwd), env(world), ClientTransport(world.client))
    assert NOTES in out
    assert '## Open threads\n\nNone.' in out
    assert ctx.NO_SUMMARY in out


def test_a_worker_or_a_stranger_gets_nothing(world: World, cwd: Path) -> None:
    tid = spawn(world)
    transport = ClientTransport(world.client)
    assert ctx.compose(start(f'ses-{tid}', cwd), env(world), transport) == ''  # 403 not_brain
    # 401 unknown_session: also a brain the hub has not registered yet at its very first start.
    assert ctx.compose(start(str(uuid.uuid4()), cwd, 'startup'), env(world), transport) == ''


def test_hub_down_still_gives_the_notes(world: World, cwd: Path) -> None:
    out = ctx.compose(start(world.brain_session, cwd), env(world), Down())
    assert NOTES in out
    assert 'Marcel’s hub could not be asked for the open threads: the hub is not answering' in out
    assert '## Open threads' not in out


@pytest.mark.parametrize(
    ('answer', 'said'),
    [
        (Answers(500, {'code': 'internal_error', 'message': 'It broke.'}), 'It broke.'),
        (Answers(502, None), 'the hub answered 502.'),
        (Answers(401, {'code': 'bad_secret', 'message': 'The hub secret does not match.'}),
         'The hub secret does not match.'),
    ],
)  # fmt: skip
def test_a_hub_problem_is_one_line(world: World, cwd: Path, answer: Answers, said: str) -> None:
    out = ctx.compose(start(world.brain_session, cwd), env(world), answer)
    assert NOTES in out
    assert out.rstrip().endswith(said), out


def test_no_secret_or_no_session_id_is_one_line(world: World, cwd: Path, tmp_path: Path) -> None:
    transport = ClientTransport(world.client)
    out = ctx.compose(start(world.brain_session, cwd), {}, transport)
    assert 'CLAUDE_PLUGIN_DATA is not set' in out
    empty = tmp_path / 'empty'
    empty.mkdir()
    no_file = {'CLAUDE_PLUGIN_DATA': str(empty)}
    assert 'the hub secret could not be read' in ctx.compose(
        start(world.brain_session, cwd), no_file, transport)  # fmt: skip
    (empty / 'hub-secret').write_text('\n')
    assert 'the hub secret file is empty' in ctx.compose(
        start(world.brain_session, cwd), no_file, transport)  # fmt: skip
    out = ctx.compose({'cwd': str(cwd), 'source': 'compact'}, env(world), transport)
    assert 'the hook gave no session id' in out
    assert NOTES in out


def test_long_or_missing_notes(world: World, tmp_path: Path) -> None:
    transport = ClientTransport(world.client)
    out = ctx.compose(start(world.brain_session, tmp_path), env(world), transport)
    assert '## MEMORY.md' not in out
    (tmp_path / 'MEMORY.md').write_text('x' * 10_000)
    out = ctx.compose(start(world.brain_session, tmp_path), env(world), transport)
    assert 'x' * 8192 + '\n… (cut at 8 KB; read MEMORY.md for the rest)' in out
    assert 'x' * 8193 not in out


# PreCompact, then SessionStart(compact) ---------------------------------------------------------


def test_the_hand_over_survives_the_compaction_once(
    world: World, cwd: Path, tmp_path: Path
) -> None:
    tid = spawn(world)
    said = transcript(tmp_path, [
        {'type': 'user', 'message': {'role': 'user', 'content': 'How full is the NUC disk?'}},
        {'type': 'assistant', 'message': {'role': 'assistant', 'content': [
            {'type': 'text', 'text': 'I started a quest to check it.'},
            {'type': 'tool_use', 'name': 'tasks_spawn', 'input': {}}]}},
        {'type': 'user', 'message': {'role': 'user', 'content': [
            {'type': 'tool_result', 'content': 'ok'}]}},
        {'type': 'attachment'},
    ])  # fmt: skip
    path = ctx.handoff(pre(world.brain_session, cwd, said), env(world),
                       ClientTransport(world.client), now=lambda: NOW)  # fmt: skip
    assert path == cwd / '.marcel' / 'handoff.md'
    text = path.read_text()
    assert text.startswith('Compacted at 2026-10-13 20:15 UTC because the context was full.')
    assert tid in text
    assert 'How full is the NUC disk?' in text
    assert 'I started a quest to check it.' in text

    transport = ClientTransport(world.client)
    out = ctx.compose(start(world.brain_session, cwd), env(world), transport)
    assert out.index('## Hand-over from before the compaction') < out.index(NOTES)
    assert 'How full is the NUC disk?' in out
    assert not path.exists(), 'given once'
    assert 'Hand-over' not in ctx.compose(start(world.brain_session, cwd), env(world), transport)


def test_only_a_compaction_reads_the_hand_over(world: World, cwd: Path) -> None:
    (cwd / '.marcel').mkdir()
    (cwd / '.marcel' / 'handoff.md').write_text('old news')
    out = ctx.compose(start(world.brain_session, cwd, 'startup'), env(world),
                      ClientTransport(world.client))  # fmt: skip
    assert 'old news' not in out
    assert (cwd / '.marcel' / 'handoff.md').exists()


def test_a_real_transcript_gives_its_last_exchange() -> None:
    # A recorded Claude Code transcript (SP2): the last prompt and the session's last words.
    asked, answered = ctx.last_exchange(str(TRANSCRIPT / 't1-happy-path.jsonl'))
    assert asked == 'Follow-up 3: reply with the single word RESUMED.'
    assert answered == 'RESUMED'


def test_a_hand_over_without_a_hub_or_transcript(world: World, cwd: Path, tmp_path: Path) -> None:
    path = ctx.handoff(pre(world.brain_session, cwd, None, 'manual'), env(world), Down(),
                       now=lambda: NOW)  # fmt: skip
    assert path is not None
    text = path.read_text()
    assert 'because you asked for it' in text
    assert 'Marcel’s hub could not be asked: the hub is not answering' in text
    assert text.count('(none found)') == 2
    assert ctx.last_exchange(str(tmp_path / 'missing.jsonl')) == ('', '')
    broken = tmp_path / 'broken.jsonl'
    broken.write_text('{"half a line\n' + json.dumps(
        {'type': 'assistant', 'message': {'content': 'x' * 700}}) + '\n')  # fmt: skip
    asked, answered = ctx.last_exchange(str(broken))
    assert asked == '' and answered == 'x' * 600 + ' …'


def test_no_hand_over_outside_a_brain_workspace(world: World, tmp_path: Path) -> None:
    # The hub is down, so a worker cannot be told from the brain: no MEMORY.md, no file written.
    repo = tmp_path / 'some-repo'
    repo.mkdir()
    assert ctx.handoff(pre(world.brain_session, repo, None), env(world), Down()) is None
    assert not (repo / '.marcel').exists()


def test_a_malformed_task_is_one_odd_line(world: World, cwd: Path) -> None:
    fine = {'id': 'tsk_1', 'state': 'working', 'title': 'Fine'}
    body = {'items': [fine, {'title': 'No id'}, 'x'], 'more': False}
    out = ctx.compose(start(world.brain_session, cwd), env(world), Answers(200, body))
    assert NOTES in out
    assert '## Open threads\n\ntsk_1 working Fine\n? ? No id\n\n' in out


def test_a_worker_gets_no_hand_over(world: World, cwd: Path) -> None:
    tid = spawn(world)
    assert (
        ctx.handoff(pre(f'ses-{tid}', cwd, None), env(world), ClientTransport(world.client)) is None
    )
    assert not (cwd / '.marcel').exists()


# main and hooks.json ----------------------------------------------------------------------------


def test_main_always_exits_0(world: World, cwd: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert ctx.main(io.StringIO('not json'), {}) == 0
    assert capsys.readouterr().err.startswith('brain_context:')
    # A real call to a port where nothing listens: the notes and the "not answering" line.
    port_env = {**env(world), 'MARCEL_HUB_URL': 'http://127.0.0.1:9'}
    assert ctx.main(io.StringIO(json.dumps(start(world.brain_session, cwd))), port_env) == 0
    out = capsys.readouterr().out
    assert NOTES in out
    assert 'the hub is not answering' in out
    assert SECRET not in out
    # PreCompact prints nothing and writes the hand-over.
    assert ctx.main(io.StringIO(json.dumps(pre(world.brain_session, cwd, None))), port_env) == 0
    assert capsys.readouterr().out == ''
    assert (cwd / '.marcel' / 'handoff.md').exists()
    assert ctx.main(io.StringIO('[]'), port_env) == 0  # not an object: treated as empty input


def test_the_hooks_run_this_script() -> None:
    hooks = json.loads(HOOKS.read_text())['hooks']
    command = 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/brain_context.py"'
    assert [h['matcher'] for h in hooks['SessionStart']] == ['startup|resume|compact']
    for event in ('SessionStart', 'PreCompact'):
        (entry,) = hooks[event][0]['hooks']
        assert entry == {'type': 'command', 'command': command, 'timeout': 15}
    assert 2 * ctx.TIMEOUT <= 15
