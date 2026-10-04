#!/usr/bin/env python3
"""Marcel's board CLI: list stories, show lanes, and start or finish a story safely.

Each story lives in its own file, `project/stories/<id>.md`. The frontmatter holds id, title,
feature, wave, runs_on, size, status, depends, touches and behaviours. The CLI exists so the
mechanical rules are enforced, not remembered:

- a story starts only when everything it depends on is Done;
- it never starts while another In Progress story touches an overlapping path;
- no more than WIP_CAP stories are In Progress at once.

Stdlib only, so it runs before any `uv sync`.

    python3 scripts/board.py list [--wave 1] [--status Backlog] [--runs-on cloud]
    python3 scripts/board.py lanes
    python3 scripts/board.py ready
    python3 scripts/board.py start S-03.2
    python3 scripts/board.py set S-03.2 status Done
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORIES = ROOT / 'project' / 'stories'

STATUSES = ('Backlog', 'In Progress', 'Done')
# Six, per the playbook: past that, conflicts on shared files cost more than parallelism saves.
WIP_CAP = 6
GLOB_CHARS = re.compile(r'[*?\[]')
# Touches that are not paths ("everything (one-off)") overlap with every other story.
WILDCARD_WORDS = {'everything', '(one-off)'}


@dataclass
class Story:
    id: str
    title: str
    path: Path
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return self.fields.get('status', 'Backlog')

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


def set_frontmatter(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf'^{re.escape(key)}:.*$', re.MULTILINE)
    header, sep, body = text[4:].partition('\n---\n')
    if pattern.search(header):
        header = pattern.sub(f'{key}: {value}', header, count=1)
    else:
        header = f'{header}\n{key}: {value}'
    return f'---\n{header}{sep}{body}'


def load(stories_dir: Path = STORIES) -> dict[str, Story]:
    stories: dict[str, Story] = {}
    for path in sorted(stories_dir.glob('*.md')):
        fields = read_frontmatter(path.read_text())
        if 'id' in fields:
            stories[fields['id']] = Story(fields['id'], fields.get('title', ''), path, fields)
    return stories


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
    if story.status != 'Backlog':
        reasons.append(f'{story.id} is already {story.status}.')
    for dep in story.depends:
        other = stories.get(dep)
        if other is None:
            reasons.append(f'{story.id} depends on {dep}, which is not on the board.')
        elif other.status != 'Done':
            reasons.append(f'{story.id} depends on {dep}, which is {other.status}.')
    in_progress = [s for s in stories.values() if s.status == 'In Progress' and s.id != story.id]
    for other in in_progress:
        for mine, theirs in overlaps(story.touches, other.touches):
            reasons.append(f'{story.id} touches {mine}, which overlaps {other.id} ({theirs}).')
    implementers = [s for s in in_progress if is_story(s)]
    if is_story(story) and len(implementers) >= WIP_CAP:
        reasons.append(f'{len(implementers)} stories are already In Progress (cap {WIP_CAP}).')
    return reasons


def is_story(story: Story) -> bool:
    """Stories count toward the cap. Spikes (SPn) are run by the owner and do not."""
    return story.id.startswith('S-')


def set_field(story: Story, key: str, value: str) -> None:
    if key == 'status' and value not in STATUSES:
        raise SystemExit(f'Status must be one of: {", ".join(STATUSES)}.')
    story.path.write_text(set_frontmatter(story.path.read_text(), key, value))
    story.fields[key] = value


def row(story: Story) -> str:
    f = story.fields
    return (
        f'{story.id:<8} {f.get("status", ""):<12} w{f.get("wave", "?"):<4} '
        f'{f.get("runs_on", ""):<12} {f.get("size", ""):<3} {story.title}'
    )


def cmd_list(args: argparse.Namespace, stories: dict[str, Story]) -> int:
    for story in stories.values():
        f = story.fields
        if args.status and story.status != args.status:
            continue
        if args.wave and f.get('wave', '') != args.wave:
            continue
        if args.runs_on and args.runs_on not in f.get('runs_on', ''):
            continue
        print(row(story))
    return 0


def cmd_ready(_: argparse.Namespace, stories: dict[str, Story]) -> int:
    for story in stories.values():
        if story.status == 'Backlog' and not blockers(story, stories):
            print(row(story))
    return 0


def cmd_lanes(_: argparse.Namespace, stories: dict[str, Story]) -> int:
    in_progress = [s for s in stories.values() if s.status == 'In Progress']
    implementers = sum(1 for s in in_progress if is_story(s))
    print(f'In progress ({implementers}/{WIP_CAP} stories, plus spikes):')
    for story in in_progress:
        print(f'  {row(story)}\n      touches: {" ".join(story.touches) or "-"}')
    print('\nReady to start:')
    cmd_ready(argparse.Namespace(), stories)
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
    set_field(story, 'status', 'In Progress')
    print(f'Started {story.id}: {story.title}')
    return 0


def cmd_set(args: argparse.Namespace, stories: dict[str, Story]) -> int:
    story = stories.get(args.id)
    if story is None:
        print(f'No story {args.id} on the board.', file=sys.stderr)
        return 1
    set_field(story, args.key, args.value)
    print(f'{story.id}: {args.key} = {args.value}')
    return 0


def main(argv: list[str] | None = None, stories_dir: Path = STORIES) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or '').partition('\n')[0])
    sub = parser.add_subparsers(dest='cmd', required=True)
    p_list = sub.add_parser('list')
    p_list.add_argument('--status')
    p_list.add_argument('--wave')
    p_list.add_argument('--runs-on')
    sub.add_parser('ready')
    sub.add_parser('lanes')
    p_start = sub.add_parser('start')
    p_start.add_argument('id')
    p_set = sub.add_parser('set')
    p_set.add_argument('id')
    p_set.add_argument('key')
    p_set.add_argument('value')
    args = parser.parse_args(argv)
    handlers = {
        'list': cmd_list,
        'ready': cmd_ready,
        'lanes': cmd_lanes,
        'start': cmd_start,
        'set': cmd_set,
    }
    return handlers[args.cmd](args, load(stories_dir))


if __name__ == '__main__':
    sys.exit(main())
