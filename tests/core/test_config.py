"""Tests for config.py edge cases."""

from __future__ import annotations

from pathlib import Path


class TestConfig:
    def test_data_dir_from_env(self):
        from marcel_core.config import Settings

        s = Settings(marcel_data_dir='/custom/path')
        assert s.data_dir == Path('/custom/path')

    def test_data_dir_default(self):
        from marcel_core.config import Settings

        s = Settings(marcel_data_dir='')
        assert s.data_dir == Path.home() / '.marcel'

    def test_cors_origins(self):
        from marcel_core.config import Settings

        s = Settings(marcel_cors_origins='http://localhost,http://example.com')
        assert s.cors_origins == ['http://localhost', 'http://example.com']
