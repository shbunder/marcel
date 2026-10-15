"""`plugins/marcel/scripts/adopt.py`, the script behind `/marcel-adopt` (S-05.4).

It is a standalone file (standard library only), so it is loaded by path. The end-to-end tests
send its request to the real hub app in-process; the others replace `urllib`'s opener. Nothing
reaches a network or `claude`."""

import email.message
import importlib.util
import io
import json
import socket
import urllib.error
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from conftest import World
from marcel_hub.adopt import new_adopt_token
from marcel_hub.models import Message, MessageKind, Task, TaskOrigin
from sqlmodel import Session, select

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'adopt.py'


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location('marcel_adopt_script', SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adopt = load()
SID = str(uuid.uuid4())
CLOUD_SID = 'session_0168oBNCs8Q2BrHDFu6tYUdh'
TOKEN = 'mra_' + 'f00d' * 16
CWD = '/home/owner/projects/harry'


# This install's own address (S-22.2): the install and `marcel connect` write ~/.marcel/hub-url.
HUB = 'https://hub.example.org'


def token_file(tmp_path: Path, token: str = TOKEN, mode: int = 0o600, hub: bool = True) -> Path:
    path = tmp_path / '.marcel' / 'adopt-token'
    path.parent.mkdir(exist_ok=True)
    path.write_text(token + '\n')
    path.chmod(mode)
    if hub and not (tmp_path / '.marcel' / 'hub-url').exists():
        hub_file(tmp_path, HUB)
    return path


class Env(dict[str, str]):
    """The session's environment, and the home directory the test gives the script (the
    script itself reads it from the password database, never from the environment)."""

    def __init__(self, home: Path, values: dict[str, str]) -> None:
        super().__init__(values)
        self.home = home


def env_for(tmp_path: Path, **extra: str) -> Env:
    return Env(tmp_path, {'CLAUDE_CODE_SESSION_ID': SID, **extra})


class Recorder:
    """A `send` that records the call and gives a fixed answer."""

    def __init__(self, status: int = 201, answer: dict[str, Any] | None = None) -> None:
        self.status = status
        self.answer = answer if answer is not None else {'task_id': 'tsk_1', 'tracked': 'live'}
        self.calls: list[tuple[str, str, dict[str, Any], float]] = []

    def __call__(
        self, url: str, token: str, body: dict[str, Any], timeout: float
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append((url, token, body, timeout))
        return self.status, self.answer


def run(
    argv: list[str], env: Env, send: Any, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    code = adopt.run(argv, env=env, send=send, cwd=CWD, machine='bunderserver', home=env.home)
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err  # never, in any outcome
    return code, out.out


# Done when: a missing token gives a readable instruction ---------------------------------------


def test_a_missing_token_says_exactly_how_to_get_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / '.marcel').mkdir()
    hub_file(tmp_path, HUB)
    send = Recorder()
    code, out = run(['--title', 'x'], env_for(tmp_path), send, capsys)
    assert code == 1
    assert out.startswith('Not adopted. Marcel has no adopt token on this computer yet.')
    assert 'marcel-hub adopt-token' in out
    assert '~/.marcel/adopt-token' in out and 'umask 077' in out
    assert send.calls == []


def test_an_empty_token_file_says_how_to_get_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file(tmp_path, token='')
    code, out = run([], env_for(tmp_path), Recorder(), capsys)
    assert code == 1 and 'is empty' in out and 'marcel-hub adopt-token' in out


# The token file -------------------------------------------------------------------------------


@pytest.mark.parametrize('mode', [0o640, 0o604, 0o644])
def test_a_token_others_can_read_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: int
) -> None:
    token_file(tmp_path, mode=mode)
    send = Recorder()
    code, out = run([], env_for(tmp_path), send, capsys)
    assert code == 1
    assert 'can be read by other people' in out and 'chmod 600' in out
    assert send.calls == []


def test_a_token_file_of_another_user_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file(tmp_path)
    monkeypatch.setattr(adopt.os, 'getuid', lambda: 4242)
    code, out = run([], env_for(tmp_path), Recorder(), capsys)
    assert code == 1 and 'belongs to another user' in out


def test_an_unreadable_token_file_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file(tmp_path, mode=0o000)
    code, out = run([], env_for(tmp_path), Recorder(), capsys)
    assert code == 1 and 'Could not read' in out


def test_a_token_path_that_cannot_be_looked_at_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / '.marcel').write_text('')  # a file where the folder should be
    code, out = run([], env_for(tmp_path), Recorder(), capsys)
    assert code == 1 and 'Could not read' in out


def test_a_cloud_session_can_take_the_token_from_its_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / '.marcel').mkdir()
    hub_file(tmp_path, HUB)  # a cloud session needs the address too: no built-in one (S-22.2)
    send = Recorder()
    env = env_for(tmp_path, MARCEL_ADOPT_TOKEN=TOKEN)
    code, _ = run([], env, send, capsys)
    assert code == 0 and send.calls[0][1] == TOKEN


@pytest.mark.parametrize('status', [400, 404, 429, 500])
def test_the_token_is_never_printed_whatever_happens(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], status: int
) -> None:
    env = env_for(tmp_path, MARCEL_ADOPT_TOKEN=TOKEN)
    code, _ = run([], env, Recorder(status, {'message': 'no'}), capsys)  # `run` checks the output
    assert code == 1


def test_the_file_wins_over_the_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file(tmp_path)
    send = Recorder()
    run([], env_for(tmp_path, MARCEL_ADOPT_TOKEN='mra_other'), send, capsys)
    assert send.calls[0][1] == TOKEN


# What is sent ---------------------------------------------------------------------------------


def test_a_local_session_sends_its_id_folder_and_machine_over_https(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file(tmp_path)
    send = Recorder()
    code, out = run(['--title', '  Refactor   the renderer '], env_for(tmp_path), send, capsys)
    assert code == 0
    url, token, body, timeout = send.calls[0]
    assert url == f'{HUB}/api/session/adopt'
    assert token == TOKEN and timeout == adopt.TIMEOUT
    assert body == {'session_id': SID, 'kind': 'local', 'title': 'Refactor the renderer',
                    'cwd': CWD, 'machine': 'bunderserver'}  # fmt: skip
    assert out.startswith('Marcel has taken over “Refactor the renderer”.')
    assert 'reads along live' in out


def test_the_session_id_argument_wins_and_an_empty_one_falls_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file(tmp_path)
    send = Recorder()
    other = str(uuid.uuid4())
    run(['--session-id', other], env_for(tmp_path), send, capsys)
    run(['--session-id', ''], env_for(tmp_path), send, capsys)  # `${CLAUDE_SESSION_ID}` unset
    assert [c[2]['session_id'] for c in send.calls] == [other, SID]


def test_without_a_title_the_folder_names_it_and_a_long_one_is_cut(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file(tmp_path)
    send = Recorder()
    run([], env_for(tmp_path), send, capsys)
    run(['--title', 'word ' * 40], env_for(tmp_path), send, capsys)
    first, second = (c[2]['title'] for c in send.calls)
    assert first == 'Session in harry'
    assert len(second) == adopt.MAX_TITLE and second.endswith('…')


def test_an_odd_host_name_is_left_out(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    token_file(tmp_path)
    send = Recorder()
    adopt.run([], env=env_for(tmp_path), send=send, cwd=CWD, machine='my laptop!', home=tmp_path)
    assert 'machine' not in send.calls[0][2]


def test_no_session_id_says_so(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    token_file(tmp_path)
    env = env_for(tmp_path)
    del env['CLAUDE_CODE_SESSION_ID']
    send = Recorder()
    code, out = run([], env, send, capsys)
    assert code == 1 and 'did not tell /marcel-adopt which session' in out
    assert send.calls == []


def test_a_cloud_session_sends_its_cloud_id_and_link(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file(tmp_path)
    send = Recorder(answer={'task_id': 'tsk_1', 'tracked': 'links_only'})
    env = env_for(tmp_path, CLAUDE_CODE_REMOTE='true', CLAUDE_CODE_REMOTE_SESSION_ID=CLOUD_SID)
    code, out = run(['--title', 'Release notes'], env, send, capsys)
    assert code == 0
    assert send.calls[0][2] == {'session_id': CLOUD_SID, 'kind': 'cloud',
                                'title': 'Release notes',
                                'url': f'https://claude.ai/code/{CLOUD_SID}'}  # fmt: skip
    assert 'cannot read along' in out


def test_a_cloud_session_without_its_id_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file(tmp_path)
    code, out = run([], env_for(tmp_path, CLAUDE_CODE_REMOTE='1'), Recorder(), capsys)
    assert code == 1 and 'This is a cloud session' in out


def hub_file(home: Path, url: str, mode: int = 0o600) -> None:
    path = home / '.marcel' / 'hub-url'
    path.parent.mkdir(exist_ok=True)
    path.write_text(url + '\n')
    path.chmod(mode)


def test_without_a_hub_address_it_says_run_marcel_connect_first(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file(tmp_path, hub=False)
    send = Recorder()
    code, out = run([], env_for(tmp_path), send, capsys)
    assert code == 1 and 'Run `marcel connect` first' in out
    assert send.calls == []


def test_the_hub_address_comes_from_an_owner_only_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file(tmp_path)
    send = Recorder()
    hub_file(tmp_path, 'https://hub.test/api/')
    run([], env_for(tmp_path), send, capsys)
    hub_file(tmp_path, 'https://hub.test')
    run([], env_for(tmp_path), send, capsys)
    assert [c[0] for c in send.calls] == ['https://hub.test/api/session/adopt'] * 2


@pytest.mark.parametrize(
    'url', ['http://hub.example.org', 'hub.example.org', 'https://', 'https://x.test/?a=1']
)
def test_the_hub_address_must_be_https(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], url: str
) -> None:
    token_file(tmp_path)
    hub_file(tmp_path, url)
    send = Recorder()
    code, out = run([], env_for(tmp_path), send, capsys)
    assert code == 1 and 'must hold an https:// address' in out
    assert send.calls == []


def test_a_hub_address_file_others_can_write_or_read_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file(tmp_path)
    hub_file(tmp_path, 'https://hub.test', mode=0o644)
    send = Recorder()
    code, out = run([], env_for(tmp_path), send, capsys)
    assert code == 1 and 'can be read by other people' in out
    assert send.calls == []


def test_the_environment_cannot_move_the_token_or_the_hub(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cloned repository's .claude/settings.json can set any environment variable."""
    token_file(tmp_path)
    evil = tmp_path / 'evil'
    evil.mkdir()
    token_file(evil, token='mra_' + 'bad0' * 16)
    hub_file(evil, 'https://evil.test')
    monkeypatch.setenv('HOME', str(evil))
    monkeypatch.setenv('MARCEL_HUB_URL', 'https://evil.test/api')
    monkeypatch.setenv('MARCEL_ADOPT_TOKEN_FILE', str(evil / '.marcel' / 'adopt-token'))
    env = env_for(
        tmp_path,
        HOME=str(evil),
        MARCEL_HUB_URL='https://evil.test/api',
        MARCEL_ADOPT_TOKEN_FILE=str(evil / '.marcel' / 'adopt-token'),
    )
    send = Recorder()
    run([], env, send, capsys)
    assert send.calls[0][:2] == (f'{HUB}/api/session/adopt', TOKEN)


def test_the_home_directory_comes_from_the_password_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pwd

    monkeypatch.setenv('HOME', '/tmp/somewhere-a-repo-chose')
    assert adopt.home_dir() == Path(pwd.getpwuid(adopt.os.getuid()).pw_dir)


def test_unsafe_characters_are_dropped_from_the_title(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file(tmp_path)
    send = Recorder()
    title = 'fix\u2028the\u2029renderer \u202eexe.txt\u2066x\u2069\u200f'
    run(['--title', title], env_for(tmp_path), send, capsys)
    assert send.calls[0][2]['title'] == 'fix the renderer exe.txt x'


COMMAND = SCRIPT.parents[1] / 'commands' / 'marcel-adopt.md'


def test_the_command_may_run_only_the_plugins_own_script() -> None:
    text = COMMAND.read_text()
    [rule] = [line for line in text.splitlines() if line.startswith('allowed-tools:')]
    prefix = 'python3 ${CLAUDE_PLUGIN_ROOT}/scripts/adopt.py --session-id'
    assert rule == f'allowed-tools: Bash({prefix}:*)'
    [line] = [x.strip() for x in text.splitlines() if 'scripts/adopt.py --session-id' in x
              and not x.startswith('allowed-tools')]  # fmt: skip
    assert line.startswith(prefix + ' ')  # the command it asks for is the one the rule allows


# What the hub answered ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('status', 'answer', 'says'),
    [
        (200, {'task_id': 'tsk_1', 'tracked': 'live'}, 'already follows this session'),
        (404, {'code': 'not_found'}, 'did not accept your adopt token'),
        (401, {}, 'did not accept your adopt token'),
        (429, {}, 'Wait a minute'),
        (400, {'message': 'Check the request: `cwd` must be a full path.'}, '`cwd` must be'),
        (400, {}, 'it did not say why'),
        (502, {}, 'answered 502'),
    ],
)
def test_every_answer_is_one_plain_sentence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    status: int,
    answer: dict[str, Any],
    says: str,
) -> None:
    token_file(tmp_path)
    code, out = run([], env_for(tmp_path), Recorder(status, answer), capsys)
    assert code == (0 if status == 200 else 1)
    assert says in out
    assert out.count('\n') == 1


# The real `post`, with urllib's opener replaced -----------------------------------------------


class FakeResponse(io.BytesIO):
    status = 201


class FakeOpener:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.requests: list[Any] = []

    def open(self, req: Any, timeout: float) -> Any:
        self.requests.append((req, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def with_opener(monkeypatch: pytest.MonkeyPatch, result: Any) -> FakeOpener:
    fake = FakeOpener(result)
    monkeypatch.setattr(adopt, 'opener', lambda: fake)
    return fake


def http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        'https://hub.test/api/session/adopt', code, 'x', email.message.Message(), io.BytesIO(body)
    )


def test_post_sends_json_with_the_token_as_a_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = with_opener(monkeypatch, FakeResponse(b'{"task_id": "tsk_1", "tracked": "live"}'))
    status, answer = adopt.post('https://hub.test/api/session/adopt', TOKEN, {'a': 1}, 15.0)
    assert (status, answer) == (201, {'task_id': 'tsk_1', 'tracked': 'live'})
    req, timeout = fake.requests[0]
    assert req.get_method() == 'POST' and timeout == 15.0
    assert req.get_header('Authorization') == f'Bearer {TOKEN}'
    assert req.get_header('Content-type') == 'application/json'
    assert json.loads(req.data) == {'a': 1}


def test_post_returns_an_error_answer_and_tolerates_non_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_opener(monkeypatch, http_error(404, b'{"code": "not_found", "message": "x"}'))
    assert adopt.post('https://hub.test/a', TOKEN, {}, 1.0) == (
        404,
        {'code': 'not_found', 'message': 'x'},
    )
    with_opener(monkeypatch, http_error(502, b'<html>Bad gateway</html>'))
    assert adopt.post('https://hub.test/a', TOKEN, {}, 1.0) == (502, {})
    with_opener(monkeypatch, http_error(500, b'[1, 2]'))
    assert adopt.post('https://hub.test/a', TOKEN, {}, 1.0) == (500, {})


@pytest.mark.parametrize(
    ('error', 'says'),
    [
        (urllib.error.URLError(socket.gaierror(8, 'nodename not known')), 'Could not reach'),
        (urllib.error.URLError(ConnectionRefusedError(61, 'refused')), 'is online'),
        (urllib.error.URLError(TimeoutError()), 'did not answer within 15 seconds'),
        (TimeoutError(), 'did not answer within 15 seconds'),
    ],
)
def test_a_hub_out_of_reach_is_said_plainly(
    monkeypatch: pytest.MonkeyPatch, error: BaseException, says: str
) -> None:
    with_opener(monkeypatch, error)
    with pytest.raises(adopt.AdoptError, match=says) as raised:
        adopt.post('https://hub.test/api/session/adopt', TOKEN, {}, 15.0)
    assert 'hub.test' in str(raised.value) and TOKEN not in str(raised.value)


def test_a_redirect_is_refused_so_the_token_goes_nowhere_else() -> None:
    handler = adopt.opener().handlers
    no_redirects = next(h for h in handler if isinstance(h, adopt.NoRedirects))
    with pytest.raises(adopt.AdoptError, match='redirect'):
        no_redirects.redirect_request(None, None, 302, 'Found', {}, 'http://evil.test/')


# End to end against the real hub app ----------------------------------------------------------


def hub_send(world: World):
    """`send` over the hub's in-process test client, at the path the script asked for."""

    def send(url: str, token: str, body: dict[str, Any], timeout: float) -> tuple[int, Any]:
        assert url.startswith(f'{HUB}/api/')
        path = url.removeprefix(HUB)
        res = world.client.post(path, json=body, headers={'Authorization': f'Bearer {token}'})
        return res.status_code, res.json()

    return send


def test_end_to_end_the_hub_adopts_the_session_once(
    world: World, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hub_token = new_adopt_token(world.data_dir)
    files = tmp_path / 'home'
    files.mkdir()
    token_file(files, token=hub_token)
    env = env_for(files)
    run_e2e = adopt.run
    send = hub_send(world)

    assert run_e2e(['--title', 'Harry renderer'], env=env, send=send, cwd=CWD, home=files) == 0
    assert 'Marcel has taken over “Harry renderer”' in capsys.readouterr().out
    assert run_e2e(['--title', 'Harry renderer'], env=env, send=send, cwd=CWD, home=files) == 0
    assert 'already follows' in capsys.readouterr().out

    with Session(world.app.state.engine) as s:
        [task] = s.exec(select(Task)).all()
        milestones = s.exec(select(Message).where(Message.kind == MessageKind.milestone)).all()
    assert task.origin == TaskOrigin.adopted and task.session_id == SID
    assert [m.body_json['milestone']['event'] for m in milestones] == ['adopted']


def test_end_to_end_a_wrong_token_is_explained(
    world: World, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    new_adopt_token(world.data_dir)
    token_file(tmp_path)  # not the hub's
    code, out = run([], env_for(tmp_path), hub_send(world), capsys)
    assert code == 1 and 'did not accept your adopt token' in out


# The adopt code: the token bound to the hub that issued it (S-22.2) ---------------------------


def test_an_adopt_code_sends_its_token_to_its_own_hub(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file(tmp_path, token=f'{TOKEN}@https://mac.tail1234.ts.net', hub=False)
    send = Recorder()
    code, _ = run([], env_for(tmp_path), send, capsys)
    assert code == 0
    assert send.calls[0][:2] == ('https://mac.tail1234.ts.net/api/session/adopt', TOKEN)


def test_a_cloud_session_adopts_with_the_code_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A cloud session has no ~/.marcel: the code in its secret carries the address."""
    send = Recorder()
    env = env_for(tmp_path, MARCEL_ADOPT_TOKEN=f'{TOKEN}@https://mac.tail1234.ts.net/api')
    code, _ = run([], env, send, capsys)
    assert code == 0
    assert send.calls[0][:2] == ('https://mac.tail1234.ts.net/api/session/adopt', TOKEN)


def test_the_codes_address_beats_the_hub_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file(tmp_path, token=f'{TOKEN}@https://mac.tail1234.ts.net')  # hub file: HUB
    send = Recorder()
    run([], env_for(tmp_path), send, capsys)
    assert send.calls[0][0] == 'https://mac.tail1234.ts.net/api/session/adopt'


def test_a_code_in_the_environment_never_redirects_the_owners_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A cloned repository can set MARCEL_ADOPT_TOKEN, but the token file always wins."""
    token_file(tmp_path)
    send = Recorder()
    env = env_for(tmp_path, MARCEL_ADOPT_TOKEN='mra_' + 'bad0' * 16 + '@https://evil.test')
    run([], env, send, capsys)
    assert send.calls[0][:2] == (f'{HUB}/api/session/adopt', TOKEN)


@pytest.mark.parametrize(
    'hub',
    [
        'http://mac.example.org',
        'https://me@evil.test',
        'https://',
        'mac.example.org',
        'https://x.test/?a=1',
        'https://x.test/#a',
    ],
)
def test_a_code_with_an_address_that_cannot_be_trusted_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], hub: str
) -> None:
    send = Recorder()
    env = env_for(tmp_path, MARCEL_ADOPT_TOKEN=f'{TOKEN}@{hub}')
    code, out = run([], env, send, capsys)
    assert code == 1 and 'must hold an https:// address' in out
    assert send.calls == []
