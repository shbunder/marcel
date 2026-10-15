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
# Query parameters app-api.yaml names on a route the hub serves but does not read yet, with the
# work that reads them. The test below fails as soon as one is read, so this list can only shrink.
PENDING_QUERY_PARAMETERS: dict[tuple[str, str, str], str] = {
    # app-api 0.8.1: the Notebook follows the phone's buddy (S-22.12's contract request).
    ('GET', '/api/memory', 'agent_id'): 'S-22.27',
    ('GET', '/api/memory/files/{}', 'agent_id'): 'S-22.27',
    ('PUT', '/api/memory/files/{}', 'agent_id'): 'S-22.27',
}
# Query parameters a route reads by hand rather than declaring them, so FastAPI cannot see them.
READ_BY_HAND: set[tuple[str, str, str]] = {('GET', '/api/ws', 'since')}  # ws.py, query_params


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


def contract_query_parameters(doc: str, prefix: str) -> set[tuple[str, str, str]]:
    """(method, path, name) for every query parameter the document names, `$ref`s resolved."""
    api = yaml.safe_load((CONTRACTS / doc).read_text())
    shared = api.get('components', {}).get('parameters', {})
    found: set[tuple[str, str, str]] = set()
    for path, item in api['paths'].items():
        for method, op in item.items():
            if method not in METHODS:
                continue
            for param in op.get('parameters', []) + item.get('parameters', []):
                if '$ref' in param:
                    param = shared[param['$ref'].rsplit('/', 1)[1]]
                if param['in'] == 'query':
                    found.add((method.upper(), normalise(prefix + path), param['name']))
    return found


@pytest.fixture(scope='module')
def served_query_parameters(tmp_path_factory: pytest.TempPathFactory) -> set[tuple[str, str, str]]:
    """(method, path, name) for every query parameter a hub route declares."""
    data = tmp_path_factory.mktemp('hub-query')
    app = create_app(Settings(data_dir=data, db_path=data / 'marcel.db'))
    found: set[tuple[str, str, str]] = set()
    for route in iter_route_contexts(app.routes):
        original = route.original_route
        if isinstance(original, APIWebSocketRoute | APIRoute):
            methods = route.methods or () if isinstance(original, APIRoute) else ('GET',)
            names = {q.alias for q in original.dependant.query_params}
            found |= {(m, normalise(original.path), n) for m in methods for n in names}
    return found


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


def test_every_query_parameter_of_a_served_route_is_read_or_pending(
    served: set[tuple[str, str]], served_query_parameters: set[tuple[str, str, str]]
) -> None:
    contract = contract_query_parameters('app-api.yaml', '/api')
    assert set(PENDING_QUERY_PARAMETERS) <= contract, 'a pending parameter is not in the contract'
    on_served = {p for p in contract if p[:2] in served}
    unread = on_served - served_query_parameters - set(PENDING_QUERY_PARAMETERS) - READ_BY_HAND
    assert unread == set(), f'the hub ignores {unread}: read them, or list them as pending'


def test_pending_query_parameters_are_still_pending(
    served_query_parameters: set[tuple[str, str, str]],
) -> None:
    read = set(PENDING_QUERY_PARAMETERS) & served_query_parameters
    assert not read, f'{read} are read now: remove them from PENDING_QUERY_PARAMETERS'
