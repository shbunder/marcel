"""Print each frame of the mock hub's WebSocket, one line each, until the avatar is idle again.

    uv run python scripts/ws_listen.py [since]

Used by walk.sh; handy on its own to watch what the app would see.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from websockets.asyncio.client import connect

HUB = os.environ.get('HUB', 'http://127.0.0.1:7499').replace('http', 'ws', 1)


def describe(frame: dict) -> str:
    data = frame['data']
    detail = {
        'agent.state': lambda: data['state']['kind'],
        'task.updated': lambda: data['state'],
        'task.event': lambda: data['type'],
        'message.created': lambda: (
            data['milestone']['event'] if data['kind'] == 'milestone' else data['kind']
        ),
        'artifact.created': lambda: data['kind'],
        'approval.created': lambda: data['summary'],
        'approval.resolved': lambda: data['state'],
        'hello': lambda: f'last_event_id={data["last_event_id"]} replayed={data["replayed"]}',
    }.get(frame['type'], lambda: '')()
    return f'{frame["id"]:>3}  {frame["type"]:<18} {detail}'


async def main(since: str | None) -> int:
    url = f'{HUB}/api/ws' + (f'?since={since}' if since else '')
    headers = {'Authorization': 'Bearer mrc_walk'}
    async with connect(url, additional_headers=headers) as ws:
        print('connected', flush=True)
        done = False
        async for raw in ws:
            frame = json.loads(raw)
            print(describe(frame), flush=True)
            state = (
                frame['data'].get('state', {}).get('kind')
                if frame['type'] == 'agent.state'
                else None
            )
            done = done or state == 'done'
            if done and state == 'idle':
                return 0
    return 1


if __name__ == '__main__':
    try:
        sys.exit(
            asyncio.run(asyncio.wait_for(main(sys.argv[1] if len(sys.argv) > 1 else None), 60))
        )
    except TimeoutError:
        print('timed out waiting for the scenario to end', file=sys.stderr)
        sys.exit(1)
