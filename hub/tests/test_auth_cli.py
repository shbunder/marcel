"""`marcel-hub pair`: prints a QR code and a one-time code that `POST /pair` accepts."""

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from marcel_hub.app import Settings, create_app, main
from marcel_hub.auth import cli
from marcel_hub.auth.tokens import OWNER_USER_ID
from marcel_hub.conversations import ensure_main_conversation
from marcel_hub.models import Agent
from marcel_hub.users.store import ensure_owner_agent


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv('MARCEL_CONFIG', str(tmp_path / 'none.toml'))
    monkeypatch.setenv('MARCEL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('MARCEL_PUBLIC_URL', 'https://mac.tail1234.ts.net')
    return tmp_path


def run_pair(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[str, str]:
    with pytest.raises(SystemExit) as exit_info:
        main(['pair', *argv])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    url = re.search(r'marcel://pair\S+', out)
    assert url, out
    return out, url.group(0)


def test_pair_prints_a_qr_code_and_a_code_the_hub_accepts(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out, url = run_pair([], capsys)
    query = parse_qs(urlparse(url).query)
    assert query['hub'] == ['https://mac.tail1234.ts.net/api']  # the hub's `public_url` (S-22.2)
    code = query['code'][0]
    assert code in out
    assert '10 minutes' in out
    assert '█' in out or '▀' in out  # a QR code drawn with block characters

    settings = Settings(data_dir=env, db_path=env / 'marcel.db')
    with TestClient(create_app(settings)) as client:
        res = client.post('/api/pair', json={'code': code, 'device_name': 'iPhone'})
    assert res.status_code == 201


def test_pair_takes_another_hub_url(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, url = run_pair(['--hub', 'http://127.0.0.1:7420/api'], capsys)
    assert parse_qs(urlparse(url).query)['hub'] == ['http://127.0.0.1:7420/api']
    assert 'hub=http%3A%2F%2F127.0.0.1%3A7420%2Fapi' in url


def test_pair_refuses_on_broken_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / 'marcel.toml'
    cfg.write_text('port = = 1')
    monkeypatch.setenv('MARCEL_CONFIG', str(cfg))
    with pytest.raises(SystemExit, match=r'No pairing code.*marcel\.toml'):
        cli.pair([])


def test_marcel_hub_without_pair_still_serves(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr('uvicorn.run', lambda app, **kw: calls.append(app))
    main([])
    assert len(calls) == 1


def test_pair_without_a_public_address_says_so_and_makes_no_code(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv('MARCEL_PUBLIC_URL')
    with pytest.raises(SystemExit) as exit_info:
        main(['pair'])
    assert exit_info.value.code == (
        'No pairing code was made. No public address yet: run `marcel connect`.'
    )
    assert 'marcel://pair' not in capsys.readouterr().out


def test_pair_takes_another_address_when_asked(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, url = run_pair(['--hub', 'https://other.example.org/api'], capsys)
    assert parse_qs(urlparse(url).query)['hub'] == ['https://other.example.org/api']


def add_buddies(env: Path) -> tuple[str, str]:
    """Marcel (slug `marcel`) and Pip (slug `pip`), as `setup` and `POST /agents` make them."""
    settings = Settings(data_dir=env, db_path=env / 'marcel.db')
    app = create_app(settings)
    with TestClient(app), Session(app.state.engine) as s:
        marcel, _ = ensure_owner_agent(s, 'Marcel')
        pip = Agent(owner_user_id=OWNER_USER_ID, name='Pip', animal='fox', palette='marcel',
                    seed='Pip', slug='pip')  # fmt: skip
        s.add(pip)
        s.commit()
        ensure_main_conversation(s, pip)
        return marcel.id, pip.id


def test_pair_with_a_buddy_pairs_a_phone_showing_it(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, pip = add_buddies(env)
    _, url = run_pair(['--buddy', 'pip'], capsys)
    query = parse_qs(urlparse(url).query)
    assert query['buddy'] == [pip]
    settings = Settings(data_dir=env, db_path=env / 'marcel.db')
    with TestClient(create_app(settings)) as client:
        paired = client.post('/api/pair', json={'code': query['code'][0], 'device_name': 'iPad'})
        devices = client.get(
            '/api/devices', headers={'Authorization': f'Bearer {paired.json()["token"]}'}
        ).json()
    assert [d['agent_id'] for d in devices] == [pip]


def test_pair_with_an_unknown_buddy_makes_no_code(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_buddies(env)
    with pytest.raises(SystemExit, match=r'No pairing code.*no buddy "zed".*marcel, pip'):
        cli.pair(['--buddy', 'zed'])
    assert 'marcel://pair' not in capsys.readouterr().out
