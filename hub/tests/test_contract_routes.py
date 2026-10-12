"""The hub serves the routes the contracts name: app-api.yaml under `/api`, brain-api.yaml under
`/brain`, and nothing in either place that the contracts do not name."""

import re
from pathlib import Path

import pytest
import yaml
from fastapi.routing import APIRoute, APIWebSocketRoute, iter_route_contexts

from marcel_hub.app import Settings, create_app

CONTRACTS = Path(__file__).resolve().parents[2] / 'contracts'
METHODS = {'get', 'post', 'put', 'patch', 'delete'}

# App API operations the contract has and the hub does not serve yet, with the work that adds
# them. The last test fails as soon as one is served, so this list can only shrink.
PENDING_OPERATIONS: dict[tuple[str, str], str] = {
    ('GET', '/api/agents/{}'): 'agent settings: no story yet',
    ('PATCH', '/api/agents/{}'): 'agent settings: no story yet',
}


def normalise(path: str) -> str:
    return re.sub(r'\{[^}]+\}', '{}', path)


def contract_routes(doc: str, prefix: str) -> set[tuple[str, str]]:
    paths = yaml.safe_load((CONTRACTS / doc).read_text())['paths']
    return {
        (method.upper(), normalise(prefix + path))
        for path, item in paths.items()
        for method in item
        if method in METHODS
    }


@pytest.fixture(scope='module')
def served(tmp_path_factory: pytest.TempPathFactory) -> set[tuple[str, str]]:
    data = tmp_path_factory.mktemp('hub')
    app = create_app(Settings(data_dir=data, db_path=data / 'marcel.db'))
    routes: set[tuple[str, str]] = set()
    for route in iter_route_contexts(app.routes):
        if isinstance(route.original_route, APIWebSocketRoute):  # the contract lists /ws as a GET
            routes.add(('GET', normalise(route.original_route.path)))
        elif isinstance(route.original_route, APIRoute):
            routes |= {(m, normalise(route.original_route.path)) for m in route.methods or ()}
    return routes


def under(routes: set[tuple[str, str]], prefix: str) -> set[tuple[str, str]]:
    return {r for r in routes if r[1].startswith(prefix)}


def test_every_app_api_operation_is_served_or_pending(served: set[tuple[str, str]]) -> None:
    contract = contract_routes('app-api.yaml', '/api')
    assert set(PENDING_OPERATIONS) <= contract, 'a pending operation is not in the contract'
    assert contract - served - set(PENDING_OPERATIONS) == set()


def test_the_hub_serves_nothing_under_api_the_contract_does_not_name(
    served: set[tuple[str, str]],
) -> None:
    contract = contract_routes('app-api.yaml', '/api') | contract_routes('session-api.yaml', '/api')
    assert under(served, '/api/') - contract == set()


def test_the_brain_routes_are_exactly_the_brain_api(served: set[tuple[str, str]]) -> None:
    assert under(served, '/brain/') == contract_routes('brain-api.yaml', '/brain')


def test_pending_operations_are_still_pending(served: set[tuple[str, str]]) -> None:
    now_served = set(PENDING_OPERATIONS) & served
    assert not now_served, f'{now_served} are served now: remove them from PENDING_OPERATIONS'
