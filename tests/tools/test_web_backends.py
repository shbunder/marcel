"""Tests for backend selection logic."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from marcel_core.tools.web.backends import SearchBackendError, select_backend
from marcel_core.tools.web.brave import BraveBackend
from marcel_core.tools.web.duckduckgo import DuckDuckGoBackend


class TestSelectBackend:
    def test_brave_selected_when_key_present(self):
        with patch('marcel_core.config.settings') as mock_settings:
            mock_settings.brave_api_key = 'test-key'
            mock_settings.web_search_backend = None
            backend = select_backend()
        assert isinstance(backend, BraveBackend)

    def test_ddg_fallback_when_no_key(self):
        with patch('marcel_core.config.settings') as mock_settings:
            mock_settings.brave_api_key = None
            mock_settings.web_search_backend = None
            backend = select_backend()
        assert isinstance(backend, DuckDuckGoBackend)

    def test_override_forces_ddg(self):
        with patch('marcel_core.config.settings') as mock_settings:
            mock_settings.brave_api_key = 'test-key'
            mock_settings.web_search_backend = 'duckduckgo'
            backend = select_backend()
        assert isinstance(backend, DuckDuckGoBackend)

    def test_override_forces_brave(self):
        with patch('marcel_core.config.settings') as mock_settings:
            mock_settings.brave_api_key = 'test-key'
            mock_settings.web_search_backend = 'brave'
            backend = select_backend()
        assert isinstance(backend, BraveBackend)

    def test_brave_override_without_key_raises(self):
        with patch('marcel_core.config.settings') as mock_settings:
            mock_settings.brave_api_key = None
            mock_settings.web_search_backend = 'brave'
            with pytest.raises(SearchBackendError) as exc_info:
                select_backend()
        assert 'BRAVE_API_KEY' in exc_info.value.reason

    def test_unknown_backend_raises(self):
        with patch('marcel_core.config.settings') as mock_settings:
            mock_settings.brave_api_key = 'test-key'
            mock_settings.web_search_backend = 'bogus'
            with pytest.raises(SearchBackendError) as exc_info:
                select_backend()
        assert 'unknown' in exc_info.value.reason.lower()
