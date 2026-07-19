"""Tests for the spawned-connector lifecycle (story e).

Drives lazy start, per-(connector, user) isolation, reuse across turns, idle
reaping and bounded-backoff restart through an injected factory — no
subprocess, no network.
"""

from __future__ import annotations

import pytest

from marcel_core.connectors.lifecycle import (
    BACKOFF_BASE_SECONDS,
    BACKOFF_MAX_SECONDS,
    ConnectorRegistry,
    ConnectorStartError,
)
from marcel_core.connectors.models import ConnectorConfig


def _cfg(name='clock', transport='stdio'):
    server = {'transport': 'stdio', 'command': ['clock-mcp']}
    if transport == 'inprocess':
        server = {'transport': 'inprocess', 'module': 'connectors.clock.server'}
    if transport == 'http':
        server = {'transport': 'http', 'url': 'https://x.test'}
    return ConnectorConfig.model_validate(
        {
            'name': name,
            'description': 'x',
            'server': server,
            'auth': {'mode': 'none', 'per_user': False},
        }
    )


class _Factory:
    """Records spawns and hands back a distinct object per call."""

    def __init__(self, fail_times: int = 0) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_times = fail_times

    async def __call__(self, config, slug):
        self.calls.append((config.name, slug))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError('spawn failed')
        return object()


class TestHandles:
    @pytest.mark.parametrize(('transport', 'expected'), [('stdio', True), ('inprocess', True), ('http', False)])
    def test_handles_only_spawned_transports(self, transport, expected):
        assert ConnectorRegistry.handles(_cfg(transport=transport)) is expected


class TestLazyStartAndReuse:
    @pytest.mark.asyncio
    async def test_nothing_spawned_until_acquired(self):
        factory = _Factory()
        ConnectorRegistry(factory)
        assert factory.calls == []  # constructing the registry spawns nothing

    @pytest.mark.asyncio
    async def test_spawns_once_then_reuses(self):
        factory = _Factory()
        reg = ConnectorRegistry(factory)
        first = await reg.acquire(_cfg(), 'shaun')
        second = await reg.acquire(_cfg(), 'shaun')
        assert first is second
        assert factory.calls == [('clock', 'shaun')]  # one spawn across turns

    @pytest.mark.asyncio
    async def test_instance_per_user(self):
        """Two users never share a spawned server — the credential is per-user."""
        factory = _Factory()
        reg = ConnectorRegistry(factory)
        a = await reg.acquire(_cfg(), 'alice')
        b = await reg.acquire(_cfg(), 'bob')
        assert a is not b
        assert reg.live_keys() == {('clock', 'alice'), ('clock', 'bob')}

    @pytest.mark.asyncio
    async def test_instance_per_connector(self):
        factory = _Factory()
        reg = ConnectorRegistry(factory)
        await reg.acquire(_cfg('clock'), 'shaun')
        await reg.acquire(_cfg('notes'), 'shaun')
        assert reg.live_keys() == {('clock', 'shaun'), ('notes', 'shaun')}


class TestIdleReaping:
    @pytest.mark.asyncio
    async def test_idle_instance_closed(self):
        closed: list[object] = []

        async def closer(inst):
            closed.append(inst)

        reg = ConnectorRegistry(_Factory(), idle_seconds=60.0, closer=closer)
        inst = await reg.acquire(_cfg(), 'shaun', now=1000.0)
        assert await reg.reap_idle(now=1030.0) == 0  # still within the window
        assert await reg.reap_idle(now=1100.0) == 1  # past it
        assert closed == [inst]
        assert reg.live_keys() == set()

    @pytest.mark.asyncio
    async def test_use_refreshes_idle_clock(self):
        reg = ConnectorRegistry(_Factory(), idle_seconds=60.0)
        await reg.acquire(_cfg(), 'shaun', now=1000.0)
        await reg.acquire(_cfg(), 'shaun', now=1050.0)  # touched
        assert await reg.reap_idle(now=1100.0) == 0  # 50s since last use
        assert await reg.reap_idle(now=1120.0) == 1

    @pytest.mark.asyncio
    async def test_reaped_instance_respawns_on_next_use(self):
        factory = _Factory()
        reg = ConnectorRegistry(factory, idle_seconds=10.0)
        await reg.acquire(_cfg(), 'shaun', now=0.0)
        await reg.reap_idle(now=100.0)
        await reg.acquire(_cfg(), 'shaun', now=101.0)
        assert len(factory.calls) == 2

    @pytest.mark.asyncio
    async def test_closer_error_does_not_break_reaping(self):
        async def bad_closer(inst):
            raise RuntimeError('close failed')

        reg = ConnectorRegistry(_Factory(), idle_seconds=1.0, closer=bad_closer)
        await reg.acquire(_cfg(), 'shaun', now=0.0)
        assert await reg.reap_idle(now=100.0) == 1
        assert reg.live_keys() == set()

    @pytest.mark.asyncio
    async def test_shutdown_closes_everything(self):
        closed: list[object] = []

        async def closer(inst):
            closed.append(inst)

        reg = ConnectorRegistry(_Factory(), closer=closer)
        await reg.acquire(_cfg(), 'alice')
        await reg.acquire(_cfg(), 'bob')
        await reg.shutdown()
        assert len(closed) == 2
        assert reg.live_keys() == set()


class TestBackoff:
    @pytest.mark.asyncio
    async def test_failure_raises_readable_and_starts_backoff(self):
        reg = ConnectorRegistry(_Factory(fail_times=1))
        with pytest.raises(ConnectorStartError, match='could not be started'):
            await reg.acquire(_cfg(), 'shaun', now=0.0)

    @pytest.mark.asyncio
    async def test_retry_blocked_inside_backoff(self):
        factory = _Factory(fail_times=1)
        reg = ConnectorRegistry(factory)
        with pytest.raises(ConnectorStartError):
            await reg.acquire(_cfg(), 'shaun', now=0.0)
        # Inside the window the factory is not called again — no host spinning.
        with pytest.raises(ConnectorStartError, match='retrying shortly'):
            await reg.acquire(_cfg(), 'shaun', now=BACKOFF_BASE_SECONDS / 2)
        assert len(factory.calls) == 1

    @pytest.mark.asyncio
    async def test_retry_allowed_after_backoff_and_recovers(self):
        factory = _Factory(fail_times=1)
        reg = ConnectorRegistry(factory)
        with pytest.raises(ConnectorStartError):
            await reg.acquire(_cfg(), 'shaun', now=0.0)
        inst = await reg.acquire(_cfg(), 'shaun', now=BACKOFF_BASE_SECONDS + 0.1)
        assert inst is not None
        assert len(factory.calls) == 2

    @pytest.mark.asyncio
    async def test_backoff_doubles_per_consecutive_failure(self):
        factory = _Factory(fail_times=5)
        reg = ConnectorRegistry(factory)
        clock = 0.0
        delays = []
        for _ in range(3):
            with pytest.raises(ConnectorStartError):
                await reg.acquire(_cfg(), 'shaun', now=clock)
            entry = reg._entries[('clock', 'shaun')]
            delays.append(entry.retry_after - clock)
            clock = entry.retry_after  # wait exactly the backoff out
        assert delays == [
            BACKOFF_BASE_SECONDS,
            BACKOFF_BASE_SECONDS * 2,
            BACKOFF_BASE_SECONDS * 4,
        ]

    @pytest.mark.asyncio
    async def test_backoff_is_capped(self):
        factory = _Factory(fail_times=50)
        reg = ConnectorRegistry(factory)
        clock = 0.0
        for _ in range(20):
            with pytest.raises(ConnectorStartError):
                await reg.acquire(_cfg(), 'shaun', now=clock)
            entry = reg._entries[('clock', 'shaun')]
            assert entry.retry_after - clock <= BACKOFF_MAX_SECONDS
            clock = entry.retry_after

    @pytest.mark.asyncio
    async def test_success_clears_failure_count(self):
        factory = _Factory(fail_times=1)
        reg = ConnectorRegistry(factory)
        with pytest.raises(ConnectorStartError):
            await reg.acquire(_cfg(), 'shaun', now=0.0)
        await reg.acquire(_cfg(), 'shaun', now=BACKOFF_BASE_SECONDS + 1)
        entry = reg._entries[('clock', 'shaun')]
        assert entry.failures == 0 and entry.retry_after == 0.0

    @pytest.mark.asyncio
    async def test_one_users_failure_does_not_block_another(self):
        class _SelectiveFactory:
            def __init__(self):
                self.calls = []

            async def __call__(self, config, slug):
                self.calls.append(slug)
                if slug == 'alice':
                    raise RuntimeError('alice cannot start')
                return object()

        reg = ConnectorRegistry(_SelectiveFactory())
        with pytest.raises(ConnectorStartError):
            await reg.acquire(_cfg(), 'alice', now=0.0)
        assert await reg.acquire(_cfg(), 'bob', now=0.0) is not None
