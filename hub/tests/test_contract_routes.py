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
    # app-api 0.8.0 (S-02.13): buddies and a phone's buddy, served by S-22.27.
    ('POST', '/api/agents'): 'S-22.27',
    ('POST', '/api/agents/{}/archive'): 'S-22.27',
    ('PATCH', '/api/devices/{}'): 'S-22.27',
    ('POST', '/api/pairing-codes'): 'S-22.27',
}
# App API operations the contract removed and the hub still serves, with the work that removes
# them. app-api 0.8.0 (S-02.13, ADR-013) dropped the people model; S-22.11 removes the routes.
# The last test fails as soon as one is gone, so this list can only shrink.
RETIRED_OPERATIONS: dict[tuple[str, str], str] = {
    ('GET', '/api/users'): 'S-22.11',
    ('POST', '/api/users'): 'S-22.11',
    ('DELETE', '/api/users/{}'): 'S-22.11',
    ('POST', '/api/users/{}/pairing-codes'): 'S-22.11',
}
# The same for brain-api.yaml (under `/brain`) and session-api.yaml (under `/api`, except a path
# with its own `servers`, served at the hub's root). Each list can only shrink.
PENDING_BRAIN_OPERATIONS: dict[tuple[str, str], str] = {}
PENDING_SESSION_OPERATIONS: dict[tuple[str, str], str] = {}


def normalise(path: str) -> str:
    return re.sub(r'\{[^}]+\}', '{}', path)


def contract_routes(doc: str, prefix: str) -> set[tuple[str, str]]:
    """The document's operations under `prefix`; a path with its own `servers` sits at the root."""
    paths = yaml.safe_load((CONTRACTS / doc).read_text())['paths']
    return {
        (method.upper(), normalise(('' if 'servers' in item else prefix) + path))
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
    assert not set(RETIRED_OPERATIONS) & contract, 'a retired operation is back in the contract'
    assert under(served, '/api/') - contract - set(RETIRED_OPERATIONS) == set()


def test_the_brain_routes_are_exactly_the_brain_api(served: set[tuple[str, str]]) -> None:
    contract = contract_routes('brain-api.yaml', '/brain')
    assert set(PENDING_BRAIN_OPERATIONS) <= contract, 'a pending operation is not in the contract'
    assert under(served, '/brain/') == contract - set(PENDING_BRAIN_OPERATIONS)


def test_read_before_hello_is_the_brain_apis_list() -> None:
    """brain-api.yaml 0.1.4 names the routes a rollover's new brain may read before its hello."""
    from marcel_hub.brain_api import READ_BEFORE_HELLO

    doc = yaml.safe_load((CONTRACTS / 'brain-api.yaml').read_text())
    header = ' '.join(doc['info']['description'].split())
    paragraph = header[header.index('**Read before hello**') :].split('**')[2]
    listed = {'/brain' + path for path in re.findall(r'`GET (/[^`]+)`', paragraph)}
    assert listed == READ_BEFORE_HELLO


def test_every_session_api_operation_is_served_or_pending(served: set[tuple[str, str]]) -> None:
    contract = contract_routes('session-api.yaml', '/api')
    assert set(PENDING_SESSION_OPERATIONS) <= contract, 'a pending operation is not in the contract'
    assert contract - served - set(PENDING_SESSION_OPERATIONS) == set()


def test_pending_operations_are_still_pending(served: set[tuple[str, str]]) -> None:
    for pending in (PENDING_OPERATIONS, PENDING_BRAIN_OPERATIONS, PENDING_SESSION_OPERATIONS):
        now_served = set(pending) & served
        assert not now_served, f'{now_served} are served now: remove them from the pending list'


def test_retired_operations_are_still_served(served: set[tuple[str, str]]) -> None:
    gone = set(RETIRED_OPERATIONS) - served
    assert not gone, f'{gone} are no longer served: remove them from RETIRED_OPERATIONS'
