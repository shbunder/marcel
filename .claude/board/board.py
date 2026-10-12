"""Answer board questions from an export of the build board's `stories` collection.

The board lives in a claude.ai artifact (URL in .claude/rules/board.md). A session exports it with
ArtifactData `list` + `out_dir`, which writes <dir>/stories/<id>.json, then asks this script:

  python3 .claude/board/board.py ready  --board DIR [--runs-on cloud|NUC|Mac]
  python3 .claude/board/board.py check  --board DIR S-04.9
  python3 .claude/board/board.py sync   --board DIR --plan DIR --out writes.json

`ready` lists stories that are todo, whose depends are all done, and whose `touches:` do not overlap a
story that is doing or in review. `check` explains why one story is or is not ready. `sync` compares
plan rows (from stories.py --out-dir) with the board and writes the ArtifactData batch entries that add
new rows and refresh definition fields; it never touches status, owner, branch, pr or notes.
"""
import argparse
import json
import sys
from pathlib import Path

from stories import DEFINITION_FIELDS

IN_FLIGHT = ("doing", "review")


def load(board_dir):
    d = Path(board_dir)
    d = d / "stories" if (d / "stories").is_dir() else d
    return {p.stem: json.loads(p.read_text()) | {"id": p.stem} for p in sorted(d.glob("*.json"))}


def paths(touches):
    return [t.rstrip("*").rstrip("/") for t in (touches or "").split() if not t.startswith("(")]


def overlap(a, b):
    """True when two `touches:` lines could edit the same file. 'everything' overlaps all."""
    if "everything" in (a or "") or "everything" in (b or ""):
        return True
    return any(x == y or x.startswith(y + "/") or y.startswith(x + "/") for x in paths(a) for y in paths(b))


def blockers(rows, sid):
    s = rows[sid]
    reasons = []
    if s.get("status") != "todo":
        reasons.append(f"status is {s.get('status')}")
    for d in s.get("depends") or []:
        if d not in rows:
            reasons.append(f"depends on {d}, which is not on the board")
        elif rows[d].get("status") != "done":
            reasons.append(f"waits for {d} ({rows[d].get('status')})")
    for o in rows.values():
        if o["id"] != sid and o.get("status") in IN_FLIGHT and overlap(s.get("touches"), o.get("touches")):
            reasons.append(f"touches the same files as {o['id']} ({o.get('status')})")
    return reasons


def ready(rows, runs_on=None):
    out = [s for sid, s in rows.items() if not blockers(rows, sid) and (not runs_on or runs_on in str(s.get("runs_on")).split(" + "))]
    return sorted(out, key=lambda s: (str(s.get("wave")), [int(n) for n in s["id"][2:].split(".")] if s["id"].startswith("S-") else [0]))


def sync(rows, plan):
    writes = []
    for sid, p in plan.items():
        if sid not in rows:
            writes.append({"op": "set", "collection": "stories", "doc_id": sid,
                           "data": p | {"owner": "", "branch": "", "pr": "", "blocked_reason": ""}})
            continue
        changed = {k: p[k] for k in DEFINITION_FIELDS if k in p and rows[sid].get(k) != p[k]}
        if changed:
            writes.append({"op": "update", "collection": "stories", "doc_id": sid, "data": changed,
                           "needs_if_version": True})
    gone = sorted(set(rows) - set(plan))
    return writes, gone


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["ready", "check", "sync"])
    ap.add_argument("story", nargs="?")
    ap.add_argument("--board", required=True)
    ap.add_argument("--runs-on")
    ap.add_argument("--plan")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    rows = load(a.board)
    if a.cmd == "ready":
        for s in ready(rows, a.runs_on):
            print(f"{s['id']:8} wave {s.get('wave'):4} {s.get('runs_on'):5} {s.get('size')}  {s.get('title')}")
    elif a.cmd == "check":
        if a.story not in rows:
            print(f"{a.story} is not on the board"); return 1
        why = blockers(rows, a.story)
        print(f"{a.story} is ready" if not why else f"{a.story} is not ready:\n- " + "\n- ".join(why))
        return 0 if not why else 1
    else:
        writes, gone = sync(rows, load(a.plan))
        Path(a.out).write_text(json.dumps(writes, indent=1))
        print(f"{len(writes)} writes -> {a.out}" + (f"; on the board but not in the plan: {', '.join(gone)}" if gone else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
