from __future__ import annotations

import io
import json
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from fake_claude import FakeClaude, shim


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never reach the network: any outbound connection in this process fails."""

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError('a fake claude test tried to use the network')

    monkeypatch.setattr(socket.socket, 'connect', refuse)
    monkeypatch.setattr(socket, 'create_connection', refuse)


@pytest.fixture
def fake(tmp_path: Path) -> Iterator[FakeClaude]:
    """The real executable on PATH with a scratch HOME; players are stopped afterwards."""
    home = tmp_path / 'home'
    home.mkdir()
    fc = FakeClaude(home)
    yield fc
    fc.stop_all()


@dataclass
class Result:
    code: int
    out: str
    err: str

    def json(self) -> Any:
        return json.loads(self.out)


class Inline:
    """Runs the shim in this process: players are recorded instead of started, then played
    synchronously with no waiting, so every branch is covered and nothing races."""

    def __init__(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.home = home
        self.env = {'HOME': str(home)}
        self.started: list[str] = []
        self.tty = False
        monkeypatch.setattr(shim, 'start_player', lambda _s, short, _c: self.started.append(short))

    @property
    def store(self) -> shim.Store:
        return shim.Store(self.home)

    def __call__(self, *argv: str, **env: str) -> Result:
        out, err = io.StringIO(), io.StringIO()
        code = shim.main(
            list(argv), env={**self.env, **env}, out=out, err=err, isatty=lambda: self.tty
        )
        return Result(code, out.getvalue(), err.getvalue())

    def play(self, short: str | None = None) -> None:
        shim.play(self.store, short or self.started[-1], sleep=lambda _s: None)

    def spawn(self, *flags: str, prompt: str = 'fix the bug', **env: str) -> str:
        result = self('--bg', *flags, '--', prompt, **env)
        assert result.code == 0, result.err
        return result.out.split(' · ')[1]

    def session(self, short: str) -> dict[str, Any]:
        return self.store.load(short)


@pytest.fixture
def inline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Inline:
    home = tmp_path / 'home'
    work = tmp_path / 'work'
    home.mkdir()
    work.mkdir()
    monkeypatch.chdir(work)
    return Inline(home, monkeypatch)
