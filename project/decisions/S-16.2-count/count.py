"""Read-only (S-16.2): brain turns by category, with tokens and a price-weighted share.

    python3 count.py turns.json labels.json

`labels.json` maps an owner message id to `status` or `follow_up`, labelled by hand by reading every owner message:
the hub's own `status_question` rule (hub/src/marcel_hub/triage.py) missed both status questions on 2026-10-13, so
the triage log cannot be the source. An owner message with no label counts as needing the brain.

The weights are Opus prices relative to input (input 1, cache write 1.25, cache read 0.1, output 5), a stand-in for
plan usage, which is not reported per session.
"""

import json
import sys

turns = json.load(open(sys.argv[1]))
labels = json.load(open(sys.argv[2]))
W = {'input': 1, 'cache_write': 1.25, 'cache_read': 0.1, 'output': 5}

# Skill text is injected as a user entry inside the turn that loaded it: fold it into that turn. Any other prompt
# that is neither a channel message, a seed nor a compaction is a probe someone typed: left out of every total.
merged = []
for x in turns:
    if x['kind'] == 'other' and merged and x['text'].startswith(('Base directory for this skill', ' Marcel starts')):
        for k in (*W, 'calls'):
            merged[-1][k] += x[k]
        merged[-1]['tools'] += x['tools']
        continue
    merged.append(x)


def category(x: dict) -> str | None:
    k = x['kind']
    if k == 'other':
        return None  # a probe
    if k in ('seed', 'compact'):
        return 'E. start-up seeds and compaction prompts'
    if k == 'unlogged':
        return 'F. calls with no transcript entry (compaction, recaps)'
    if k == 'user_message':
        label = labels.get(x['message_id'] or '')
        if label == 'status':
            return 'A. status question'
        if label == 'follow_up':
            return 'B. follow-up to an existing thread'
        return 'D. needs the brain: start a quest' if 'tasks_spawn' in x['tools'] else 'D. needs the brain: chat'
    if k in ('task_question', 'task_failed'):
        return 'C. worker event the brain only relayed'
    if k == 'digest_due':
        return 'D. needs the brain: digest'
    return 'D. needs the brain: other event'


rows: dict[str, dict] = {}
left_out = 0
for x in merged:
    c = category(x)
    if c is None:
        left_out += 1
        continue
    r = rows.setdefault(c, {'turns': 0, **{k: 0 for k in W}, 'weighted': 0.0})
    r['turns'] += x['kind'] != 'unlogged'
    for k in W:
        r[k] += x[k]
    r['weighted'] += sum(x[k] * w for k, w in W.items())
total = sum(r['weighted'] for r in rows.values())
events = sum(r['weighted'] for c, r in rows.items() if c[0] in 'ABCD')
print(f"{'category':56} turns  cache_read  output   weighted   share  of_events")
for c in sorted(rows):
    r = rows[c]
    of_events = f" {r['weighted'] / events:8.1%}" if c[0] in 'ABCD' else ''
    print(f"{c:56} {r['turns']:5} {r['cache_read']:11} {r['output']:7} {r['weighted']:10.0f} {r['weighted'] / total:7.1%}"
          + of_events)
typed = sum(r['weighted'] for c, r in rows.items() if c[0] in 'ABC')
print(f"A+B+C, a typed decision could have done these: {typed / total:.1%} of brain usage, {typed / events:.1%} of event turns")
print(f'probes left out: {left_out}')
