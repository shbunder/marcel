"""Scenario-based tests for jobs/cache.py and jobs/templates.py."""

from __future__ import annotations

import pytest

from marcel_core.storage import _root


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class TestJobCache:
    def test_write_and_read(self):
        from marcel_core.jobs.cache import read_cache, write_cache

        write_cache('alice', 'news', {'articles': [1, 2, 3]})
        entry = read_cache('alice', 'news')
        assert entry is not None
        assert entry['key'] == 'news'
        assert entry['data'] == {'articles': [1, 2, 3]}
        assert 'updated_at' in entry

    def test_read_nonexistent_returns_none(self):
        from marcel_core.jobs.cache import read_cache

        assert read_cache('alice', 'nope') is None

    def test_overwrite(self):
        from marcel_core.jobs.cache import read_cache, write_cache

        write_cache('alice', 'key', 'v1')
        write_cache('alice', 'key', 'v2')
        entry = read_cache('alice', 'key')
        assert entry is not None
        assert entry['data'] == 'v2'

    def test_list_keys(self):
        from marcel_core.jobs.cache import list_cache_keys, write_cache

        write_cache('alice', 'alpha', 'a')
        write_cache('alice', 'beta', 'b')
        keys = list_cache_keys('alice')
        assert keys == ['alpha', 'beta']

    def test_list_keys_empty(self):
        from marcel_core.jobs.cache import list_cache_keys

        assert list_cache_keys('alice') == []

    def test_corrupt_cache_returns_none(self, tmp_path):
        from marcel_core.jobs.cache import read_cache

        cache_dir = tmp_path / 'users' / 'alice' / 'job_cache'
        cache_dir.mkdir(parents=True)
        (cache_dir / 'bad.json').write_text('not json')
        assert read_cache('alice', 'bad') is None


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


class TestTemplates:
    def test_get_template_known(self):
        from marcel_core.jobs.templates import get_template

        tpl = get_template('sync')
        assert tpl is not None
        assert 'description' in tpl
        assert tpl['notify'] == 'on_failure'

    def test_get_template_unknown(self):
        from marcel_core.jobs.templates import get_template

        assert get_template('nonexistent') is None

    def test_list_templates(self):
        from marcel_core.jobs.templates import list_templates

        templates = list_templates()
        assert len(templates) >= 4
        names = {t['name'] for t in templates}
        assert {'sync', 'check', 'scrape', 'digest'} <= names
        for t in templates:
            assert 'name' in t
            assert 'description' in t

    def test_all_templates_have_required_keys(self):
        from marcel_core.jobs.templates import TEMPLATES

        for name, tpl in TEMPLATES.items():
            assert 'description' in tpl, f'{name} missing description'
            assert 'default_trigger' in tpl, f'{name} missing default_trigger'
            assert 'system_prompt' in tpl, f'{name} missing system_prompt'
            assert 'notify' in tpl, f'{name} missing notify'
            assert 'model' in tpl, f'{name} missing model'
