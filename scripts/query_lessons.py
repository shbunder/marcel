#!/usr/bin/env python3
"""
Search lessons learned across all closed issue files.

Greps the ## Lessons Learned section of every closed issue for the given
keywords, scores by hit count, and prints matches sorted by date (most
recent first).

Usage:
    python scripts/query_lessons.py <keyword> [keyword ...]
    python scripts/query_lessons.py scheduler timeout --top 5
    python scripts/query_lessons.py auth --since 260101

Keywords are matched case-insensitively against the full Lessons Learned
section.  Multiple keywords are OR-combined for filtering; score is the
total hit count across all keywords.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CLOSED_DIR = REPO_ROOT / 'project' / 'issues' / 'closed'

# Legacy files that predate per-issue sections — searched as a fallback
LEGACY_FILES = [
    REPO_ROOT / 'project' / 'lessons-learned.md',
    REPO_ROOT / 'project' / 'lessons-learned-archive.md',
]

# Matches new-style filenames: ISSUE-260416-a7085c-slug.md
NEW_STYLE_RE = re.compile(r'ISSUE-(\d{6})-[0-9a-f]{6}-')

# Matches Created date inside file header: **Created:** 2026-04-16
CREATED_DATE_RE = re.compile(r'\*\*Created:\*\*\s*(\d{4}-\d{2}-\d{2})')

LESSONS_SECTION_RE = re.compile(
    r'^## Lessons Learned\s*\n(.*?)(?=\n## |\Z)',
    re.MULTILINE | re.DOTALL,
)


@dataclass
class Match:
    issue_id: str
    title: str
    issue_date: date
    body: str
    score: int = 0
    source: str = ''


def extract_date(path: Path, text: str) -> date:
    """Parse the issue date from filename (preferred) or Created: field."""
    m = NEW_STYLE_RE.search(path.name)
    if m:
        raw = m.group(1)  # e.g. "260416"
        yy, mm, dd = int(raw[:2]), int(raw[2:4]), int(raw[4:])
        return date(2000 + yy, mm, dd)
    # Legacy: look inside the file
    m = CREATED_DATE_RE.search(text)
    if m:
        return date.fromisoformat(m.group(1))
    return date(2000, 1, 1)


def extract_issue_id(path: Path) -> str:
    """Return short ISSUE-{hash} or ISSUE-{NNN} identifier."""
    name = path.stem  # e.g. ISSUE-260416-a7085c-slug or ISSUE-077-slug
    parts = name.split('-')
    # New style: ISSUE + YYMMDD + hash + slug...
    if len(parts) >= 3 and len(parts[1]) == 6 and parts[1].isdigit():
        return f'ISSUE-{parts[2]}'
    # Legacy: ISSUE + NNN + slug...
    if len(parts) >= 2:
        return f'ISSUE-{parts[1]}'
    return path.stem


def extract_title(text: str) -> str:
    """Pull the title from the first # heading."""
    m = re.search(r'^# ISSUE-[^:]+:\s*(.+)$', text, re.MULTILINE)
    return m.group(1).strip() if m else 'Untitled'


def score_text(text: str, keywords: list[str]) -> int:
    """Count total keyword occurrences (case-insensitive)."""
    lower = text.lower()
    return sum(lower.count(kw.lower()) for kw in keywords)


def search_issue_files(keywords: list[str], since: date | None) -> list[Match]:
    matches: list[Match] = []
    for path in sorted(CLOSED_DIR.glob('*.md')):
        text = path.read_text(encoding='utf-8')
        m = LESSONS_SECTION_RE.search(text)
        if not m:
            continue
        body = m.group(1).strip()
        if not body:
            continue
        # Filter by date
        issue_date = extract_date(path, text)
        if since and issue_date < since:
            continue
        # Filter and score by keywords
        if keywords:
            s = score_text(body, keywords)
            if s == 0:
                continue
        else:
            s = 0
        matches.append(
            Match(
                issue_id=extract_issue_id(path),
                title=extract_title(text),
                issue_date=issue_date,
                body=body,
                score=s,
                source=path.name,
            )
        )
    return matches


def search_legacy_files(keywords: list[str], since: date | None) -> list[Match]:
    """Search legacy global lessons files for entries not yet migrated."""
    matches: list[Match] = []
    entry_re = re.compile(
        r'^## (ISSUE-[^:]+):\s+(.+?)\s*\((\d{4}-\d{2}-\d{2})\)\s*\n(.*?)(?=\n---|\Z)',
        re.MULTILINE | re.DOTALL,
    )
    for legacy_path in LEGACY_FILES:
        if not legacy_path.exists():
            continue
        text = legacy_path.read_text(encoding='utf-8')
        for m in entry_re.finditer(text):
            issue_id = m.group(1).strip()
            title = m.group(2).strip()
            issue_date = date.fromisoformat(m.group(3))
            body = m.group(4).strip()
            if since and issue_date < since:
                continue
            if keywords:
                s = score_text(body, keywords)
                if s == 0:
                    continue
            else:
                s = 0
            matches.append(
                Match(
                    issue_id=issue_id,
                    title=title,
                    issue_date=issue_date,
                    body=body,
                    score=s,
                    source=f'[legacy] {legacy_path.name}',
                )
            )
    return matches


def deduplicate(matches: list[Match]) -> list[Match]:
    """Remove duplicates — issue file wins over legacy entry."""
    seen: set[str] = set()
    out: list[Match] = []
    # Issue-file matches come first (legacy appended at end)
    for m in matches:
        if m.issue_id not in seen:
            seen.add(m.issue_id)
            out.append(m)
    return out


def format_match(m: Match, show_source: bool = False) -> str:
    lines = [f'=== {m.issue_id} ({m.issue_date}) — {m.title}']
    if show_source:
        lines.append(f'    source: {m.source}')
    lines.append('')
    lines.append(m.body)
    lines.append('')
    return '\n'.join(lines)


def parse_since(raw: str) -> date:
    """Parse YYMMDD into a date."""
    if len(raw) != 6 or not raw.isdigit():
        raise argparse.ArgumentTypeError(f'--since must be YYMMDD (e.g. 260101), got: {raw!r}')
    yy, mm, dd = int(raw[:2]), int(raw[2:4]), int(raw[4:])
    return date(2000 + yy, mm, dd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('keywords', nargs='*', help='Keywords to search for (OR-combined)')
    parser.add_argument('--top', type=int, default=0, help='Show only the top N matches (0 = all)')
    parser.add_argument(
        '--since',
        type=parse_since,
        default=None,
        metavar='YYMMDD',
        help='Only show issues on or after this date (e.g. 260101)',
    )
    parser.add_argument('--source', action='store_true', help='Show source filename for each match')
    args = parser.parse_args()

    keywords: list[str] = args.keywords

    issue_matches = search_issue_files(keywords, args.since)
    legacy_matches = search_legacy_files(keywords, args.since)

    all_matches = deduplicate(issue_matches + legacy_matches)

    if not all_matches:
        print('No lessons found matching your query.')
        return

    # Sort: by score desc (if keywords given), then date desc
    all_matches.sort(
        key=lambda m: (m.score if keywords else 0, m.issue_date),
        reverse=True,
    )

    if args.top:
        all_matches = all_matches[: args.top]

    print(f'Found {len(all_matches)} match(es):\n')
    for m in all_matches:
        print(format_match(m, show_source=args.source))
        print('-' * 60)


if __name__ == '__main__':
    main()
