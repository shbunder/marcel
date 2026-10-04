#!/usr/bin/env python3
"""SP3 report receiver: stands in for the hub's report endpoint behind marcel-bot.com.

Accepts only `POST /sp3/report` with `Authorization: Bearer $SP3_TOKEN` (scoped, one-off), body ≤ 64 KB.
Everything else → 404 (wrong token → 401). Each request is logged to $SP3_LOG as one JSON line.

    SP3_TOKEN=… SP3_LOG=reports.jsonl python3 receiver.py   # binds 127.0.0.1:7420 (cloudflared's origin)
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ['SP3_TOKEN']
LOG = os.environ.get('SP3_LOG', 'reports.jsonl')
PORT = int(os.environ.get('SP3_PORT', '7420'))


def log(**kw) -> None:
    with open(LOG, 'a') as f:
        f.write(json.dumps({'t': int(time.time() * 1000), **kw}) + '\n')


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        log(method='GET', path=self.path, ua=self.headers.get('user-agent'), cf_ip=self.headers.get('cf-connecting-ip'))
        self._reply(404, {'error': 'not found'})

    def do_POST(self):
        n = int(self.headers.get('content-length', 0))
        if self.path != '/sp3/report' or n > 65536:
            log(method='POST', path=self.path, status=404)
            return self._reply(404, {'error': 'not found'})
        raw = self.rfile.read(n)
        ok = self.headers.get('authorization') == f'Bearer {TOKEN}'
        try:
            body = json.loads(raw)
        except ValueError:
            body = raw.decode(errors='replace')
        log(method='POST', path=self.path, status=200 if ok else 401, authorized=ok, body=body,
            ua=self.headers.get('user-agent'), cf_ip=self.headers.get('cf-connecting-ip'))
        self._reply(200 if ok else 401, {'ok': ok})


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
