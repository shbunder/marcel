"""Tests for the web search result formatter."""

from __future__ import annotations

from marcel_core.tools.web.backends import SearchResult
from marcel_core.tools.web.formatter import format_results


def _result(idx: int) -> SearchResult:
    return SearchResult(
        title=f'Title {idx}',
        url=f'https://example.com/{idx}',
        snippet=f'Snippet number {idx}.',
    )


class TestFormatResults:
    def test_single_result(self):
        output = format_results([_result(1)], 'test query', 'brave')
        assert 'Search results for "test query" (via brave):' in output
        assert '1. Title 1' in output
        assert 'https://example.com/1' in output
        assert 'Snippet number 1.' in output

    def test_multiple_results_numbered(self):
        output = format_results([_result(1), _result(2), _result(3)], 'query', 'duckduckgo')
        assert '1. Title 1' in output
        assert '2. Title 2' in output
        assert '3. Title 3' in output
        assert '(via duckduckgo)' in output

    def test_result_without_snippet(self):
        result = SearchResult(title='No snippet', url='https://example.com', snippet='')
        output = format_results([result], 'q', 'brave')
        assert '1. No snippet' in output
        assert 'https://example.com' in output
        # No extra blank snippet line
        lines = output.split('\n')
        # Find the title line and assert the next line is the URL, not empty
        for i, line in enumerate(lines):
            if '1. No snippet' in line:
                assert lines[i + 1].strip() == 'https://example.com'

    def test_query_with_quotes_escaped_via_format(self):
        output = format_results([_result(1)], 'a "tricky" query', 'brave')
        assert 'a "tricky" query' in output

    def test_trailing_blank_trimmed(self):
        output = format_results([_result(1)], 'q', 'brave')
        assert not output.endswith('\n\n')
