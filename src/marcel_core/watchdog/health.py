"""Health-check poller for the Marcel watchdog.

Uses only the Python standard library so this module can never be broken by a
bad dependency install.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request


def poll_health(
    port: int = 8000,
    timeout_s: float = 30.0,
    interval_s: float = 2.0,
) -> bool:
    """Poll ``GET http://localhost:{port}/health`` until 200 OK or timeout.

    Args:
        port: TCP port on localhost to poll.
        timeout_s: Maximum number of seconds to wait before giving up.
        interval_s: Seconds to wait between attempts.

    Returns:
        ``True`` if a 200 response is received within *timeout_s* seconds,
        ``False`` if the deadline is exceeded.
    """
    url = f"http://localhost:{port}/health"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=interval_s) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(interval_s)
    return False
