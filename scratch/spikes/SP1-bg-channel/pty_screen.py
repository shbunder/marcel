#!/usr/bin/env python3
"""Run a command on a pty for N seconds, optionally sending keys after a regex appears; print the screen text.

    python3 pty_screen.py <seconds> [--send REGEX KEYS]... -- cmd args...
KEYS uses Python escapes, e.g. '\\r' or '\\x1b[B\\r'.
"""
import os, pty, re, select, sys, time
ANSI = re.compile(rb'\x1b\[[0-9;?<>=]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-B]')
secs = float(sys.argv[1]); rest = sys.argv[2:]; sends = []
while rest and rest[0] == '--send':
    sends.append((re.compile(rest[1]), rest[2].encode().decode('unicode_escape').encode('latin-1'))); rest = rest[3:]
cmd = rest[1:] if rest[0] == '--' else rest
pid, fd = pty.fork()
if pid == 0:
    env = {k: v for k, v in os.environ.items() if not k.startswith(('CLAUDE', 'AI_AGENT', 'MCP_'))}
    env['TERM'] = 'xterm-256color'
    os.execvpe(cmd[0], cmd, env)
buf = b''; end = time.time() + secs; done = [False] * len(sends)
while time.time() < end:
    r, _, _ = select.select([fd], [], [], 0.3)
    if r:
        try: chunk = os.read(fd, 65536)
        except OSError: break
        if not chunk: break
        buf += chunk
    text = re.sub(r'\s+', ' ', ANSI.sub(b'', buf).decode(errors='replace'))
    for i, (rx, keys) in enumerate(sends):
        if not done[i] and rx.search(text):
            time.sleep(0.6); os.write(fd, keys); done[i] = True
            print(f'[sent {keys!r} after /{rx.pattern}/ at {int(time.time()*1000)}]', flush=True)
try: os.kill(pid, 9)
except ProcessLookupError: pass
print(re.sub(r'\s+', ' ', ANSI.sub(b'', buf).decode(errors='replace'))[-3500:])
