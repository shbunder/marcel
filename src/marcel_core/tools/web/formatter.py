"""Format a list of :class:`SearchResult` objects for the model to read.

Stable text template so the model learns to parse it deterministically:

    Search results for "<query>" (via <backend>):

    1. <title>
       <url>
       <snippet>

    2. <title>
       <url>
       <snippet>

0-result and error outputs live in the dispatcher — this module only
formats the happy path.
"""

from __future__ import annotations

from marcel_core.tools.web.backends import SearchResult


def format_results(results: list[SearchResult], query: str, backend_name: str) -> str:
    """Render a result list into the stable text template.

    Caller is responsible for the zero-results case — this function
    assumes at least one result.
    """
    lines: list[str] = [f'Search results for "{query}" (via {backend_name}):', '']
    for idx, result in enumerate(results, start=1):
        lines.append(f'{idx}. {result.title}')
        lines.append(f'   {result.url}')
        if result.snippet:
            lines.append(f'   {result.snippet}')
        lines.append('')

    # Trim trailing blank line
    while lines and lines[-1] == '':
        lines.pop()

    return '\n'.join(lines)
