"""Finding the hub (config) and calling it (the stdlib transport), without any network."""

import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from marcel_tools.hub import (
    DEFAULT_HUB_URL,
    Config,
    ConfigProblem,
    Hub,
    HubError,
    UrllibTransport,
    load_config,
    segment,
)

SID = '6f1c2a9e-8b7d-4c3a-9e2f-0a1b2c3d4e5f'


def plugin_data(tmp_path: Path, secret: str = 'hs_secret\n') -> dict[str, str]:
    (tmp_path / 'hub-secret').write_text(secret)
    return {'CLAUDE_CODE_SESSION_ID': SID, 'CLAUDE_PLUGIN_DATA': str(tmp_path)}


def test_config_from_the_session_id_and_the_plugin_data(tmp_path: Path) -> None:
    assert load_config(plugin_data(tmp_path)) == Config(DEFAULT_HUB_URL, SID, 'hs_secret')
    env = {**plugin_data(tmp_path), 'MARCEL_HUB_URL': 'http://127.0.0.1:9999/'}
    assert load_config(env).hub_url == 'http://127.0.0.1:9999'


@pytest.mark.parametrize(
    ('change', 'problem'),
    [
        ({'CLAUDE_CODE_SESSION_ID': 'nope'}, 'CLAUDE_CODE_SESSION_ID is missing'),
        ({'CLAUDE_PLUGIN_DATA': ''}, 'CLAUDE_PLUGIN_DATA is not set'),
        ({'CLAUDE_PLUGIN_DATA': '/no/such/dir'}, 'The hub secret is missing'),
    ],
)
def test_config_problems_say_what_is_missing(
    tmp_path: Path, change: dict[str, str], problem: str
) -> None:
    with pytest.raises(ConfigProblem, match=problem):
        load_config({**plugin_data(tmp_path), **change})


def test_an_empty_secret_file_is_a_problem(tmp_path: Path) -> None:
    with pytest.raises(ConfigProblem, match='is empty'):
        load_config(plugin_data(tmp_path, '  \n'))


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self) -> 'FakeResponse':
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def test_the_transport_sends_json_with_the_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []

    def urlopen(req: urllib.request.Request, timeout: float) -> FakeResponse:
        seen.append(req)
        return FakeResponse(b'{"ok": true}')

    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    hub = Hub(Config('http://127.0.0.1:7420', SID, 'hs_secret'))
    assert hub.call('POST', '/tasks', {'title': 'x'}, {'a': 'b c'}) == {'ok': True}
    (req,) = seen
    assert req.full_url == 'http://127.0.0.1:7420/brain/tasks?a=b+c'
    assert req.get_method() == 'POST'
    assert req.data == b'{"title": "x"}'
    assert req.get_header('X-marcel-secret') == 'hs_secret'
    assert req.get_header('X-marcel-session') == SID
    assert req.get_header('Content-type') == 'application/json'


def test_the_transport_reads_an_error_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(req: urllib.request.Request, timeout: float) -> Any:
        body = io.BytesIO(json.dumps({'code': 'x', 'message': 'No such task.'}).encode())
        raise urllib.error.HTTPError(req.full_url, 404, 'Not Found', {}, body)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    transport = UrllibTransport('http://hub')
    status, body = transport.request('GET', '/x', params=None, body=None, headers={})
    assert (status, body) == (404, {'code': 'x', 'message': 'No such task.'})
    with pytest.raises(HubError, match='^No such task.$'):
        Hub(Config('http://hub', SID, 's'), transport).call('GET', '/x')


def test_a_hub_that_is_down_is_said_plainly(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(req: urllib.request.Request, timeout: float) -> Any:
        raise urllib.error.URLError(ConnectionRefusedError(61, 'Connection refused'))

    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    hub = Hub(Config('http://127.0.0.1:7420', SID, 's'))
    with pytest.raises(HubError, match='hub is not answering at http://127.0.0.1:7420') as exc:
        hub.call('GET', '/tasks')
    assert str(exc.value).endswith('Nothing was done.')


class Canned:
    def __init__(self, status: int, body: Any) -> None:
        self.answer = (status, body)

    def request(self, method: str, path: str, **_: Any) -> tuple[int, Any]:
        return self.answer


def test_odd_answers_still_read_as_sentences(monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config('http://hub', SID, 's')
    with pytest.raises(HubError, match='The hub answered 500 with no explanation.'):
        Hub(config, Canned(500, None)).call('GET', '/x')

    def urlopen(req: urllib.request.Request, timeout: float) -> FakeResponse:
        return FakeResponse(b'Internal Server Error')

    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    status, body = UrllibTransport('http://hub').request('GET', '/', params=None, body=None,
                                                         headers={})  # fmt: skip
    assert body == {'message': 'Internal Server Error'}
    monkeypatch.setattr(urllib.request, 'urlopen', lambda req, timeout: FakeResponse(b''))
    assert UrllibTransport('http://hub').request('GET', '/', params=None, body=None,
                                                 headers={}) == (200, None)  # fmt: skip


def test_segment_keeps_a_value_in_one_path_part() -> None:
    assert segment('../projects') == '..%2Fprojects'
