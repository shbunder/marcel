#!/usr/bin/env python3
"""SP2 recorder: poll `claude agents --json --all` and log every change.

Each output line is {"t": <unix ms when the poll started>, "poll_ms": <how long
the CLI call took>, "agents": [...]} — written only when the filtered list
changes. Filter with --name to keep only rows whose name matches.

    python3 record_agents.py --out agents.jsonl --name t1 --interval 0.25
"""

import argparse
import json
import subprocess
import time


def snapshot(names: set[str] | None) -> tuple[list[dict], float]:
    t0 = time.monotonic()
    out = subprocess.run(
        ['claude', 'agents', '--json', '--all'], capture_output=True, text=True, timeout=30
    ).stdout
    poll_ms = (time.monotonic() - t0) * 1000
    try:
        rows = json.loads(out)
    except json.JSONDecodeError:
        rows = [{'_unparsed': out}]
    if names:
        rows = [r for r in rows if r.get('name') in names]
    return rows, poll_ms


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--name', action='append', help='only keep rows with this name (repeatable)')
    ap.add_argument('--interval', type=float, default=0.25)
    ap.add_argument('--duration', type=float, default=3600, help='seconds to record')
    args = ap.parse_args()
    names = set(args.name) if args.name else None

    last = None
    end = time.time() + args.duration
    with open(args.out, 'a') as f:
        while time.time() < end:
            t = int(time.time() * 1000)
            rows, poll_ms = snapshot(names)
            key = json.dumps(rows, sort_keys=True)
            if key != last:
                f.write(json.dumps({'t': t, 'poll_ms': round(poll_ms), 'agents': rows}) + '\n')
                f.flush()
                last = key
            time.sleep(args.interval)


if __name__ == '__main__':
    main()
