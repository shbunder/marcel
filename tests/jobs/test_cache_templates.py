"""Scenario-based tests for jobs/cache.py and jobs/templates.py."""

from __future__ import annotations

import textwrap

import pytest

from marcel_core.storage import _root


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
    # The template loader also reads the zoo — point it somewhere empty so
    # these tests control the entire template universe via the data root.
    from marcel_core.config import settings

    monkeypatch.setattr(settings, 'marcel_zoo_dir', None, raising=False)


def _write_template(tmp_path, name: str, body: str) -> None:
    habitat = tmp_path / 'jobs' / name
    habitat.mkdir(parents=True, exist_ok=True)
    (habitat / 'template.yaml').write_text(textwrap.dedent(body))


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
# Templates (habitat-backed)
# ---------------------------------------------------------------------------


_SYNC_YAML = """
description: Periodically sync data from an external service.
default_trigger:
  type: interval
  interval_seconds: 28800
system_prompt: |
  You are a background sync worker for Marcel.
task_template: 'Run {skill} now and report the results.'
notify: on_failure
model: anthropic:claude-haiku-4-5-20251001
"""


class TestTemplates:
    def test_get_template_known(self, tmp_path):
        _write_template(tmp_path, 'sync', _SYNC_YAML)

        from marcel_core.jobs.templates import get_template

        tpl = get_template('sync')
        assert tpl is not None
        assert tpl['description'].startswith('Periodically sync')
        assert tpl['notify'] == 'on_failure'

    def test_get_template_unknown(self):
        from marcel_core.jobs.templates import get_template

        assert get_template('nonexistent') is None

    def test_list_templates(self, tmp_path):
        _write_template(tmp_path, 'sync', _SYNC_YAML)
        _write_template(
            tmp_path,
            'check',
            """
            description: Check a condition.
            default_trigger: {type: event}
            system_prompt: Monitor.
            notify: on_output
            model: anthropic:claude-haiku-4-5-20251001
            """,
        )

        from marcel_core.jobs.templates import list_templates

        templates = list_templates()
        names = {t['name'] for t in templates}
        assert {'sync', 'check'} <= names
        for t in templates:
            assert 'name' in t
            assert 'description' in t

    def test_empty_when_no_sources(self):
        from marcel_core.jobs.templates import TEMPLATES, list_templates

        assert list_templates() == []
        assert TEMPLATES == {}

    def test_template_missing_required_key_is_skipped(self, tmp_path, caplog):
        # No 'model' key → should be rejected and not appear in the dict.
        _write_template(
            tmp_path,
            'broken',
            """
            description: broken template
            default_trigger: {type: event}
            system_prompt: s
            notify: silent
            """,
        )
        import logging

        with caplog.at_level(logging.ERROR, logger='marcel_core.plugin.jobs'):
            from marcel_core.jobs.templates import get_template

            assert get_template('broken') is None
        assert any("'broken'" in m and 'model' in m for m in caplog.messages)

    def test_data_root_overrides_zoo(self, tmp_path, monkeypatch):
        # Zoo ships 'sync' with notify=always; user overrides with notify=silent.
        zoo = tmp_path / 'zoo'
        (zoo / 'jobs' / 'sync').mkdir(parents=True)
        (zoo / 'jobs' / 'sync' / 'template.yaml').write_text(
            textwrap.dedent(
                """
                description: zoo sync
                default_trigger: {type: interval, interval_seconds: 60}
                system_prompt: from zoo
                notify: always
                model: anthropic:claude-haiku-4-5-20251001
                """
            )
        )
        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo), raising=False)

        # User override in data_root.
        _write_template(
            tmp_path,
            'sync',
            """
            description: user sync
            default_trigger: {type: interval, interval_seconds: 60}
            system_prompt: from user
            notify: silent
            model: anthropic:claude-haiku-4-5-20251001
            """,
        )

        from marcel_core.jobs.templates import get_template

        tpl = get_template('sync')
        assert tpl is not None
        assert tpl['description'] == 'user sync'
        assert tpl['notify'] == 'silent'

    def test_instance_directory_without_template_is_ignored(self, tmp_path):
        # A data-root jobs/<slug>/ dir with JOB.md but no template.yaml is a
        # job *instance*, not a template — the loader must skip it.
        instance = tmp_path / 'jobs' / 'alice-news'
        instance.mkdir(parents=True)
        (instance / 'JOB.md').write_text('---\nid: abc\nname: alice-news\n---\n')

        from marcel_core.jobs.templates import list_templates

        assert list_templates() == []
