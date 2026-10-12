"""Every route needs a device token, except the public ones the contract names.

The test walks the app's real route table and calls each route without a token, so a router that
forgets the auth dependency turns it red, including routers added by later stories.
"""

import re
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute, iter_route_contexts
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

from marcel_hub.app import Settings, create_app
from marcel_hub.auth import tokens

# The contract's public operations: `security: []` on /health and /pair.
PUBLIC = {('GET', '/api/health'), ('POST', '/api/pair')}
# The channel gateway's socket (S-04.6) takes no device token: a session proves itself with the hub
# secret in its hello (tests/test_channel_gateway.py). It is outside /api, so the tunnel never
# forwards it.
SESSION_SOCKETS = {'/channel'}
# The brain tools' routes (S-05.3) take no device token either: the brain proves itself with the hub
# secret and its session id (tests/test_brain_api.py). Without them they answer 401, which this
# test checks like any other route. They are outside /api too.
HUB_SECRET = 'hs_' + 'a1b2c3d4' * 6
# The session API's routes (contracts/session-api.yaml) take their own tokens, never a device token,
# and answer a missing or wrong one with a 404 a scanner cannot tell from "no such route". This
# test holds them to that 404 instead of 401 (S-05.4: tests/test_adopt.py).
HIDDEN = {('POST', '/api/session/adopt')}
HIDDEN_ANSWER = {'code': 'not_found', 'message': 'There is nothing at that address.'}


def unprotected_routes(app: FastAPI) -> list[str]:
    """Every route that answers something other than 401 without a token, or with a bad one."""
    leaks: list[str] = []
    # A route that forgot the dependency may crash instead of answering: count that as a leak.
    with TestClient(app, raise_server_exceptions=False) as client:
        for route in iter_route_contexts(app.routes):  # included routers, flattened
            # A WebSocket route's context has no path of its own; its original route has it.
            path = str(route.path or getattr(route.original_route, 'path', ''))
            url = re.sub(r'\{[^}]+\}', 'x', path)
            if isinstance(route.original_route, APIWebSocketRoute):
                if path in SESSION_SOCKETS:
                    continue
                leaks += socket_leaks(client, path, url)
                continue
            if not isinstance(route.original_route, APIRoute):
                leaks.append(f'{path} is not an API route: teach this test how to call it')
                continue
            for method in sorted(route.methods or ()):
                if (method, path) in PUBLIC:
                    continue
                hidden = (method, path) in HIDDEN
                for headers in ({}, {'Authorization': f'Bearer {tokens.new_token()}'}):
                    res = client.request(method, url, headers=headers, json={})
                    refused = (
                        res.status_code == 404 and res.json() == HIDDEN_ANSWER
                        if hidden
                        else res.status_code == 401
                    )
                    if not refused:
                        leaks.append(f'{method} {path} -> {res.status_code}')
                        break
    return leaks


def socket_leaks(client: TestClient, path: str, url: str) -> list[str]:
    """A socket must refuse (close before accepting, so the server answers 403) without a token
    and with an unknown one."""
    for headers in ({}, {'Authorization': f'Bearer {tokens.new_token()}'}):
        try:
            with client.websocket_connect(url, headers=headers):
                return [f'WS {path} -> accepted']
        except WebSocketDisconnect:
            continue  # refused, as it should be
        except Exception as exc:  # a crash is not a refusal
            return [f'WS {path} -> {type(exc).__name__}']
    return []


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    (tmp_path / 'hub-secret').write_text(HUB_SECRET)
    return Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db')


def test_every_route_except_health_and_pair_needs_a_token(settings: Settings) -> None:
    assert unprotected_routes(create_app(settings)) == []


def test_the_public_routes_answer_without_a_token(settings: Settings) -> None:
    app = create_app(settings)
    routes = {(m, r.path) for r in iter_route_contexts(app.routes) for m in r.methods or ()}
    assert routes >= PUBLIC
    with TestClient(app) as client:
        assert client.get('/api/health').status_code == 200
        assert client.post('/api/pair', json={'code': 'ZZZZZZZZ', 'device_name': 'x'}).json()[
            'code'
        ] == ('pairing_code_invalid')


def test_the_check_catches_a_router_without_the_auth_dependency(settings: Settings) -> None:
    app = create_app(settings)
    leaky = APIRouter(prefix='/api')

    @leaky.get('/leak/{thing}')
    def leak(thing: str) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {'thing': thing}

    app.include_router(leaky)
    assert unprotected_routes(app) == ['GET /api/leak/{thing} -> 200']


def test_the_check_catches_a_socket_without_the_token_check(settings: Settings) -> None:
    app = create_app(settings)

    @app.websocket('/api/socket')
    async def socket(ws: WebSocket) -> None:  # pyright: ignore[reportUnusedFunction]
        await ws.accept()
        await ws.close()

    assert unprotected_routes(app) == ['WS /api/socket -> accepted']


def test_the_check_catches_a_socket_that_crashes(settings: Settings) -> None:
    app = create_app(settings)

    @app.websocket('/api/socket')
    async def socket(ws: WebSocket) -> None:  # pyright: ignore[reportUnusedFunction]
        raise RuntimeError('forgot the token check and crashed')

    assert unprotected_routes(app) == ['WS /api/socket -> RuntimeError']


def test_the_check_catches_routes_it_cannot_call(settings: Settings) -> None:
    app = create_app(settings)

    async def raw(request: Request) -> PlainTextResponse:  # pragma: no cover - never called
        return PlainTextResponse('raw')

    app.add_route('/api/raw', raw)

    assert unprotected_routes(app) == [
        '/api/raw is not an API route: teach this test how to call it'
    ]


def test_the_schema_pages_are_not_served(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        for path in ('/docs', '/redoc', '/openapi.json'):
            assert client.get(path).status_code == 404
