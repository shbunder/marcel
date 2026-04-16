#!/usr/bin/env python3
"""One-shot migration: move lessons-learned entries into their closed issue files.

Reads project/lessons-learned.md and project/lessons-learned-archive.md,
maps each entry to the matching closed issue file by ISSUE hash or number,
and appends a ## Lessons Learned section.  Skips files that already have one.

Usage:
    python scripts/migrate_lessons.py [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CLOSED_DIR = REPO_ROOT / "project" / "issues" / "closed"
LEGACY_FILES = [
    REPO_ROOT / "project" / "lessons-learned.md",
    REPO_ROOT / "project" / "lessons-learned-archive.md",
]

# Matches the header line of each entry, e.g.:
#   ## ISSUE-a7085c: Title (2026-04-16)
#   ## ISSUE-077: Post-076 audit (2026-04-14)
ENTRY_HEADER_RE = re.compile(r"^## (ISSUE-[^:]+):\s+(.+)$", re.MULTILINE)


def parse_entries(text: str) -> list[tuple[str, str, str]]:
    """Return list of (issue_id, title, body) tuples parsed from a lessons file.

    Body includes the three ### subsections but not the leading --- separator
    or the ## header line.
    """
    entries: list[tuple[str, str, str]] = []
    matches = list(ENTRY_HEADER_RE.finditer(text))
    for i, m in enumerate(matches):
        issue_id = m.group(1).strip()  # e.g. ISSUE-a7085c or ISSUE-077
        title_with_date = m.group(2).strip()
        # Strip trailing date like "(2026-04-16)" from title for cleanliness
        title = re.sub(r"\s*\(\d{4}-\d{2}-\d{2}\)\s*$", "", title_with_date).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        # Drop trailing --- separators that belong to this entry
        body = re.sub(r"(\n---\s*)+$", "", body).strip()
        entries.append((issue_id, title, body))
    return entries


def find_issue_file(issue_id: str) -> Path | None:
    """Find the closed issue file for the given ISSUE-{id}.

    Handles both new-style (ISSUE-YYMMDD-hash-slug) and legacy (ISSUE-NNN-slug).
    """
    # Extract the distinguishing part after "ISSUE-"
    # New hash style: ISSUE-a7085c → look for *-a7085c-* in filename
    # Legacy number: ISSUE-077 → look for ISSUE-077-* prefix
    raw = issue_id  # e.g. "ISSUE-a7085c" or "ISSUE-077"

    # Try exact prefix match first (legacy ISSUE-NNN)
    candidates = list(CLOSED_DIR.glob(f"{raw}-*.md"))
    if candidates:
        return candidates[0]

    # Try hash substring match (new-style: the hash appears between two dashes)
    # issue_id like ISSUE-a7085c → hash is a7085c
    parts = raw.split("-")
    if len(parts) == 2:
        hash_part = parts[1]
        candidates = list(CLOSED_DIR.glob(f"*-{hash_part}-*.md"))
        if candidates:
            return candidates[0]

    return None


def build_lessons_section(body: str) -> str:
    return f"\n## Lessons Learned\n\n{body}\n"


def already_has_lessons(issue_path: Path) -> bool:
    return "## Lessons Learned" in issue_path.read_text(encoding="utf-8")


def migrate(dry_run: bool = False) -> None:
    all_entries: list[tuple[str, str, str]] = []
    for legacy_file in LEGACY_FILES:
        if not legacy_file.exists():
            print(f"  [skip] {legacy_file.name} not found")
            continue
        text = legacy_file.read_text(encoding="utf-8")
        entries = parse_entries(text)
        print(f"  parsed {len(entries)} entries from {legacy_file.name}")
        all_entries.extend(entries)

    migrated = skipped_exists = skipped_no_file = 0

    for issue_id, title, body in all_entries:
        issue_file = find_issue_file(issue_id)
        if issue_file is None:
            print(f"  [no file] {issue_id}: {title}")
            skipped_no_file += 1
            continue
        if already_has_lessons(issue_file):
            print(f"  [exists]  {issue_id} → {issue_file.name}")
            skipped_exists += 1
            continue

        lessons_block = build_lessons_section(body)
        print(f"  [migrate] {issue_id} → {issue_file.name}")
        if not dry_run:
            with issue_file.open("a", encoding="utf-8") as f:
                f.write(lessons_block)
        migrated += 1

    print()
    print(f"Done: {migrated} migrated, {skipped_exists} already had section, "
          f"{skipped_no_file} no matching file found")
    if dry_run:
        print("(dry run — no files written)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen without writing files")
    args = parser.parse_args()
    migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
