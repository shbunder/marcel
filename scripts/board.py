#!/usr/bin/env python3
"""Marcel's board CLI: list stories, show lanes, and check that a story may start.

Two sources, by how often they change:

- **Definitions** live in git, one file per story: `project/stories/<id>.md`. The frontmatter holds
  id, title, feature, wave, runs_on, size, depends, touches and behaviours. No status.
- **Status** lives on the build board, a claude.ai artifact (URL in `.claude/rules/board.md`). A
  Claude session exports it with ArtifactData (`list stories`, `out_dir: .board/export`), which
  writes `.board/export/stories/<id>.json`; this CLI reads that export. It never writes status:
  claims and moves are ArtifactData writes, made by the /story, /finish-story and /merge-story
  skills.

The mechanical rules are enforced here, not remembered:

- a story starts only when everything it depends on is Done;
- it never starts while another In Progress or Review story touches an overlapping path;
- no more than WIP_CAP stories are In Progress or in Review at once.

Stdlib only, so it runs before any `uv sync`.

    python3 scripts/board.py list [--wave 1] [--status Backlog] [--runs-on cloud]
    python3 scripts/board.py lanes
    python3 scripts/board.py ready [--runs-on Mac]
    python3 scripts/board.py start S-03.2          # checks only; prints the claim to write
    python3 scripts/board.py rows .board/plan      # definitions as board rows, one JSON per story
    python3 scripts/board.py sync .board/sync.json # ArtifactData writes to match the board
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORIES = ROOT / 'project' / 'stories'
FEATURES = ROOT / 'project' / 'features'
EXPORT = ROOT / '.board' / 'export' / 'stories'

STATUSES = ('Backlog', 'In Progress', 'Review', 'Blocked', 'Done')
IN_FLIGHT = ('In Progress', 'Review')
# Fields that come from git. Everything else on a board row (status, owner, branch, pr,
# blocked_reason, updated_at) belongs to the board and a sync never touches it.
DEFINITION_FIELDS = (
    'title',
    'feature',
    'feature_title',
    'wave',
    'runs_on',
    'size',
    'depends',
    'touches',
    'behaviours',
    'source',
)
# Six, per the playbook: past that, conflicts on shared files cost more than parallelism saves.
WIP_CAP = 6
GLOB_CHARS = re.compile(r'[*?\[]')
# Touches that are not paths ("everything (one-off)") overlap with every other story.
WILDCARD_WORDS = {'everything', '(one-off)'}
EXPORT_HELP = (
    'Export the board first: ArtifactData list, collection "stories", query {limit: 1000}, '
    'out_dir ".board/export" (the /board skill does this).'
)


@dataclass
class Story:
    id: str
    title: str
    path: Path
    fields: dict[str, str] = field(default_factory=dict)
    board: dict[str, object] | None = None

    @property
    def status(self) -> str:
        return str((self.board or {}).get('status', 'Backlog'))

    @property
    def on_board(self) -> bool:
        return self.board is not None

    @property
    def depends(self) -> list[str]:
        return split_list(self.fields.get('depends', ''))

    @property
    def touches(self) -> list[str]:
        return self.fields.get('touches', '').split()


def split_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(',') if item.strip()]


def read_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith('---\n'):
        return {}
    header, _, _ = text[4:].partition('\n---\n')
    fields: dict[str, str] = {}
    for line in header.splitlines():
        key, sep, value = line.partition(':')
        if sep:
            fields[key.strip()] = value.strip()
    return fields


class BoardError(SystemExit):
    """A story file or board export the CLI cannot trust. Raised loudly: a silently skipped story
    escapes the dependency and overlap checks."""


def load_definitions(stories_dir: Path = STORIES) -> dict[str, Story]:
    stories: dict[str, Story] = {}
    for path in sorted(stories_dir.glob('*.md')):
        fields = read_frontmatter(path.read_text())
        if 'id' not in fields:
            raise BoardError(f'{path.name} has no frontmatter id; fix or remove it.')
        if 'status' in fields:
            raise BoardError(
                f'{path.name} has a status line; status lives on the build board, not in git.'
            )
        if fields['id'] in stories:
            raise BoardError(f'{fields["id"]} appears twice ({path.name}).')
        stories[fields['id']] = Story(fields['id'], fields.get('title', ''), path, fields)
    return stories


def read_export(export_dir: Path) -> dict[str, dict[str, object]]:
    if not export_dir.is_dir():
        raise BoardError(f'No board export at {export_dir}. {EXPORT_HELP}')
    rows: dict[str, dict[str, object]] = {}
    for path in sorted(export_dir.glob('*.json')):
        row = json.loads(path.read_text())
        status = row.get('status', 'Backlog')
        if status not in STATUSES:
            raise BoardError(
                f'The board says {path.stem} is "{status}"; use one of: {", ".join(STATUSES)}.'
            )
        rows[path.stem] = row
    return rows


def load(stories_dir: Path = STORIES, export_dir: Path = EXPORT) -> dict[str, Story]:
    stories = load_definitions(stories_dir)
    for sid, row in read_export(export_dir).items():
        if sid in stories:
            stories[sid].board = row
    return stories


def export_age(export_dir: Path = EXPORT) -> str:
    times = [p.stat().st_mtime for p in export_dir.glob('*.json')]
    if not times:
        return 'the board export is empty'
    minutes = int((time.time() - max(times)) // 60)
    return f'board export from {minutes} min ago' if minutes else 'board export from just now'


def expand_braces(pattern: str) -> list[str]:
    match = re.search(r'\{([^{}]*)\}', pattern)
    if not match:
        return [pattern]
    head, tail = pattern[: match.start()], pattern[match.end() :]
    return [p for option in match.group(1).split(',') for p in expand_braces(head + option + tail)]


def literal_prefixes(touch: str) -> list[str]:
    """The literal path before the first glob character, for each brace expansion."""
    if touch in WILDCARD_WORDS:
        return ['']
    return [GLOB_CHARS.split(option, maxsplit=1)[0] for option in expand_braces(touch)]


def overlaps(a: list[str], b: list[str]) -> list[tuple[str, str]]:
    """Pairs of touches that may name the same file. Conservative: shared prefixes overlap."""
    hits: list[tuple[str, str]] = []
    for ta in a:
        for tb in b:
            for pa in literal_prefixes(ta):
                if any(pa.startswith(pb) or pb.startswith(pa) for pb in literal_prefixes(tb)):
                    hits.append((ta, tb))
                    break
    return hits


def blockers(story: Story, stories: dict[str, Story]) -> list[str]:
    """Every reason this story cannot start now, in plain words. Empty means it can."""
    reasons: list[str] = []
    if not story.on_board:
        reasons.append(f'{story.id} is not on the build board yet; run /board sync.')
    elif story.status != 'Backlog':
        reasons.append(f'{story.id} is already {story.status}.')
    for dep in story.depends:
        other = stories.get(dep)
        if other is None:
            reasons.append(f'{story.id} depends on {dep}, which is not on the board.')
        elif other.status != 'Done':
            reasons.append(f'{story.id} depends on {dep}, which is {other.status}.')
    in_flight = [s for s in stories.values() if s.status in IN_FLIGHT and s.id != story.id]
    for other in in_flight:
        for mine, theirs in overlaps(story.touches, other.touches):
            reasons.append(f'{story.id} touches {mine}, which overlaps {other.id} ({theirs}).')
    implementers = [s for s in in_flight if is_story(s)]
    if is_story(story) and len(implementers) >= WIP_CAP:
        reasons.append(f'{len(implementers)} stories are already in flight (cap {WIP_CAP}).')
    return reasons


def is_story(story: Story) -> bool:
    """Stories count toward the cap. Spikes (SPn) are run by the owner and do not."""
    return story.id.startswith('S-')


def row(story: Story) -> str:
    f = story.fields
    status = story.status if story.on_board else '(not on board)'
    return (
        f'{story.id:<8} {status:<14} w{f.get("wave", "?"):<4} '
        f'{f.get("runs_on", ""):<12} {f.get("size", ""):<3} {story.title}'
    )


def runs_on_matches(story: Story, place: str | None) -> bool:
    return not place or place in story.fields.get('runs_on', '')


def cmd_list(args: argparse.Namespace, stories: dict[str, Story]) -> int:
    for story in stories.values():
        if args.status and story.status != args.status:
            continue
        if args.wave and story.fields.get('wave', '') != args.wave:
            continue
        if not runs_on_matches(story, args.runs_on):
            continue
        print(row(story))
    return 0


def ready(stories: dict[str, Story], place: str | None = None) -> list[Story]:
    return [
        s
        for s in stories.values()
        if s.status == 'Backlog' and runs_on_matches(s, place) and not blockers(s, stories)
    ]


def cmd_ready(args: argparse.Namespace, stories: dict[str, Story]) -> int:
    for story in ready(stories, getattr(args, 'runs_on', None)):
        print(row(story))
    return 0


def cmd_lanes(_: argparse.Namespace, stories: dict[str, Story]) -> int:
    in_flight = [s for s in stories.values() if s.status in IN_FLIGHT]
    implementers = sum(1 for s in in_flight if is_story(s))
    print(f'In flight ({implementers}/{WIP_CAP} stories, plus spikes):')
    for story in in_flight:
        owner = (story.board or {}).get('owner') or '?'
        print(f'  {row(story)}\n      owner: {owner}   touches: {" ".join(story.touches) or "-"}')
    blocked = [s for s in stories.values() if s.status == 'Blocked']
    if blocked:
        print('\nBlocked:')
        for story in blocked:
            print(f'  {row(story)}\n      why: {(story.board or {}).get("blocked_reason") or "?"}')
    print('\nReady to start:')
    cmd_ready(argparse.Namespace(runs_on=None), stories)
    return 0


def cmd_start(args: argparse.Namespace, stories: dict[str, Story]) -> int:
    story = stories.get(args.id)
    if story is None:
        print(f'No story {args.id} on the board.', file=sys.stderr)
        return 1
    reasons = blockers(story, stories)
    if reasons:
        print(f'Cannot start {story.id}:', file=sys.stderr)
        for reason in reasons:
            print(f'  - {reason}', file=sys.stderr)
        return 1
    claim = {'status': 'In Progress', 'branch': f'story/{story.id}'}
    print(f'{story.id} can start: {story.title}')
    print(f'Claim it on the board (ArtifactData update stories/{story.id}): {json.dumps(claim)}')
    return 0


def feature_titles(features_dir: Path = FEATURES) -> dict[str, str]:
    titles: dict[str, str] = {}
    for path in features_dir.glob('*.md'):
        fields = read_frontmatter(path.read_text())
        if 'id' in fields:
            titles[fields['id']] = fields.get('title', '')
    return titles


def definition_row(story: Story, titles: dict[str, str]) -> dict[str, object]:
    f = story.fields
    return {
        'id': story.id,
        'title': story.title,
        'feature': f.get('feature', ''),
        'feature_title': titles.get(f.get('feature', ''), ''),
        'wave': f.get('wave', ''),
        'runs_on': f.get('runs_on', ''),
        'size': f.get('size', ''),
        'depends': story.depends,
        'touches': f.get('touches', ''),
        'behaviours': f.get('behaviours', ''),
        'source': f'project/stories/{story.path.name}',
    }


def sync_writes(stories: dict[str, Story], titles: dict[str, str]) -> list[dict[str, object]]:
    """ArtifactData batch entries: new rows are created as Backlog; changed definitions are
    updated. Status and the other board-only fields are never sent for an existing row."""
    writes: list[dict[str, object]] = []
    for story in stories.values():
        definition = definition_row(story, titles)
        if story.board is None:
            data = definition | {
                'status': 'Backlog',
                'owner': '',
                'branch': '',
                'pr': '',
                'blocked_reason': '',
            }
            writes.append({'op': 'set', 'collection': 'stories', 'doc_id': story.id, 'data': data})
            continue
        changed = {
            k: definition[k] for k in DEFINITION_FIELDS if story.board.get(k) != definition[k]
        }
        if changed:
            writes.append(
                {
                    'op': 'update',
                    'collection': 'stories',
                    'doc_id': story.id,
                    'data': changed,
                    'needs_if_version': True,
                }
            )
    return writes


def cmd_rows(args: argparse.Namespace, stories: dict[str, Story]) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    titles = feature_titles()
    for story in stories.values():
        (out / f'{story.id}.json').write_text(json.dumps(definition_row(story, titles), indent=1))
    print(f'{len(stories)} rows -> {out}')
    return 0


def cmd_sync(args: argparse.Namespace, stories: dict[str, Story], export_dir: Path) -> int:
    writes = sync_writes(stories, feature_titles())
    Path(args.out).write_text(json.dumps(writes, indent=1))
    gone = sorted(set(read_export(export_dir)) - set(stories))
    print(f'{len(writes)} writes -> {args.out}')
    if gone:
        print(f'On the board but not in project/stories (ask before deleting): {", ".join(gone)}')
    return 0


def main(
    argv: list[str] | None = None, stories_dir: Path = STORIES, export_dir: Path = EXPORT
) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or '').partition('\n')[0])
    sub = parser.add_subparsers(dest='cmd', required=True)
    p_list = sub.add_parser('list')
    p_list.add_argument('--status')
    p_list.add_argument('--wave')
    p_list.add_argument('--runs-on')
    p_ready = sub.add_parser('ready')
    p_ready.add_argument('--runs-on')
    sub.add_parser('lanes')
    p_start = sub.add_parser('start')
    p_start.add_argument('id')
    p_rows = sub.add_parser('rows')
    p_rows.add_argument('out')
    p_sync = sub.add_parser('sync')
    p_sync.add_argument('out')
    args = parser.parse_args(argv)
    if args.cmd == 'rows':
        return cmd_rows(args, load_definitions(stories_dir))
    stories = load(stories_dir, export_dir)
    if args.cmd == 'sync':
        return cmd_sync(args, stories, export_dir)
    if args.cmd == 'lanes':
        print(f'({export_age(export_dir)})')
    handlers = {'list': cmd_list, 'ready': cmd_ready, 'lanes': cmd_lanes, 'start': cmd_start}
    return handlers[args.cmd](args, stories)


if __name__ == '__main__':
    sys.exit(main())
