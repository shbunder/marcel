#!/usr/bin/env python3
"""SP6 runner stand-in: JSON over HTTP on a unix socket. Stdlib only.

    GET  /health                       → {"ok": true, "pid": …, "uid": …}
    GET  /sessions                     → background rows of `claude agents --json --all`
    POST /spawn  {"cwd","name","prompt","model"}  → runs `claude --bg …` natively, returns its short id
    POST /stop   {"id"}                → `claude stop <id>`
    GET  /tail?session=<uuid>&offset=N → the session's transcript JSONL from byte N, streamed as SSE
                                         (so the hub never needs ~/.claude mounted)

The socket path comes from $RUNNER_SOCK; it is chmod'ed to $RUNNER_SOCK_MODE (default 0660).
"""

import glob
import json
import os
import socketserver
import subprocess
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

SOCK = os.environ.get('RUNNER_SOCK', os.path.join(os.environ.get('XDG_RUNTIME_DIR', '/tmp'), 'marcel-sp6', 'runner.sock'))
MODE = int(os.environ.get('RUNNER_SOCK_MODE', '0660'), 8)
CLEAN_ENV = {k: os.environ[k] for k in ('HOME', 'USER', 'LOGNAME', 'PATH', 'LANG', 'XDG_RUNTIME_DIR') if k in os.environ}


def claude(*args: str, cwd: str | None = None, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(['claude', *args], cwd=cwd, env=CLEAN_ENV, capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL)


class Handler(BaseHTTPRequestHandler):
    def address_string(self) -> str:  # unix sockets have no client address
        return 'unix'

    def log_message(self, fmt, *args):
        print(f'{time.strftime("%H:%M:%S")} {self.command} {self.path} — {fmt % args}', flush=True)

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get('content-length', 0))
        return json.loads(self.rfile.read(n) or b'{}')

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == '/health':
            return self._json(200, {'ok': True, 'pid': os.getpid(), 'uid': os.getuid(), 'sock': SOCK})
        if url.path == '/sessions':
            rows = json.loads(claude('agents', '--json', '--all').stdout or '[]')
            return self._json(200, [r for r in rows if r.get('kind') == 'background'])
        if url.path == '/tail':
            q = parse_qs(url.query)
            return self._tail(q['session'][0], int(q.get('offset', ['0'])[0]), float(q.get('follow', ['0'])[0]))
        self._json(404, {'error': 'not found'})

    def do_POST(self):
        if self.path == '/spawn':
            b = self._body()
            p = claude('--bg', '--name', b['name'], '--model', b.get('model', 'sonnet'),
                       '--permission-mode', b.get('permission_mode', 'auto'), '--', b['prompt'], cwd=b['cwd'])
            out = p.stdout + p.stderr
            short = next((ln.split('·')[1].strip() for ln in out.splitlines() if ln.startswith('backgrounded')), None)
            return self._json(200 if short else 500, {'id': short, 'output': out.strip()})
        if self.path == '/stop':
            p = claude('stop', self._body()['id'])
            return self._json(200, {'output': (p.stdout + p.stderr).strip()})
        self._json(404, {'error': 'not found'})

    def _tail(self, session: str, offset: int, follow_s: float) -> None:
        paths = glob.glob(os.path.expanduser(f'~/.claude/projects/*/{session}.jsonl'))
        if not paths:
            return self._json(404, {'error': f'no transcript for {session}'})
        self.send_response(200)
        self.send_header('content-type', 'text/event-stream')
        self.end_headers()
        end = time.time() + follow_s
        with open(paths[0], 'rb') as f:
            f.seek(offset)
            while True:
                line = f.readline()
                if line.endswith(b'\n'):
                    offset += len(line)
                    ev = json.loads(line)
                    # id = byte offset after this line, so a client resumes with ?offset=<last id>
                    self.wfile.write(f'id: {offset}\nevent: {ev.get("type")}\ndata: {line.decode().strip()}\n\n'.encode())
                    continue
                if time.time() >= end:
                    break
                time.sleep(0.2)
                f.seek(offset)


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main() -> None:
    os.makedirs(os.path.dirname(SOCK), exist_ok=True)
    if os.path.exists(SOCK):
        os.unlink(SOCK)
    srv = UnixHTTPServer(SOCK, Handler)
    os.chmod(SOCK, MODE)
    print(f'runner pid={os.getpid()} uid={os.getuid()} listening on {SOCK} mode={oct(MODE)}', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
