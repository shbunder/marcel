"""DuckDuckGo HTML search backend.

Fallback backend used when ``BRAVE_API_KEY`` is not set. Scrapes the
``html.duckduckgo.com/html`` endpoint with a browser-like User-Agent and
parses the result HTML with regexes.

Port of openclaw's ``extensions/duckduckgo/src/ddg-client.ts`` (MIT
licensed). The parser, entity decoder, redirect decoder, and bot-challenge
detector all mirror the original — see that file for comment history on
edge cases.

**Reliability note**: DDG bot-challenges unpredictably and the HTML
structure can change without notice. Treat every error from this backend
as a suggestion to set ``BRAVE_API_KEY``.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse

import httpx

from marcel_core.tools.web.backends import SearchBackend, SearchBackendError, SearchResult

log = logging.getLogger(__name__)

_DDG_HTML_ENDPOINT = 'https://html.duckduckgo.com/html'
_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'

_RESULT_RE = re.compile(
    r'<a\b(?=[^>]*\bclass="[^"]*\bresult__a\b[^"]*")([^>]*)>([\s\S]*?)</a>',
    re.IGNORECASE,
)
_NEXT_RESULT_RE = re.compile(
    r'<a\b(?=[^>]*\bclass="[^"]*\bresult__a\b[^"]*")[^>]*>',
    re.IGNORECASE,
)
_SNIPPET_RE = re.compile(
    r'<a\b(?=[^>]*\bclass="[^"]*\bresult__snippet\b[^"]*")[^>]*>([\s\S]*?)</a>',
    re.IGNORECASE,
)
_HREF_RE = re.compile(r'\bhref="([^"]*)"', re.IGNORECASE)
_TAG_RE = re.compile(r'<[^>]+>')
_WHITESPACE_RE = re.compile(r'\s+')

_BOT_CHALLENGE_HINT_RE = re.compile(
    r'g-recaptcha|are you a human|id="challenge-form"|name="challenge"',
    re.IGNORECASE,
)
_RESULT_CLASS_HINT_RE = re.compile(r'class="[^"]*\bresult__a\b[^"]*"', re.IGNORECASE)


_HTML_ENTITIES: dict[str, str] = {
    '&amp;': '&',
    '&lt;': '<',
    '&gt;': '>',
    '&quot;': '"',
    '&apos;': "'",
    '&#39;': "'",
    '&#x27;': "'",
    '&#x2F;': '/',
    '&nbsp;': ' ',
    '&ndash;': '-',
    '&mdash;': '--',
    '&hellip;': '...',
}
_NUMERIC_ENTITY_RE = re.compile(r'&#(\d+);')
_HEX_ENTITY_RE = re.compile(r'&#x([0-9a-f]+);', re.IGNORECASE)


def decode_html_entities(text: str) -> str:
    """Decode a small set of named + numeric HTML entities.

    Direct port of openclaw's ``decodeHtmlEntities``. Only covers what
    shows up in DDG result pages — not a general-purpose HTML parser.
    """
    out = text
    for entity, replacement in _HTML_ENTITIES.items():
        out = out.replace(entity, replacement)
    out = _NUMERIC_ENTITY_RE.sub(lambda m: chr(int(m.group(1))), out)
    out = _HEX_ENTITY_RE.sub(lambda m: chr(int(m.group(1), 16)), out)
    return out


def strip_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    return _WHITESPACE_RE.sub(' ', _TAG_RE.sub(' ', html)).strip()


def decode_duckduckgo_url(raw_url: str) -> str:
    """Extract the real target URL from a DDG redirect link.

    DDG wraps result URLs in ``/l/?uddg=<encoded>``. Parse the query
    string and pull out the ``uddg`` parameter. If the URL is not a
    redirect, return it unchanged.
    """
    try:
        normalized = 'https:' + raw_url if raw_url.startswith('//') else raw_url
        parsed = urlparse(normalized)
        qs = parse_qs(parsed.query)
        uddg = qs.get('uddg')
        if uddg:
            return uddg[0]
    except (ValueError, UnicodeError):
        pass
    return raw_url


def is_bot_challenge(html: str) -> bool:
    """Return True when DDG served a captcha / challenge page instead of results.

    Normal result pages contain ``class="...result__a..."`` markers. If
    those are absent AND the page contains recaptcha / challenge markers,
    treat it as a bot challenge.
    """
    if _RESULT_CLASS_HINT_RE.search(html):
        return False
    return bool(_BOT_CHALLENGE_HINT_RE.search(html))


def parse_duckduckgo_html(html: str) -> list[SearchResult]:
    """Parse a DDG HTML results page into :class:`SearchResult` objects.

    Walks each ``result__a`` match, extracts the title from the link
    body, the URL from the ``href`` (decoded through
    :func:`decode_duckduckgo_url`), and the snippet from the next
    ``result__snippet`` block before the following result starts.
    """
    results: list[SearchResult] = []

    for match in _RESULT_RE.finditer(html):
        raw_attributes = match.group(1) or ''
        raw_title = match.group(2) or ''
        href_match = _HREF_RE.search(raw_attributes)
        raw_url = href_match.group(1) if href_match else ''

        match_end = match.end()
        trailing = html[match_end:]
        next_result = _NEXT_RESULT_RE.search(trailing)
        scoped_trailing = trailing[: next_result.start()] if next_result else trailing

        snippet_match = _SNIPPET_RE.search(scoped_trailing)
        raw_snippet = snippet_match.group(1) if snippet_match else ''

        title = decode_html_entities(strip_html(raw_title))
        url = decode_duckduckgo_url(decode_html_entities(raw_url))
        snippet = decode_html_entities(strip_html(raw_snippet))

        if title and url:
            results.append(SearchResult(title=title, url=url, snippet=snippet))

    return results


class DuckDuckGoBackend(SearchBackend):
    """DuckDuckGo HTML scraping backend."""

    name = 'duckduckgo'

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        params = {'q': query, 'kp': '-1'}  # kp=-1 → moderate safe-search
        headers = {'User-Agent': _USER_AGENT}

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(_DDG_HTML_ENDPOINT, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise SearchBackendError(f'network failure — {exc}') from exc

        if resp.status_code >= 400:
            raise SearchBackendError(f'DuckDuckGo HTTP {resp.status_code}')

        html = resp.text
        if is_bot_challenge(html):
            raise SearchBackendError('DuckDuckGo bot challenge — set BRAVE_API_KEY for reliable search')

        return parse_duckduckgo_html(html)[:max_results]
