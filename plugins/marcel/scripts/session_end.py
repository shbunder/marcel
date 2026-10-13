#!/usr/bin/env python3
"""Tell the hub when a session finishes a turn or ends (S-05.8, ADR-010).

The marcel plugin runs this for two hooks (`hooks/hooks.json`), so the hub hears it from Claude
Code itself instead of relying on a worker remembering `report()`:

- **`SessionEnd`**: posts `{event: "session_end", reason}` to the hub's `POST /channel/session-end`
  (session-api.yaml `sessionEnded`). A worker that ended owing a report is marked `failed` there,
  and the brain is told. Prints nothing.
- **`Stop`**: posts `{event: "stop"}`. When the hub answers `report_owed: true`, it prints
  `{"decision": "block", "reason": "Call report(done) or report(failed) before you finish."}`, so
  the worker is reminded once. Never when the hook input's `stop_hook_active` is true (Claude Code
  is already continuing because of this hook), so a worker is never looped.

Identity is the channel's: the hub secret from `$CLAUDE_PLUGIN_DATA/hub-secret` in
`X-Marcel-Secret`, the hook's `session_id` in `X-Marcel-Session`, on `http://127.0.0.1:7420`
(`MARCEL_HUB_URL` for development). Without a secret file (a computer that is not the NUC) it asks
nothing. A hub that does not answer means "nothing owed". It always exits 0. Standard library only.
"""

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'src'))
from marcel_tools.hub import DEFAULT_HUB_URL, Transport, UrllibTransport  # noqa: E402

PATH = '/channel/session-end'
TIMEOUT = 3.0  # seconds; SessionEnd gets 5 s in hooks.json
REASON_MAX = 64  # session-api.yaml SessionEnd.reason
REMINDER = 'Call report(done) or report(failed) before you finish.'


def secret(env: Mapping[str, str]) -> str | None:
    data_dir = env.get('CLAUDE_PLUGIN_DATA', '')
    if not data_dir:
        return None
    try:
        value = (Path(data_dir) / 'hub-secret').read_text().strip()
    except OSError:
        return None
    return value or None


def ask(transport: Transport, session_id: str, key: str, body: dict[str, Any]) -> bool:
    """Post to the hub; True when it answers that a report is owed. Any failure: False."""
    headers = {'X-Marcel-Secret': key, 'X-Marcel-Session': session_id, 'Accept': 'application/json'}
    try:
        status, answer = transport.request('POST', PATH, params=None, body=body, headers=headers)
    except OSError as exc:
        print(f'session_end: the hub is not answering ({exc}).', file=sys.stderr)
        return False
    if status != 200 or not isinstance(answer, dict):
        message = answer.get('message') if isinstance(answer, dict) else None
        print(
            f'session_end: the hub answered {status}: {message or "no explanation"}',
            file=sys.stderr,
        )
        return False
    return answer.get('report_owed') is True


def run(hook: Mapping[str, Any], env: Mapping[str, str], transport: Transport | None) -> str:
    """What to print for this hook input ('' for nothing)."""
    event = hook.get('hook_event_name')
    session_id = str(hook.get('session_id') or '')
    if event not in ('Stop', 'SessionEnd') or not session_id:
        return ''
    if event == 'Stop' and hook.get('stop_hook_active'):
        return ''  # already continuing because of this hook: never loop
    key = secret(env)
    if key is None:
        return ''  # not the NUC, or no secret deployed: nothing to tell
    transport = transport or UrllibTransport(env.get('MARCEL_HUB_URL') or DEFAULT_HUB_URL, TIMEOUT)
    if event == 'SessionEnd':
        body: dict[str, Any] = {'event': 'session_end'}
        reason = str(hook.get('reason') or '').strip()[:REASON_MAX]
        if reason:
            body['reason'] = reason
        ask(transport, session_id, key, body)
        return ''
    if ask(transport, session_id, key, {'event': 'stop'}):
        return json.dumps({'decision': 'block', 'reason': REMINDER})
    return ''


def main(stdin: TextIO = sys.stdin, env: Mapping[str, str] = os.environ) -> int:
    try:
        hook = json.loads(stdin.read() or '{}')
        print(run(hook if isinstance(hook, dict) else {}, env, None), end='')
    except Exception as exc:  # never block the session over this hook
        print(f'session_end: {exc}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
