#!/usr/bin/env python3
"""SP6 hub stand-in: talks to the runner over the mounted unix socket. Stdlib only.

    python3 hub.py health | sessions | spawn <cwd> <name> <prompt> | stop <id> | tail <session-uuid> [offset] [follow_s]
"""
import http.client
import json
import os
import socket
import sys

SOCK = os.environ.get('RUNNER_SOCK', '/run/marcel/runner.sock')


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 120):
        super().__init__('runner', timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def call(method: str, path: str, body: dict | None = None):
    c = UnixHTTPConnection(SOCK)
    c.request(method, path, body=json.dumps(body) if body is not None else None,
              headers={'content-type': 'application/json'})
    r = c.getresponse()
    return r.status, r.read().decode()


def main() -> None:
    cmd, *a = sys.argv[1:]
    print(f'hub uid={os.getuid()} gid={os.getgid()} groups={os.getgroups()} sock={SOCK}', file=sys.stderr)
    if cmd == 'health':
        print(*call('GET', '/health'))
    elif cmd == 'sessions':
        print(*call('GET', '/sessions'))
    elif cmd == 'spawn':
        print(*call('POST', '/spawn', {'cwd': a[0], 'name': a[1], 'prompt': a[2]}))
    elif cmd == 'stop':
        print(*call('POST', '/stop', {'id': a[0]}))
    elif cmd == 'tail':
        off = a[1] if len(a) > 1 else '0'
        fol = a[2] if len(a) > 2 else '0'
        print(*call('GET', f'/tail?session={a[0]}&offset={off}&follow={fol}'))


if __name__ == '__main__':
    main()
