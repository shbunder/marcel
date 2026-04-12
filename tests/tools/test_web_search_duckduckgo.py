"""Tests for DuckDuckGoBackend — parser, decoders, bot-challenge detection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from marcel_core.tools.web.backends import SearchBackendError
from marcel_core.tools.web.duckduckgo import (
    DuckDuckGoBackend,
    decode_duckduckgo_url,
    decode_html_entities,
    is_bot_challenge,
    parse_duckduckgo_html,
    strip_html,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DDG_RESULTS_HTML = """
<html>
<body>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fone">Example One &mdash; Homepage</a>
  <a class="result__snippet" href="#">This is the <b>first</b> result &#39;snippet&#39;.</a>
</div>
<div class="result">
  <a class="result__a" href="https://example.com/two">Second &amp; Title</a>
  <a class="result__snippet" href="#">Snippet number two with &nbsp; spaces.</a>
</div>
<div class="result">
  <a class="result__a" href="https://example.com/three">Third Result</a>
  <a class="result__snippet" href="#">Third snippet.</a>
</div>
</body>
</html>
"""

_DDG_BOT_CHALLENGE_HTML = """
<html>
<body>
<h1>Are you a human?</h1>
<form id="challenge-form"></form>
<div class="g-recaptcha"></div>
</body>
</html>
"""

_DDG_NO_RESULTS_HTML = """
<html>
<body>
<div class="no-results">No results found for your query.</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# decode_html_entities
# ---------------------------------------------------------------------------


class TestDecodeHtmlEntities:
    def test_named_entities(self):
        assert decode_html_entities('Foo &amp; Bar') == 'Foo & Bar'
        assert decode_html_entities('&lt;tag&gt;') == '<tag>'
        assert decode_html_entities('&quot;quoted&quot;') == '"quoted"'
        assert decode_html_entities('&apos;single&apos;') == "'single'"
        assert decode_html_entities('&nbsp;space') == ' space'

    def test_apostrophe_variants(self):
        assert decode_html_entities('&#39;') == "'"
        assert decode_html_entities('&#x27;') == "'"

    def test_numeric_decimal(self):
        assert decode_html_entities('&#65;') == 'A'
        assert decode_html_entities('&#8364;') == '€'

    def test_numeric_hex(self):
        assert decode_html_entities('&#x41;') == 'A'
        assert decode_html_entities('&#x20ac;') == '€'

    def test_mixed(self):
        assert decode_html_entities('A &amp; B &#39;C&#39;') == "A & B 'C'"


# ---------------------------------------------------------------------------
# strip_html
# ---------------------------------------------------------------------------


class TestStripHtml:
    def test_strips_tags(self):
        assert strip_html('<b>bold</b>') == 'bold'
        assert strip_html('<a href="x">link</a>') == 'link'

    def test_collapses_whitespace(self):
        assert strip_html('  foo   bar  ') == 'foo bar'
        assert strip_html('line\n\n\nbreaks') == 'line breaks'

    def test_nested_tags(self):
        assert strip_html('<p>First</p><p>Second</p>') == 'First Second'


# ---------------------------------------------------------------------------
# decode_duckduckgo_url
# ---------------------------------------------------------------------------


class TestDecodeDuckDuckGoUrl:
    def test_extracts_uddg_param(self):
        raw = '//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&rut=abc'
        assert decode_duckduckgo_url(raw) == 'https://example.com/page'

    def test_passes_through_direct_urls(self):
        assert decode_duckduckgo_url('https://example.com/direct') == 'https://example.com/direct'

    def test_handles_protocol_relative(self):
        raw = '//duckduckgo.com/l/?uddg=https%3A%2F%2Ftarget.org'
        assert decode_duckduckgo_url(raw) == 'https://target.org'

    def test_malformed_url_returns_original(self):
        # No uddg param
        assert decode_duckduckgo_url('https://example.com/?q=foo') == 'https://example.com/?q=foo'


# ---------------------------------------------------------------------------
# is_bot_challenge
# ---------------------------------------------------------------------------


class TestIsBotChallenge:
    def test_detects_challenge_page(self):
        assert is_bot_challenge(_DDG_BOT_CHALLENGE_HTML) is True

    def test_normal_results_not_flagged(self):
        assert is_bot_challenge(_DDG_RESULTS_HTML) is False

    def test_detects_recaptcha_alone(self):
        html = '<html><body><div class="g-recaptcha"></div></body></html>'
        assert is_bot_challenge(html) is True

    def test_detects_challenge_form(self):
        html = '<html><body><form id="challenge-form"></form></body></html>'
        assert is_bot_challenge(html) is True

    def test_empty_results_not_challenge(self):
        assert is_bot_challenge(_DDG_NO_RESULTS_HTML) is False


# ---------------------------------------------------------------------------
# parse_duckduckgo_html
# ---------------------------------------------------------------------------


class TestParseDuckDuckGoHtml:
    def test_parses_three_results(self):
        results = parse_duckduckgo_html(_DDG_RESULTS_HTML)
        assert len(results) == 3

    def test_decodes_title_entities(self):
        results = parse_duckduckgo_html(_DDG_RESULTS_HTML)
        assert 'Second & Title' in [r.title for r in results]
        assert 'Example One -- Homepage' in [r.title for r in results]

    def test_decodes_snippet_entities(self):
        results = parse_duckduckgo_html(_DDG_RESULTS_HTML)
        assert any("'snippet'" in r.snippet for r in results)

    def test_decodes_redirect_urls(self):
        results = parse_duckduckgo_html(_DDG_RESULTS_HTML)
        assert results[0].url == 'https://example.com/one'
        assert results[1].url == 'https://example.com/two'

    def test_empty_html_returns_empty(self):
        assert parse_duckduckgo_html('') == []

    def test_no_results_html_returns_empty(self):
        assert parse_duckduckgo_html(_DDG_NO_RESULTS_HTML) == []


# ---------------------------------------------------------------------------
# DuckDuckGoBackend (mocked HTTP)
# ---------------------------------------------------------------------------


def _mock_client(response: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _html_response(status: int, body: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = body
    return resp


class TestDuckDuckGoBackendHttp:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        resp = _html_response(200, _DDG_RESULTS_HTML)
        client = _mock_client(resp)
        backend = DuckDuckGoBackend()

        with patch('marcel_core.tools.web.duckduckgo.httpx.AsyncClient', return_value=client):
            results = await backend.search('example', max_results=5)

        assert len(results) == 3
        assert results[0].url.startswith('https://example.com/')

    @pytest.mark.asyncio
    async def test_respects_max_results(self):
        resp = _html_response(200, _DDG_RESULTS_HTML)
        client = _mock_client(resp)
        backend = DuckDuckGoBackend()

        with patch('marcel_core.tools.web.duckduckgo.httpx.AsyncClient', return_value=client):
            results = await backend.search('example', max_results=2)

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_bot_challenge_raises(self):
        resp = _html_response(200, _DDG_BOT_CHALLENGE_HTML)
        client = _mock_client(resp)
        backend = DuckDuckGoBackend()

        with patch('marcel_core.tools.web.duckduckgo.httpx.AsyncClient', return_value=client):
            with pytest.raises(SearchBackendError) as exc_info:
                await backend.search('example', max_results=5)

        assert 'bot challenge' in exc_info.value.reason
        assert 'BRAVE_API_KEY' in exc_info.value.reason

    @pytest.mark.asyncio
    async def test_http_error_raises(self):
        resp = _html_response(503, '')
        client = _mock_client(resp)
        backend = DuckDuckGoBackend()

        with patch('marcel_core.tools.web.duckduckgo.httpx.AsyncClient', return_value=client):
            with pytest.raises(SearchBackendError) as exc_info:
                await backend.search('example', max_results=5)

        assert 'HTTP 503' in exc_info.value.reason

    @pytest.mark.asyncio
    async def test_network_error_raises(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError('DNS fail'))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        backend = DuckDuckGoBackend()

        with patch('marcel_core.tools.web.duckduckgo.httpx.AsyncClient', return_value=client):
            with pytest.raises(SearchBackendError) as exc_info:
                await backend.search('example', max_results=5)

        assert 'network failure' in exc_info.value.reason
