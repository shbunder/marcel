"""Read-only (S-16.2): brain turns by category, with tokens and a price-weighted share."""
import json, sys
t = json.load(open(sys.argv[1]))
# Skill text is injected as a user entry inside the turn that loaded it: fold it into that turn. A user entry
# that is neither a channel message, a seed nor a compaction is a probe someone typed (not the family).
merged = []
for x in t:
    if x['kind'] == 'other' and merged and x['text'].startswith(('Base directory for this skill', ' Marcel starts')):
        for k in ('input', 'cache_read', 'cache_write', 'output', 'calls'):
            merged[-1][k] += x[k]
        merged[-1]['tools'] += x['tools']
        continue
    if x['kind'] == 'other':
        x['kind'] = 'probe'
    merged.append(x)
t = merged
STATUS = ('is anything of mine still waiting on me', 'what is still open or waiting on me')
def category(x):
    k, text = x['kind'], x['text']
    if k in ('seed', 'compact', 'probe'):
        return 'E. start-up, compaction, probe'
    if k == 'user_message' and any(s in text for s in STATUS):
        return 'A. status question'
    if k in ('task_question', 'task_failed'):
        return 'C. worker event the brain only relayed'
    if k == 'digest_due':
        return 'D. needs the brain: digest'
    if k == 'user_message' and 'tasks_spawn' in x['tools']:
        return 'D. needs the brain: start a quest'
    return 'D. needs the brain: chat'
W = {'input': 1, 'cache_write': 1.25, 'cache_read': 0.1, 'output': 5}
def cost(x): return sum(x[k] * w for k, w in W.items())
rows = {}
for x in t:
    c = category(x)
    r = rows.setdefault(c, {'turns': 0, 'input': 0, 'cache_write': 0, 'cache_read': 0, 'output': 0, 'cost': 0.0})
    r['turns'] += 1
    for k in W: r[k] += x[k]
    r['cost'] += cost(x)
total = sum(r['cost'] for r in rows.values())
work = sum(r['cost'] for c, r in rows.items() if not c.startswith('E.'))
print(f"{'category':45} turns  cache_read  cache_write  output   weighted  share  share_of_event_turns")
for c in sorted(rows):
    r = rows[c]
    print(f"{c:45} {r['turns']:5} {r['cache_read']:11} {r['cache_write']:12} {r['output']:7} {r['cost']:10.0f} {r['cost']/total:6.1%}"
          + ('' if c.startswith('E.') else f" {r['cost']/work:6.1%}"))
print(f"{'total':45} {sum(r['turns'] for r in rows.values()):5} {'':11} {'':12} {'':7} {total:10.0f}")
