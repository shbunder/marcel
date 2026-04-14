"""Tests for BrowserManager and snapshot formatting."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from marcel_core.tools.browser.manager import (
    _build_aria_selector,
    _is_sparse_snapshot,
    build_snapshot,
    extract_readable,
)


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


class TestIsSparseSnapshot:
    def test_empty_sentinel_is_sparse(self):
        assert _is_sparse_snapshot('(Empty page)') is True

    def test_a11y_error_sentinel_is_sparse(self):
        assert _is_sparse_snapshot('(Could not read page accessibility tree)') is True

    def test_few_lines_is_sparse(self):
        assert _is_sparse_snapshot('[1] main\n[2] generic') is True

    def test_rich_snapshot_is_not_sparse(self):
        text = '\n'.join(
            [
                '[1] main',
                '[2] heading "Welcome"',
                '[3] button "Sign In"',
                '[4] link "Home"',
                '[5] textbox "Email"',
                '[6] paragraph',
            ]
        )
        assert _is_sparse_snapshot(text) is False

    def test_blank_lines_dont_count(self):
        # Only real content lines are meaningful.
        assert _is_sparse_snapshot('\n\n\n[1] main\n\n') is True


class TestExtractReadable:
    """extract_readable() on React/styled-component-shaped HTML."""

    REACT_HTML = """
    <!DOCTYPE html><html><head><title>HelloFresh recipes</title></head>
    <body><div id="__next">
      <div class="sc-abc xyz"><main>
        <article class="sc-def uvw">
          <h2>Kip teriyaki met rijst</h2>
          <p>30 minuten. Sappige kipfilet in een zelfgemaakte teriyakisaus met pandanrijst
          en knapperige broccoliroosjes. Een snelle aziatische klassieker.</p>
        </article>
        <article class="sc-def uvw">
          <h2>Pasta pesto met tomaten</h2>
          <p>25 minuten. Verse tagliatelle met romige pestosaus, zongedroogde tomaten,
          pijnboompitten en een handvol rucola. Italiaanse comfort food.</p>
        </article>
      </main></div>
    </div></body></html>
    """

    async def test_extracts_react_styled_prose(self):
        page = SimpleNamespace(content=AsyncMock(return_value=self.REACT_HTML))
        result = await extract_readable(page)
        assert 'teriyakisaus' in result.lower()
        assert 'pestosaus' in result.lower()

    async def test_falls_back_to_inner_text_when_trafilatura_empty(self):
        """Tiny non-article HTML → Trafilatura returns None → inner_text fallback."""
        page = SimpleNamespace(
            content=AsyncMock(return_value='<html><body></body></html>'),
            inner_text=AsyncMock(return_value='  plain body text from fallback  '),
        )
        result = await extract_readable(page)
        assert 'plain body text from fallback' in result

    async def test_returns_sentinel_when_page_content_fails(self):
        page = SimpleNamespace(content=AsyncMock(side_effect=RuntimeError('no page')))
        result = await extract_readable(page)
        assert result == '(Could not read page content)'

    async def test_returns_sentinel_when_both_extractors_empty(self):
        page = SimpleNamespace(
            content=AsyncMock(return_value='<html></html>'),
            inner_text=AsyncMock(return_value=''),
        )
        result = await extract_readable(page)
        assert result == '(Empty page content)'

    async def test_truncates_long_output(self):
        # Build an HTML blob that Trafilatura will extract and that exceeds the
        # 8000-char budget, forcing the truncation branch.
        paragraphs = ''.join(
            f'<p>This is paragraph number {i} with some Dutch recipe content: '
            f'lekkere maaltijd met verse groenten en kruiden.</p>'
            for i in range(500)
        )
        html = f'<html><body><article>{paragraphs}</article></body></html>'
        page = SimpleNamespace(content=AsyncMock(return_value=html))
        result = await extract_readable(page)
        assert result.endswith('... (truncated)')
        assert len(result) <= 8100  # 8000 budget + truncation marker
