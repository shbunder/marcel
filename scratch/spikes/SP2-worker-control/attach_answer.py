#!/usr/bin/env python3
"""Answer a background session's pending permission prompt through `claude attach` on a pty.

    python3 attach_answer.py <short-id> <key>     # key e.g. "1" (Yes) or "4" (No)

Waits for "Do you want to proceed", sends the key, waits, then detaches with Ctrl+Z
(the session keeps running).
"""

import os
import pty
import re
import select
import sys
import time

ANSI = re.compile(rb'\x1b\[[0-9;?<>=]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-B]')


def main(short: str, key: str) -> None:
    pid, fd = pty.fork()
    if pid == 0:
        env = {k: v for k, v in os.environ.items() if not k.startswith(('CLAUDE', 'AI_AGENT', 'MCP_'))}
        env['TERM'] = 'xterm-256color'
        os.execvpe('claude', ['claude', 'attach', short], env)
    buf = b''
    sent = False
    t_sent = 0.0
    end = time.time() + 40
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.3)
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
        text = ANSI.sub(b'', buf).decode(errors='replace')
        if not sent and re.search(r'proceed\?', text.replace(' ', '')):
            time.sleep(0.5)
            os.write(fd, key.encode())
            sent, t_sent = True, time.time()
            print(f'sent {key!r} at {int(t_sent * 1000)}')
        if sent and time.time() - t_sent > 5:
            os.write(fd, b'\x1a')  # Ctrl+Z: detach, session keeps running
            time.sleep(1)
            break
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        pass
    print('prompt seen:', sent)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
