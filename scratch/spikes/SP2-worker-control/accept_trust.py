#!/usr/bin/env python3
"""Accept Claude Code's workspace-trust prompt for a directory, headlessly.

`claude --bg` refuses an untrusted workspace ("Workspace not trusted. Run `claude` in
<dir> once and accept the trust prompt"). This drives an interactive `claude` through a
pty, picks "Yes, I trust this folder" on the trust dialog, then exits — the same thing a human would do.

    python3 accept_trust.py <dir>
"""

import os
import pty
import re
import select
import sys
import time

ANSI = re.compile(rb'\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07')


def run(cwd: str, timeout: float = 40) -> str:
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        os.environ['TERM'] = 'xterm-256color'
        os.execvp('claude', ['claude'])
    buf = b''
    answered = exited = False
    answered_at = 0.0
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.5)
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
        text = ANSI.sub(b'', buf).decode(errors='replace')
        if not answered and re.search(r'I\s*trust\s*this\s*folder', text):
            # the dialog defaults to "No, exit": move down to "Yes, I trust this folder"
            time.sleep(0.5)
            os.write(fd, b'\x1b[B')
            time.sleep(0.3)
            os.write(fd, b'\r')
            answered = True
            answered_at = time.time()
        elif answered and not exited and time.time() - answered_at > 4:
            os.write(fd, b'/exit\r')
            exited = True
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        pass
    return ANSI.sub(b'', buf).decode(errors='replace')


if __name__ == '__main__':
    out = run(os.path.abspath(sys.argv[1]))
    print(out[-1500:])
