"""End-to-end tests for the UDS-isolated habitat mechanism (ISSUE-f60b09 Phase 1).

Spawns the fixture habitat at ``tests/fixtures/uds_habitat/`` via the
real loader, calls the proxy coroutines the loader registered, and
validates every reachable shape:

- success / numeric result
- concurrent calls on the same habitat (two in-flight at once)
- handler-raised exception → JSON-RPC error frame → proxy ``RuntimeError``
- unknown method → ``-32601`` error
- supervisor respawn after the habitat is killed mid-run
- clean teardown of all tracked habitats

The fixture habitat uses the kernel's own venv (``sys.executable`` via
``_uds_supervisor.habitat_python``). Per-habitat venvs land in Phase 2
when real zoo habitats migrate.

All tests use an isolated data-root via ``MARCEL_DATA_DIR`` so sockets
created here do not collide with any dev instance of Marcel that might
be running on the same machine.
"""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

import pytest
import yaml

from marcel_core.plugin import _uds_supervisor
from marcel_core.toolkit import (
    _make_uds_proxy,
    _metadata,
    _registry,
    discover,
    get_handler,
)

_FIXTURE_DIR = Path(__file__).resolve().parent.parent / 'fixtures' / 'uds_habitat'


@pytest.fixture
def clean_registry(monkeypatch):
    """Give each test a fresh integration registry, restored on teardown."""
    saved_registry = dict(_registry)
    saved_metadata = dict(_metadata)
    _registry.clear()
    _metadata.clear()
    yield
    _registry.clear()
    _registry.update(saved_registry)
    _metadata.clear()
    _metadata.update(saved_metadata)


@pytest.fixture
def isolated_zoo(tmp_path, monkeypatch, clean_registry):
    """Stage a zoo directory containing the uds_fixture habitat.

    Copies the fixture into a zoo-shaped tree and points ``MARCEL_ZOO_DIR``
    + ``MARCEL_DATA_DIR`` at tmp_path so socket files live under test
    control. Cleans up habitat subprocesses in teardown regardless of
    test outcome.
    """
    import shutil

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    zoo_dir = tmp_path / 'zoo'
    (zoo_dir / 'integrations').mkdir(parents=True)
    shutil.copytree(_FIXTURE_DIR, zoo_dir / 'integrations' / 'uds_fixture')

    monkeypatch.setenv('MARCEL_ZOO_DIR', str(zoo_dir))
    monkeypatch.setenv('MARCEL_DATA_DIR', str(data_dir))

    # Settings is a module-level singleton cached at import; patch the
    # live instance so _habitat_socket_path / zoo_dir resolve against tmp.
    from marcel_core.config import settings

    monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo_dir))
    monkeypatch.setattr(settings, 'marcel_data_dir', str(data_dir))

    _uds_supervisor._reset_for_tests()

    yield zoo_dir

    # Kill any remaining subprocesses so a test crash doesn't leak zombies.
    for handle in list(_uds_supervisor.list_habitats().values()):
        proc = handle.proc
        if proc is None or proc.poll() is not None:
            continue
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        try:
            proc.wait(timeout=2.0)
        except Exception:
            pass
    _uds_supervisor._reset_for_tests()


@pytest.mark.asyncio
async def test_discover_spawns_and_registers_proxies(isolated_zoo):
    """discover() spawns the fixture, registers one proxy per `provides:` entry."""
    discover()

    handles = _uds_supervisor.list_habitats()
    assert 'uds_fixture' in handles, handles

    handle = handles['uds_fixture']
    assert handle.proc is not None
    assert handle.proc.poll() is None, 'fixture exited unexpectedly'
    assert handle.socket_path.exists()

    for name in ('uds_fixture.echo', 'uds_fixture.add', 'uds_fixture.boom'):
        assert name in _registry, f'proxy not registered for {name}'


@pytest.mark.asyncio
async def test_proxy_round_trip_success(isolated_zoo):
    """A registered proxy forwards params over UDS and returns the habitat's result."""
    discover()
    handler = get_handler('uds_fixture.echo')
    result = await handler({'message': 'hello'}, 'alice')
    assert result == 'hello (for alice)'


@pytest.mark.asyncio
async def test_proxy_propagates_handler_exception_as_runtime_error(isolated_zoo):
    """A habitat handler that raises becomes a proxy-side RuntimeError with context."""
    discover()
    handler = get_handler('uds_fixture.boom')
    with pytest.raises(RuntimeError, match=r'uds habitat error in .uds_fixture\.boom.'):
        await handler({'tag': 'test'}, 'alice')


@pytest.mark.asyncio
async def test_proxy_unknown_method_reports_method_not_found(isolated_zoo):
    """A proxy built for a method the habitat did not register raises with JSON-RPC wording."""
    discover()
    handle = _uds_supervisor.list_habitats()['uds_fixture']

    # Bypass the registered proxy — build one for a method the habitat never declared.
    rogue = _make_uds_proxy('uds_fixture.nope', handle.socket_path)
    with pytest.raises(RuntimeError, match=r'method not found'):
        await rogue({}, 'alice')


@pytest.mark.asyncio
async def test_concurrent_calls_on_same_habitat(isolated_zoo):
    """Two simultaneous proxy calls each get their own answer — accept loop is concurrent."""
    discover()
    handler = get_handler('uds_fixture.add')
    results = await asyncio.gather(
        handler({'a': 2, 'b': 3}, 'alice'),
        handler({'a': 10, 'b': 20}, 'bob'),
        handler({'a': 100, 'b': 1}, 'carol'),
    )
    assert results == ['5', '30', '101']


@pytest.mark.asyncio
async def test_metadata_is_published_for_uds_habitats(isolated_zoo):
    """``integration.yaml`` metadata is parsed even on the UDS path (needed for depends_on)."""
    discover()
    assert 'uds_fixture' in _metadata
    meta = _metadata['uds_fixture']
    assert sorted(meta.provides) == ['uds_fixture.add', 'uds_fixture.boom', 'uds_fixture.echo']


@pytest.mark.asyncio
async def test_supervisor_respawns_after_sigkill(isolated_zoo):
    """Killing the habitat mid-run causes the supervisor to restart it; proxy calls resume."""
    discover()
    handle = _uds_supervisor.list_habitats()['uds_fixture']
    first_pid = handle.pid
    assert first_pid is not None

    # Start the supervisor loop (discover() doesn't on its own — lifespan does).
    _uds_supervisor.start_supervisor()
    try:
        os.killpg(first_pid, signal.SIGKILL)
        # Wait for restart — poll interval is 2s, plus socket readiness.
        deadline = asyncio.get_running_loop().time() + 15.0
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.25)
            current = _uds_supervisor.list_habitats().get('uds_fixture')
            if (
                current is not None
                and current.pid is not None
                and current.pid != first_pid
                and current.socket_path.exists()
            ):
                break
        else:
            pytest.fail('supervisor did not respawn the habitat within 15s')

        # Prove the respawned habitat is actually answering.
        handler = get_handler('uds_fixture.echo')
        result = await handler({'message': 'alive'}, 'alice')
        assert result == 'alive (for alice)'
    finally:
        await _uds_supervisor.stop_supervisor()


@pytest.mark.asyncio
async def test_stop_supervisor_terminates_all_habitats(isolated_zoo):
    """lifespan-teardown equivalent: stop_supervisor sends SIGTERM to every child."""
    discover()
    handle = _uds_supervisor.list_habitats()['uds_fixture']
    proc = handle.proc
    assert proc is not None and proc.poll() is None

    await _uds_supervisor.stop_supervisor()

    # After stop_supervisor, the handle table is cleared.
    assert _uds_supervisor.list_habitats() == {}
    # The subprocess has exited.
    assert proc.poll() is not None


@pytest.mark.asyncio
async def test_discover_rejects_provides_outside_namespace(tmp_path, monkeypatch, clean_registry):
    """A habitat declaring ``provides:`` outside its own namespace is rejected cleanly."""
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    zoo_dir = tmp_path / 'zoo'
    hab = zoo_dir / 'integrations' / 'bad_hab'
    hab.mkdir(parents=True)
    (hab / 'integration.yaml').write_text(
        yaml.safe_dump(
            {
                'name': 'bad_hab',
                'isolation': 'uds',
                'provides': ['other_namespace.do_thing'],
            }
        )
    )
    (hab / '__init__.py').write_text('')

    from marcel_core.config import settings

    monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo_dir))
    monkeypatch.setattr(settings, 'marcel_data_dir', str(data_dir))
    _uds_supervisor._reset_for_tests()

    try:
        discover()
    finally:
        _uds_supervisor._reset_for_tests()

    assert 'bad_hab' not in _uds_supervisor.list_habitats()
    assert 'other_namespace.do_thing' not in _registry


@pytest.mark.asyncio
async def test_discover_is_idempotent_for_uds_habitats(isolated_zoo):
    """Calling discover() twice does not spawn a second subprocess."""
    discover()
    first_pid = _uds_supervisor.list_habitats()['uds_fixture'].pid

    discover()
    second_pid = _uds_supervisor.list_habitats()['uds_fixture'].pid

    assert first_pid == second_pid, 'discover() spawned the habitat twice'
