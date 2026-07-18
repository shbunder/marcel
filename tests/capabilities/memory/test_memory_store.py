"""Unit tests for the per-user memory stores (FEAT-260718-30d45a).

The harness ``FileStore`` supplies CAS/journal semantics (tested upstream);
these tests pin Marcel's wrapping: layout mapping onto the pre-capability
memory directory, existing-file round-trip without migration, per-user
caching, and the read-only household union.
"""

from __future__ import annotations

import pytest
from pydantic_ai_harness.memory import MemoryConflictError

from marcel_core.capabilities.memory import memory_store_for, reset_memory_stores
from marcel_core.storage import _root


@pytest.fixture(autouse=True)
def _rooted(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
    reset_memory_stores()
    yield tmp_path
    reset_memory_stores()


EXISTING = '---\nname: news\ndescription: News preferences\ntype: preference\n---\nReads VRT NWS.\n'


class TestLayoutAndRoundTrip:
    async def test_existing_frontmatter_file_reads_without_migration(self, _rooted):
        mem_dir = _rooted / 'users' / 'alice' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'news.md').write_text(EXISTING)

        store = memory_store_for('alice')
        file = await store.read('memory/news.md', max_chars=10_000)
        assert file is not None
        assert 'Reads VRT NWS.' in file.content
        assert 'name: news' in file.content

    async def test_write_lands_in_the_memory_dir(self, _rooted):
        store = memory_store_for('alice')
        await store.write('memory/garden.md', 'Tomatoes in June.', expected_version=None)
        assert (_rooted / 'users' / 'alice' / 'memory' / 'garden.md').read_text() == 'Tomatoes in June.'

    async def test_stale_version_conflicts(self, _rooted):
        store = memory_store_for('alice')
        first = await store.write('memory/garden.md', 'v1', expected_version=None)
        await store.write('memory/garden.md', 'v2', expected_version=first.version)
        with pytest.raises(MemoryConflictError):
            await store.write('memory/garden.md', 'v3', expected_version=first.version)

    async def test_invalid_slug_rejected(self):
        with pytest.raises(ValueError):
            memory_store_for('../etc')

    async def test_store_cached_per_user_and_reset(self, _rooted):
        a1 = memory_store_for('alice')
        assert memory_store_for('alice') is a1
        assert memory_store_for('bob') is not a1
        reset_memory_stores()
        assert memory_store_for('alice') is not a1


class TestHouseholdUnion:
    async def _seed_household(self, _rooted):
        h_dir = _rooted / 'users' / '_household' / 'memory'
        h_dir.mkdir(parents=True)
        (h_dir / 'wifi.md').write_text('---\nname: wifi\ntype: household\n---\nPassword: hunter2\n')

    async def test_household_readable_under_household_prefix(self, _rooted):
        await self._seed_household(_rooted)
        store = memory_store_for('alice')
        file = await store.read('memory/household.wifi.md', max_chars=10_000)
        assert file is not None and 'hunter2' in file.content

    async def test_household_writes_and_deletes_refused(self, _rooted):
        await self._seed_household(_rooted)
        store = memory_store_for('alice')
        with pytest.raises(PermissionError, match='zoo keeper'):
            await store.write('memory/household.wifi.md', 'evil', expected_version=None)
        with pytest.raises(PermissionError, match='zoo keeper'):
            await store.delete('memory/household.wifi.md', expected_version=None)
        assert 'hunter2' in (_rooted / 'users' / '_household' / 'memory' / 'wifi.md').read_text()

    async def test_listing_unions_household(self, _rooted):
        await self._seed_household(_rooted)
        store = memory_store_for('alice')
        await store.write('memory/own.md', 'mine', expected_version=None)
        paths = await store.list_paths('memory/', limit=50)
        assert 'memory/own.md' in paths
        assert 'memory/household.wifi.md' in paths

    async def test_search_unions_household_with_mapped_paths(self, _rooted):
        await self._seed_household(_rooted)
        store = memory_store_for('alice')
        result = await store.search(
            'memory/', 'hunter2', limit=5, max_files=100, max_chars=2_000, max_file_chars=10_000
        )
        assert any(m.path == 'memory/household.wifi.md' for m in result.matches)

    async def test_users_cannot_reach_each_other(self, _rooted):
        alice = memory_store_for('alice')
        await alice.write('memory/secret.md', 'alice only', expected_version=None)
        bob = memory_store_for('bob')
        assert await bob.read('memory/secret.md', max_chars=100) is None
