"""`POST /brains` (S-17.7): one agent's brain folder, made on the host for the hub.

Through the socket, with the real `brain/render.py`. Claude's plugin install and the trust dialog
are stand-ins that record what they were asked and write what Claude Code would."""

import http.client
import json
import socket
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import apart

from marcel_runner import brains as brains_mod
from marcel_runner.brains import ANIMALS, PERSONALITIES, Brains
from marcel_runner.health import Settings
from marcel_runner.server import serve
from marcel_runner.sessions import SessionError


class Host:
    """A fake home: Claude's plugin list and trusted folders, and what was run."""

    def __init__(self, tmp: Path) -> None:
        self.home = tmp / 'home'
        (self.home / '.claude' / 'plugins').mkdir(parents=True)
        self.installs: list[Path] = []
        self.trusted: list[Path] = []
        self.install_fails = False
        self.trust_fails = False

    def run(self, argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if argv[1:3] != ['plugin', 'install']:  # the render: the real brain/render.py
            return brains_mod._run(argv, cwd)
        self.installs.append(cwd)
        if self.install_fails:
            return subprocess.CompletedProcess(argv, 1, '', 'marketplace marcel-local not found')
        path = self.home / '.claude' / 'plugins' / 'installed_plugins.json'
        data = json.loads(path.read_text()) if path.exists() else {'plugins': {}}
        data['plugins'].setdefault('marcel@marcel-local', []).append({'projectPath': str(cwd)})
        path.write_text(json.dumps(data))
        return subprocess.CompletedProcess(argv, 0, 'installed', '')

    def trust(self, claude: Path, cwd: Path, env: dict[str, str]) -> None:
        self.trusted.append(cwd)
        if self.trust_fails:
            raise SessionError(422, 'not_trusted', 'Claude never showed its trust dialog.')
        path = self.home / '.claude.json'
        data = json.loads(path.read_text()) if path.exists() else {'projects': {}}
        data['projects'][str(cwd)] = {'hasTrustDialogAccepted': True}
        path.write_text(json.dumps(data))


@pytest.fixture
def host(tmp_path: Path) -> Host:
    return Host(tmp_path)


@pytest.fixture
def brains(tmp_path: Path, host: Host, fake_claude: Path) -> Brains:
    return Brains(
        claude=fake_claude,
        home=host.home,
        root=tmp_path / 'marcel' / 'brains',
        memory_root=tmp_path / 'marcel' / 'data' / 'memory',
        marketplace=tmp_path / 'marcel' / 'plugins',
        run=host.run,
        trust=host.trust,
    )


@pytest.fixture
def sock(tmp_path: Path, brains: Brains, fake_claude: Path) -> Iterator[Path]:
    path = Path(tempfile_dir()) / 'r.sock'
    srv = serve(path, Settings(claude=fake_claude, cap=1), cgroup_of=apart, brains=brains)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield path
    srv.shutdown()
    srv.server_close()


def tempfile_dir() -> str:
    import tempfile

    return tempfile.mkdtemp(prefix='mb-')  # short: unix socket paths are limited to ~100 bytes


def call(sock: Path, method: str, body: Any = None) -> tuple[int, dict[str, Any]]:
    class Conn(http.client.HTTPConnection):
        def connect(self) -> None:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.connect(str(sock))

    conn = Conn('runner', timeout=60)
    data = (
        None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    )
    conn.request(method, '/brains', body=data)
    res = conn.getresponse()
    out = json.loads(res.read() or b'{}')
    conn.close()
    return res.status, out


PIP = {
    'agent_slug': 'pip',
    'name': 'Pip',
    'persona': {'person_name': 'Emma', 'personality': 'cheerful'},
}


def test_prepares_a_trusted_folder_whose_memory_is_the_agents_own(
    sock: Path, brains: Brains, host: Host
) -> None:
    status, out = call(sock, 'POST', PIP)
    assert status == 200, out
    folder, memory = brains.root / 'pip', brains.memory_root / 'agents' / 'pip'
    assert out == {'agent_slug': 'pip', 'cwd': str(folder), 'memory_dir': str(memory)}
    persona = (folder / 'CLAUDE.md').read_text()
    assert 'You are Pip' in persona and 'helps Emma' in persona and 'cheerful' in persona
    assert (folder / 'MEMORY.md').resolve() == (memory / 'MEMORY.md').resolve()
    assert (folder / 'memory').resolve() == (memory / 'memory').resolve()
    settings = json.loads((folder / '.claude' / 'settings.json').read_text())
    assert settings['extraKnownMarketplaces']['marcel-local']['source']['path'] == str(
        brains.marketplace
    )
    assert host.installs == [folder] and host.trusted == [folder]


def test_two_agents_never_share_a_folder_or_a_memory(sock: Path, brains: Brains) -> None:
    call(sock, 'POST', PIP)
    call(
        sock,
        'POST',
        {'agent_slug': 'marcel', 'name': 'Marcel', 'persona': {'person_name': 'Shaun'}},
    )
    pip, marcel = brains.root / 'pip', brains.root / 'marcel'
    assert (pip / 'MEMORY.md').resolve() != (marcel / 'MEMORY.md').resolve()
    assert 'helps Shaun' in (marcel / 'CLAUDE.md').read_text()
    assert 'professional' in (marcel / 'CLAUDE.md').read_text()  # the default personality


def test_repeating_it_is_safe_and_never_touches_the_memory(
    sock: Path, brains: Brains, host: Host
) -> None:
    call(sock, 'POST', PIP)
    notes = brains.memory_root / 'agents' / 'pip' / 'MEMORY.md'
    notes.write_text('Emma likes foxes.\n')
    status, _ = call(sock, 'POST', PIP)
    assert status == 200
    assert notes.read_text() == 'Emma likes foxes.\n'
    assert host.installs == [brains.root / 'pip'], 'installed once'
    assert host.trusted == [brains.root / 'pip'], 'trusted once'


@pytest.mark.parametrize(
    'body',
    [
        {**PIP, 'agent_slug': '../marcel'},
        {**PIP, 'agent_slug': 'Pip'},
        {**PIP, 'agent_slug': 'a' * 41},
        {**PIP, 'name': ''},
        {**PIP, 'persona': {}},
        {**PIP, 'persona': {'person_name': 'Emma', 'personality': 'angry'}},
        {**PIP, 'persona': {'person_name': 'Emma', 'mood': 'x'}},
        {**PIP, 'persona': {'person_name': 'Emma', 'animal': 'unicorn'}},
        {**PIP, 'persona': {'person_name': 'Emma', 'animal': 7}},
        {**PIP, 'extra': 1},
        b'not json',
        [],
    ],
)
def test_a_body_off_the_contract_is_a_400_and_makes_nothing(
    sock: Path, brains: Brains, body: Any
) -> None:
    status, out = call(sock, 'POST', body)
    assert (status, out['code']) == (400, 'bad_request'), out
    assert not brains.root.exists()


def test_the_brain_speaks_as_its_own_animal(sock: Path, brains: Brains) -> None:
    """S-17.8: Pip is a rabbit, so its CLAUDE.md says rabbit and nothing of a pink giraffe."""
    rabbit = {**PIP, 'persona': {**PIP['persona'], 'animal': 'rabbit'}}
    status, out = call(sock, 'POST', rabbit)
    assert status == 200, out
    persona = (brains.root / 'pip' / 'CLAUDE.md').read_text()
    assert 'a friendly rabbit' in persona
    assert 'giraffe' not in persona


@pytest.mark.parametrize(
    'persona', [{'person_name': 'Shaun', 'animal': 'giraffe'}, {'person_name': 'Shaun'}]
)
def test_a_giraffe_or_no_animal_keeps_the_owners_pink_giraffe(
    sock: Path, brains: Brains, persona: dict[str, str]
) -> None:
    status, out = call(sock, 'POST', {'agent_slug': 'marcel', 'name': 'Marcel', 'persona': persona})
    assert status == 200, out
    assert 'a friendly pink giraffe' in (brains.root / 'marcel' / 'CLAUDE.md').read_text()


def test_a_real_file_where_a_memory_link_goes_is_render_failed(sock: Path, brains: Brains) -> None:
    folder = brains.root / 'pip'
    folder.mkdir(parents=True)
    (folder / 'MEMORY.md').write_text('a real file')
    status, out = call(sock, 'POST', PIP)
    assert (status, out['code']) == (422, 'render_failed'), out
    assert (folder / 'MEMORY.md').read_text() == 'a real file'


def test_a_failed_plugin_install_is_clone_failed(sock: Path, host: Host) -> None:
    host.install_fails = True
    status, out = call(sock, 'POST', PIP)
    assert (status, out['code']) == (422, 'clone_failed'), out
    assert 'marketplace marcel-local not found' in out['detail']


def test_a_folder_that_cannot_be_trusted_is_not_trusted(sock: Path, host: Host) -> None:
    host.trust_fails = True
    status, out = call(sock, 'POST', PIP)
    assert (status, out['code']) == (422, 'not_trusted'), out


def test_a_missing_claude_is_a_503(sock: Path, brains: Brains, host: Host) -> None:
    def gone(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if argv[1:3] == ['plugin', 'install']:
            raise FileNotFoundError(argv[0])
        return brains_mod._run(argv, cwd)

    brains.run = gone
    status, out = call(sock, 'POST', PIP)
    assert (status, out['code']) == (503, 'claude_unavailable'), out


def test_a_slow_step_is_a_503(sock: Path, brains: Brains) -> None:
    def slow(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, 1)

    brains.run = slow
    status, out = call(sock, 'POST', PIP)
    assert (status, out['code']) == (503, 'claude_timeout'), out


def test_only_post(sock: Path) -> None:
    assert call(sock, 'GET')[0] == 405


def test_personalities_are_the_contracts() -> None:
    """PrepareBrain.persona.personality in contracts/runner-api.yaml (one `enum: [...]` line)."""
    spec = (Path(__file__).resolve().parents[2] / 'contracts' / 'runner-api.yaml').read_text()
    block = spec[spec.index('    PrepareBrain:') : spec.index('    PreparedBrain:')]
    line = next(x for x in block.splitlines() if 'enum:' in x and 'professional' in x)
    listed = tuple(w.strip() for w in line.split('[', 1)[1].split(']', 1)[0].split(','))
    assert listed == PERSONALITIES


def test_animals_are_the_contracts() -> None:
    """PrepareBrain.persona.animal in contracts/runner-api.yaml (one `enum: [...]` line)."""
    spec = (Path(__file__).resolve().parents[2] / 'contracts' / 'runner-api.yaml').read_text()
    block = spec[spec.index('    PrepareBrain:') : spec.index('    PreparedBrain:')]
    line = next(x for x in block.splitlines() if 'enum:' in x and 'giraffe' in x)
    listed = tuple(w.strip() for w in line.split('[', 1)[1].split(']', 1)[0].split(','))
    assert listed == ANIMALS


def test_defaults_from_the_environment(tmp_path: Path) -> None:
    b = brains_mod.brains_from_env(Path('/c'), tmp_path, env={})
    assert (b.root, b.memory_root, b.marketplace) == (
        tmp_path / 'marcel' / 'brains',
        tmp_path / 'marcel' / 'data' / 'memory',
        tmp_path / 'marcel' / 'plugins',
    )
    moved = brains_mod.brains_from_env(Path('/c'), tmp_path, env={'MARCEL_BRAINS': '/b'})
    assert moved.root == Path('/b')


def test_any_trust_failure_is_not_trusted(sock: Path, brains: Brains) -> None:
    """Whatever the trust step raises (a slow claude here), the answer is 422 `not_trusted`."""

    def slow_trust(claude: Path, cwd: Path, env: dict[str, str]) -> None:
        raise SessionError(503, 'claude_timeout', 'claude did not answer in time')

    brains.trust = slow_trust
    status, out = call(sock, 'POST', PIP)
    assert (status, out['code']) == (422, 'not_trusted'), out


def test_two_asks_for_the_same_folder_run_one_after_the_other(brains: Brains, host: Host) -> None:
    """A hub that timed out may ask again while the first still runs: the second waits, then
    finds everything done (one install, one trust)."""
    import time

    real = host.run
    inside = threading.Event()

    def slow(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if argv[1:3] == ['plugin', 'install']:
            inside.set()
            time.sleep(0.5)
        return real(argv, cwd)

    brains.run = slow
    body = json.dumps(PIP).encode()
    first = threading.Thread(target=brains.prepare, args=(body,))
    first.start()
    inside.wait(10)
    brains.prepare(body)  # waits for the first
    first.join()
    assert host.installs == [brains.root / 'pip'] and host.trusted == [brains.root / 'pip']
