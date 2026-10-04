#!/usr/bin/env python3
"""Convert the plan's feature and spike pages into one board file per feature, story and spike.

Run once, when F01 moves the plan onto the board. It is kept because its parser documents the
story header format, and so a future plan page can be converted the same way.

    python3 scripts/plan_to_board.py plan/ project/
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

FEATURE_RE = re.compile(r'^# (F\d\d) — (.+?)\s{2,}\((.+)\)\s*$')
STORY_RE = re.compile(r'^### (S-\d\d\.\d+)\s+(.+?)\s{2,}runs-on: (.+?) · size: (.+?)\s*$')
SPIKE_RE = re.compile(r'^### (SP\d) — (.+?)\s+·\s+(\w+)\s+·\s+blocks (.+?)\s*$')
DEP_LINE_RE = re.compile(r'^depends:\s*(.*?)(?:\s{2,}touches:\s*(.*))?$')
REF_RE = re.compile(r'(S-\d\d\.\d+)(?:…(S-\d\d\.\d+))?|(SP\d)')

# The playbook's waves where a feature spans more than one. Every other story takes the
# first wave named in its feature's heading.
WAVE_OVERRIDES = {
    'S-05.1': '1',
    'S-12.1': '1',
    'S-13.1': '1',
    'S-13.2': '1',
    'S-14.1': '1',
    'S-14.2': '3',
    'S-14.3': '3',
}


@dataclass
class Item:
    id: str
    title: str
    feature: str
    wave: str
    runs_on: str
    size: str
    body: list[str] = field(default_factory=list)
    depends: list[str] = field(default_factory=list)
    touches: str = ''
    behaviours: str = ''


def expand_refs(text: str) -> list[str]:
    refs: list[str] = []
    for start, end, spike in REF_RE.findall(text):
        if spike:
            refs.append(spike)
            continue
        if not end:
            refs.append(start)
            continue
        feature, first = start[:4], int(start.split('.')[1])
        last = int(end.split('.')[1])
        refs.extend(f'{feature}.{n}' for n in range(first, last + 1))
    return refs


def parse_features(text: str) -> tuple[list[tuple[str, str, str, str]], list[Item]]:
    features: list[tuple[str, str, str, str]] = []
    stories: list[Item] = []
    feature_id, wave, goal = '', '0', ''
    current: Item | None = None
    for line in text.splitlines():
        if m := FEATURE_RE.match(line):
            feature_id = m.group(1)
            wave = (re.findall(r'\d', m.group(3)) or ['0'])[0]
            goal = ''
            features.append((feature_id, m.group(2), m.group(3), goal))
            current = None
            continue
        if line.startswith('Goal:') and features and current is None:
            fid, title, waves, _ = features[-1]
            features[-1] = (fid, title, waves, line.removeprefix('Goal:').strip())
            continue
        if m := STORY_RE.match(line):
            sid = m.group(1)
            current = Item(
                sid,
                m.group(2),
                feature_id,
                WAVE_OVERRIDES.get(sid, wave),
                m.group(3),
                m.group(4).split()[0],
            )
            stories.append(current)
            continue
        if current is None:
            continue
        if (m := DEP_LINE_RE.match(line)) and not current.body:
            current.depends = expand_refs(m.group(1))
            current.touches = (m.group(2) or '').strip()
            if 'everything above' in m.group(1):
                current.depends = ['*']
        elif line.startswith('behaviours:') and not current.body:
            current.behaviours = line.removeprefix('behaviours:').strip()
        else:
            current.body.append(line)
    return features, stories


def parse_spikes(text: str) -> list[Item]:
    spikes: list[Item] = []
    current: Item | None = None
    for line in text.splitlines():
        if m := SPIKE_RE.match(line):
            sid = m.group(1)
            runs_on = m.group(3)
            current = Item(sid, m.group(2), 'F00', '0', runs_on, 'S')
            current.touches = f'scratch/spikes/{sid}-*'
            current.body.append(f'Blocks: {m.group(4)}')
            spikes.append(current)
        elif current is not None:
            if line.startswith('---') or line.startswith('# '):
                current = None
            else:
                current.body.append(line)
    return spikes


def render(item: Item, status: str) -> str:
    body = '\n'.join(item.body).strip()
    return (
        '---\n'
        f'id: {item.id}\n'
        f'title: {item.title}\n'
        f'feature: {item.feature}\n'
        f'wave: {item.wave}\n'
        f'runs_on: {item.runs_on}\n'
        f'size: {item.size}\n'
        f'status: {status}\n'
        f'depends: {", ".join(item.depends)}\n'
        f'touches: {item.touches}\n'
        f'behaviours: {item.behaviours}\n'
        '---\n\n'
        f'# {item.id} — {item.title}\n\n'
        f'{body}\n'
    )


def main(plan: Path, board: Path, statuses: dict[str, str]) -> int:
    features: list[tuple[str, str, str, str]] = []
    stories: list[Item] = []
    for page in sorted((plan / 'features').glob('*.md')):
        f, s = parse_features(page.read_text())
        features += f
        stories += s
    spikes = parse_spikes((plan / '03-spikes.md').read_text())
    everything = [s.id for s in stories if not s.id.startswith('S-16')]
    for story in stories:
        if story.depends == ['*']:
            story.depends = everything
    (board / 'stories').mkdir(parents=True, exist_ok=True)
    (board / 'features').mkdir(parents=True, exist_ok=True)
    for item in spikes + stories:
        status = statuses.get(item.id, 'Backlog')
        (board / 'stories' / f'{item.id}.md').write_text(render(item, status))
    spike_feature = ('F00', 'Phase 0 spikes', 'lead + owner · wave 0', 'Settle seven unknowns.')
    for fid, title, waves, goal in [spike_feature, *features]:
        members = [i for i in spikes + stories if i.feature == fid]
        lines = '\n'.join(f'- [{i.id}](../stories/{i.id}.md) {i.title}' for i in members)
        (board / 'features' / f'{fid}.md').write_text(
            f'---\nid: {fid}\ntitle: {title}\nwaves: {waves}\n---\n\n'
            f'# {fid} — {title}\n\n{goal}\n\n## Stories\n\n{lines}\n'
        )
    print(f'{len(features) + 1} features, {len(stories)} stories, {len(spikes)} spikes')
    return 0


if __name__ == '__main__':
    started = {sp: 'In Progress' for sp in ('SP1', 'SP2', 'SP3', 'SP4', 'SP5', 'SP6', 'SP7')}
    done = {s: 'Done' for s in ('S-01.1', 'S-01.2', 'S-01.3')}
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2]), {**started, **done}))
