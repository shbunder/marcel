#!/usr/bin/env python3
"""SP1 — a minimal two-way Claude Code channel with permission relay. Stdlib only.

MCP side (stdio, newline-delimited JSON-RPC):
  * declares `claude/channel` + `claude/channel/permission`
  * tool `reply(text)` — the session's way to talk back
  * receives `notifications/claude/channel/permission_request`

HTTP side (127.0.0.1:$SP1_PORT) — stands in for the hub:
  POST /message     {"text": "..."}                       → push a channel message into the session
  POST /permission  {"request_id": "...", "behavior": "allow"|"deny"}  → answer a relayed prompt
  GET  /events                                            → everything seen so far (replies, permission requests, log)

Every event is also appended to $SP1_LOG (JSONL) so a test script can watch it.
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAME = os.environ.get('SP1_NAME', 'sp1')
# Identity: Claude Code gives every MCP server its session's id; custom env (MARCEL_*) does not survive `claude --bg`
# (SP7). The hub maps session id → conversation, so the server just reports the session id.
SESSION = os.environ.get('CLAUDE_CODE_SESSION_ID', '?')
CONV = os.environ.get('MARCEL_CONVERSATION_ID', 'session:' + SESSION[:8])
# Preferred fixed port; a second session (e.g. an SP7 fork) falls back to an ephemeral one and reports it in `started`.
# (The real channel server dials out to the hub and listens on nothing.)
PORT = int(os.environ.get('SP1_PORT', '8791'))
LOG = os.environ.get('SP1_LOG', os.path.join(os.path.dirname(os.path.abspath(__file__)), f'{NAME}-events.jsonl'))

out_lock = threading.Lock()
events: list[dict] = []
seq = 0


def record(kind: str, **data) -> dict:
    ev = {'t': int(time.time() * 1000), 'kind': kind, 'server': NAME, 'conversation': CONV, 'session': SESSION,
          'pid': os.getpid(), **data}
    events.append(ev)
    with open(LOG, 'a') as f:
        f.write(json.dumps(ev) + '\n')
    return ev


def send(msg: dict) -> None:
    with out_lock:
        sys.stdout.write(json.dumps(msg) + '\n')
        sys.stdout.flush()


def notify(method: str, params: dict) -> None:
    send({'jsonrpc': '2.0', 'method': method, 'params': params})


INSTRUCTIONS = (
    f'Messages from the Marcel app arrive as <channel source="{NAME}" ...>. The sender reads the app, not this '
    'session: anything they should see must go through the reply tool. Reply with the reply tool.'
)
TOOLS = [{
    'name': 'reply',
    'description': 'Send a message to the Marcel app user.',
    'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']},
}]


def handle(msg: dict) -> None:
    method, mid = msg.get('method'), msg.get('id')
    if method == 'initialize':
        record('initialize', client=msg['params'].get('clientInfo'), protocol=msg['params'].get('protocolVersion'))
        send({'jsonrpc': '2.0', 'id': mid, 'result': {
            'protocolVersion': msg['params'].get('protocolVersion', '2025-06-18'),
            'capabilities': {'tools': {}, 'experimental': {'claude/channel': {}, 'claude/channel/permission': {}}},
            'serverInfo': {'name': NAME, 'version': '0.0.1'},
            'instructions': INSTRUCTIONS,
        }})
    elif method == 'tools/list':
        send({'jsonrpc': '2.0', 'id': mid, 'result': {'tools': TOOLS}})
    elif method == 'tools/call':
        name, args = msg['params']['name'], msg['params'].get('arguments') or {}
        if name == 'reply':
            record('reply', text=args.get('text', ''))
            send({'jsonrpc': '2.0', 'id': mid, 'result': {'content': [{'type': 'text', 'text': 'sent'}]}})
        else:
            send({'jsonrpc': '2.0', 'id': mid, 'result': {'content': [{'type': 'text', 'text': f'unknown {name}'}], 'isError': True}})
    elif method == 'ping':
        send({'jsonrpc': '2.0', 'id': mid, 'result': {}})
    elif method == 'notifications/claude/channel/permission_request':
        record('permission_request', **msg['params'])
    elif mid is not None:
        send({'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': f'no method {method}'}})
    else:
        record('notification', method=method, params=msg.get('params'))


class Http(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/events':
            return self._json(200, events)
        self._json(404, {'error': 'not found'})

    def do_POST(self):
        global seq
        body = json.loads(self.rfile.read(int(self.headers.get('content-length', 0))) or b'{}')
        if self.path == '/message':
            seq += 1
            mid = f'm{seq}'
            notify('notifications/claude/channel', {
                'content': body['text'],
                'meta': {'chat_id': CONV, 'message_id': mid, 'user': 'owner', 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())},
            })
            record('message_in', message_id=mid, text=body['text'])
            return self._json(200, {'message_id': mid})
        if self.path == '/permission':
            notify('notifications/claude/channel/permission', {'request_id': body['request_id'], 'behavior': body['behavior']})
            record('permission_answer', request_id=body['request_id'], behavior=body['behavior'])
            return self._json(200, {'ok': True})
        self._json(404, {'error': 'not found'})


def main() -> None:
    try:
        srv = ThreadingHTTPServer(('127.0.0.1', PORT), Http)
    except OSError as e:
        record('http_port_taken', port=PORT, error=str(e))
        srv = ThreadingHTTPServer(('127.0.0.1', 0), Http)
    if srv:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        # SP7: which identity does the server get from its session? (keys only, plus the session id)
        env = {k: (os.environ[k] if k in ('CLAUDE_CODE_SESSION_ID', 'MARCEL_CONVERSATION_ID') else '…')
               for k in os.environ if k.startswith(('CLAUDE', 'MARCEL'))}
        record('started', port=srv.server_address[1], ppid=os.getppid(), env=env)
    for line in sys.stdin:
        line = line.strip()
        if line:
            try:
                handle(json.loads(line))
            except Exception as e:  # keep the channel alive; the log shows what broke
                record('error', error=repr(e), line=line[:500])
    record('stdin_closed')


if __name__ == '__main__':
    main()
