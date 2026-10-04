#!/usr/bin/env python3
"""Send one channel message to a spike channel server and wait for its reply, approving only `reply` prompts.

    python3 ask.py <port> <events.jsonl> "<text>" [timeout_s]
Prints the reply text (or TIMEOUT). Any non-reply permission request is printed and left unanswered.
"""
import json, sys, time, urllib.request

port, log, text = int(sys.argv[1]), sys.argv[2], sys.argv[3]
timeout = float(sys.argv[4]) if len(sys.argv) > 4 else 90


def post(path, body):
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', json.dumps(body).encode(), {'content-type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def events():
    try:
        return [json.loads(l) for l in open(log)]
    except FileNotFoundError:
        return []


start = len(events())
post('/message', {'text': text})
answered = set()
end = time.time() + timeout
while time.time() < end:
    for ev in events()[start:]:
        if ev['kind'] == 'reply':
            print(ev['text'])
            sys.exit(0)
        if ev['kind'] == 'permission_request' and ev['request_id'] not in answered:
            answered.add(ev['request_id'])
            if ev['tool_name'].endswith('__reply'):
                post('/permission', {'request_id': ev['request_id'], 'behavior': 'allow'})
            else:
                print(f'UNANSWERED permission: {ev["tool_name"]} {ev["input_preview"]}', file=sys.stderr)
    time.sleep(0.5)
print('TIMEOUT')
