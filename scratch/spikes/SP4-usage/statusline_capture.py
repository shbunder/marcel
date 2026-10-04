#!/usr/bin/env python3
"""Status-line command for SP4: append the JSON Claude Code pipes in to $SL_OUT, print a short line.

If the input has `rate_limits`, this is all a production status line needs to do to publish usage to a file.
"""
import json, os, sys, time
raw = sys.stdin.read()
with open(os.environ.get('SL_OUT', '/tmp/statusline-input.jsonl'), 'a') as f:
    f.write(json.dumps({'t': int(time.time() * 1000), 'input': json.loads(raw) if raw.strip() else None}) + '\n')
try:
    rl = json.loads(raw).get('rate_limits') or {}
    print(' '.join(f'{k}:{v.get("used_percentage", v)}' for k, v in rl.items() if isinstance(v, dict)) or 'no rate_limits')
except Exception as e:
    print(f'sl error {e}')
