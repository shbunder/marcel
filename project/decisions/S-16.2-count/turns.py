"""Read-only (S-16.2): every brain turn in the brain transcripts, with its trigger and its usage.

A turn starts at a user entry that is a prompt (the seed, a channel message, a command), not a tool result. Its usage
is the sum over its assistant messages, each message id counted once (a message is split over several entries that
repeat its usage)."""
import glob, json, os, re, sys
out = []
for d in sys.argv[1:]:
    for path in sorted(glob.glob(os.path.join(d, '*.jsonl'))):
        turn, seen = None, set()
        for line in open(path):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            t = e.get('type')
            if t == 'user':
                content = e.get('message', {}).get('content')
                if isinstance(content, list):
                    if any(isinstance(c, dict) and c.get('type') == 'tool_result' for c in content):
                        continue
                    text = ' '.join(c.get('text', '') for c in content if isinstance(c, dict))
                else:
                    text = content or ''
                if not text.strip() or text.startswith(('<local-command', '<command-name>')):
                    continue
                if turn:
                    out.append(turn)
                m = re.match(r'<channel [^>]*kind="([^"]+)"(?:[^>]*event="([^"]+)")?', text)
                kind = (m.group(2) or m.group(1)) if m else ('seed' if text.startswith('You are ') else
                       'compact' if text.startswith(('/compact', 'This session is being continued')) else 'other')
                turn = {'session': os.path.basename(path)[:8], 'agent': os.path.basename(d).split('-')[-1],
                        'at': e.get('timestamp'), 'kind': kind, 'text': text[:900],
                        'input': 0, 'cache_read': 0, 'cache_write': 0, 'output': 0, 'calls': 0, 'tools': []}
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
        if turn:
            out.append(turn)
out.sort(key=lambda x: x['at'] or '')
json.dump(out, sys.stdout, indent=1)
