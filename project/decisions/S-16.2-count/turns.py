"""Read-only (S-16.2): every brain turn in the brain transcripts, with its trigger and its usage.

    python3 turns.py <brain transcript folder> [<folder> …] > turns.json

A turn starts at a prompt: the seed, a channel message (also one delivered mid-turn, as a `queued_command`), a
command. Not at a tool result. Its usage is the sum over the assistant messages after it, each message id counted
once: Claude Code splits a message over several entries that repeat its usage.

Some calls leave no assistant entry: compaction, its recaps, a call cut off by a stop. Claude Code's own
`cost-state` record (its last one in the file) has the session's usage by token type. What it has beyond the
transcript is added as one `unlogged` turn per session. A session without a `cost-state` record (one still running)
has nothing added.
"""

import glob
import json
import os
import re
import sys

KEYS = ('input', 'cache_write', 'cache_read', 'output')
COST_KEYS = {'input': 'inputTokens', 'cache_write': 'cacheCreationInputTokens',
             'cache_read': 'cacheReadInputTokens', 'output': 'outputTokens'}


def kind_of(text: str) -> str:
    m = re.match(r'<channel [^>]*kind="([^"]+)"(?:[^>]*event="([^"]+)")?', text)
    if m:
        return m.group(2) or m.group(1)
    if text.startswith('You are '):
        return 'seed'
    if text.startswith(('/compact', 'This session is being continued')):
        return 'compact'
    return 'other'


def new_turn(path: str, folder: str, at: str, text: str) -> dict:
    m = re.search(r'message_id="([^"]+)"', text)
    return {'session': os.path.basename(path)[:8], 'agent': os.path.basename(folder).split('-')[-1], 'at': at,
            'kind': kind_of(text), 'message_id': m.group(1) if m else None, 'text': text[:900],
            **{k: 0 for k in KEYS}, 'calls': 0, 'tools': [], 'models': []}


out = []
for folder in sys.argv[1:]:
    for path in sorted(glob.glob(os.path.join(folder, '*.jsonl'))):
        turns, turn, seen, cost = [], None, set(), None
        for line in open(path):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            t = e.get('type')
            attachment = e.get('attachment') if isinstance(e.get('attachment'), dict) else {}
            if t == 'cost-state':
                cost = e
            elif attachment.get('type') == 'queued_command' and '<channel ' in str(attachment.get('prompt', '')):
                turn = new_turn(path, folder, e.get('timestamp'), attachment['prompt'])
                turns.append(turn)
            elif t == 'user':
                content = e.get('message', {}).get('content')
                if isinstance(content, list):
                    if any(isinstance(c, dict) and c.get('type') == 'tool_result' for c in content):
                        continue
                    text = ' '.join(c.get('text', '') for c in content if isinstance(c, dict))
                else:
                    text = content or ''
                if not text.strip() or text.startswith(('<local-command', '<command-name>')):
                    continue
                turn = new_turn(path, folder, e.get('timestamp'), text)
                turns.append(turn)
            elif t == 'assistant' and turn is not None:
                msg = e.get('message', {})
                for c in msg.get('content') or []:
                    if isinstance(c, dict) and c.get('type') == 'tool_use':
                        turn['tools'].append(c.get('name', '').split('__')[-1])
                if msg.get('id') in seen:
                    continue
                seen.add(msg.get('id'))
                u = msg.get('usage') or {}
                turn['input'] += u.get('input_tokens', 0)
                turn['output'] += u.get('output_tokens', 0)
                turn['cache_read'] += u.get('cache_read_input_tokens', 0)
                turn['cache_write'] += u.get('cache_creation_input_tokens', 0)
                turn['calls'] += 1
                if msg.get('model') and msg['model'] not in turn['models']:
                    turn['models'].append(msg['model'])
        if cost:
            unlogged = new_turn(path, folder, turns[-1]['at'] if turns else None, '')
            unlogged['kind'] = 'unlogged'
            for k, ck in COST_KEYS.items():
                total = sum(u.get(ck, 0) for u in cost.get('modelUsage', {}).values())
                unlogged[k] = max(0, total - sum(x[k] for x in turns))
            if any(unlogged[k] for k in KEYS):
                turns.append(unlogged)
        out.extend(turns)
out.sort(key=lambda x: x['at'] or '')
json.dump(out, sys.stdout, indent=1)
