"""Tests for BrowserManager and snapshot formatting."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from marcel_core.browser.manager import _build_aria_selector, build_snapshot


def _mock_page(snapshot_return=None, snapshot_error=None):
    """Create a mock page with a configurable accessibility.snapshot() return."""
    mock_snapshot = AsyncMock()
    if snapshot_error:
        mock_snapshot.side_effect = snapshot_error
    else:
        mock_snapshot.return_value = snapshot_return

    return SimpleNamespace(accessibility=SimpleNamespace(snapshot=mock_snapshot))


class TestBuildAriaSelector:
    def test_role_and_name(self):
        result = _build_aria_selector({'role': 'button', 'name': 'Submit'})
        assert result == 'role=button[name="Submit"]'

    def test_role_only(self):
        result = _build_aria_selector({'role': 'heading', 'name': ''})
        assert result == 'role=heading'

    def test_empty(self):
        result = _build_aria_selector({'role': '', 'name': ''})
        assert result == ''

    def test_name_with_quotes(self):
        result = _build_aria_selector({'role': 'button', 'name': 'Say "hello"'})
        assert result == 'role=button[name="Say \\"hello\\""]'


class TestBuildSnapshot:
    """Test the snapshot builder with mock Page objects."""

    async def test_empty_page(self):
        """Empty accessibility snapshot returns empty page message."""
        page = _mock_page(snapshot_return=None)
        text, ref_map = await build_snapshot(page)
        assert text == '(Empty page)'
        assert ref_map == {}

    async def test_simple_tree(self):
        """Simple accessibility tree is formatted correctly."""
        tree = {
            'role': 'WebArea',
            'name': 'Test Page',
            'children': [
                {'role': 'heading', 'name': 'Welcome', 'children': []},
                {'role': 'textbox', 'name': 'Email', 'focused': True, 'children': []},
                {'role': 'button', 'name': 'Submit', 'children': []},
            ],
        }
        page = _mock_page(snapshot_return=tree)
        text, ref_map = await build_snapshot(page)

        assert '[1] WebArea "Test Page"' in text
        assert '[2] heading "Welcome"' in text
        assert '[3] textbox "Email" focused' in text
        assert '[4] button "Submit"' in text

        assert len(ref_map) == 4
        assert ref_map[4]['role'] == 'button'
        assert ref_map[4]['name'] == 'Submit'

    async def test_generic_nodes_skipped(self):
        """Generic/none roles without names are skipped."""
        tree = {
            'role': 'WebArea',
            'name': 'Test',
            'children': [
                {
                    'role': 'generic',
                    'name': '',
                    'children': [
                        {'role': 'button', 'name': 'Click Me'},
                    ],
                },
            ],
        }
        page = _mock_page(snapshot_return=tree)
        text, ref_map = await build_snapshot(page)

        assert 'generic' not in text
        assert 'button "Click Me"' in text

    async def test_long_name_truncated(self):
        """Names longer than 80 chars are truncated."""
        tree = {'role': 'heading', 'name': 'A' * 100}
        page = _mock_page(snapshot_return=tree)
        text, _ = await build_snapshot(page)
        assert '...' in text

    async def test_checked_attribute(self):
        """Checked state is included in output."""
        tree = {'role': 'checkbox', 'name': 'Accept terms', 'checked': True}
        page = _mock_page(snapshot_return=tree)
        text, _ = await build_snapshot(page)
        assert 'checked' in text

    async def test_unchecked_attribute(self):
        """Unchecked state is included in output."""
        tree = {'role': 'checkbox', 'name': 'Accept terms', 'checked': False}
        page = _mock_page(snapshot_return=tree)
        text, _ = await build_snapshot(page)
        assert 'unchecked' in text

    async def test_value_attribute(self):
        """Value attribute is included in output."""
        tree = {'role': 'textbox', 'name': 'Search', 'value': 'hello world'}
        page = _mock_page(snapshot_return=tree)
        text, _ = await build_snapshot(page)
        assert 'value="hello world"' in text

    async def test_exception_handling(self):
        """Exception during accessibility snapshot returns error message."""
        page = _mock_page(snapshot_error=RuntimeError('Browser not ready'))
        text, ref_map = await build_snapshot(page)
        assert 'Could not read' in text
        assert ref_map == {}
