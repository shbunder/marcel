"""Read every spike and story out of plan/ and print them as JSON rows for the build board.

Usage: python3 .claude/board/stories.py > rows.json      (or --out-dir DIR: one <id>.json per row)

A story starts with a heading like:  ### S-12.3  Chat: the one conversation   runs-on: Mac · size: L (...)
and may carry `depends:`, `touches:` and `behaviours:` lines right below it.
A spike starts with:  ### SP1 — Title  ·  NUC  ·  blocks F05  ·  **DONE …**   (in plan/03-spikes.md)

Only the *definition* fields come from git. `status`, `owner`, `branch`, `pr` and `blocked_reason` live on
the board and are never overwritten by a sync (see .claude/rules/board.md).
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEAD = re.compile(r"^### (S-\d+\.\d+)\s+(.+?)\s{2,}runs-on:\s*(.+?)\s*·\s*size:\s*(\w+)", re.M)
FEAT = re.compile(r"^# (F\d+)\s+—\s+(.+?)(?:\s{2,}\((.+)\))?$", re.M)
SPIKE = re.compile(r"^### (SP\d+)\s+—\s+(.+?)\s+·\s+(\w+)\s+·\s+blocks ([^·\n]+)(.*)$", re.M)
DEFINITION_FIELDS = ("feature", "feature_title", "wave", "title", "runs_on", "size", "depends", "touches",
                     "behaviours", "source")


def wave_of(text):
    m = re.search(r"waves?\s+([\d–\-, ]+)", text or "")
    return m.group(1).strip(" ,") if m else ""


def spikes(root):
    path = root / "plan" / "03-spikes.md"
    rows = []
    for m in SPIKE.finditer(path.read_text()):
        sid, title, runs_on, blocks, rest = m.groups()
        rows.append({
            "id": sid, "feature": "SP", "feature_title": "Spikes", "wave": "0", "title": title.strip(),
            "runs_on": runs_on, "size": "S", "depends": [], "touches": f"scratch/spikes/{sid}-*",
            "behaviours": f"blocks {blocks.strip()}", "source": "plan/03-spikes.md",
            "status": "done" if "DONE" in rest else "todo",
        })
    return rows


def stories(root):
    rows = []
    for path in sorted((root / "plan" / "features").glob("*.md")):
        src = path.read_text()
        feats = [(m.start(), m.group(1), m.group(2).strip(), wave_of(m.group(3))) for m in FEAT.finditer(src)]
        for m in HEAD.finditer(src):
            sid, title, runs_on, size = m.groups()
            feat = max((f for f in feats if f[0] < m.start()), default=(0, "F?", "", ""), key=lambda f: f[0])
            end = src.find("\n### ", m.end())
            block = src[m.end(): end if end > 0 else len(src)]

            def field(name):
                f = re.search(rf"^{name}:\s*(.+)$", block, re.M)
                return f.group(1).strip() if f else ""

            deps_line = field("depends")
            depends = re.findall(r"\b(S-\d+\.\d+|SP\d+)\b", deps_line.split("touches:")[0])
            touches = deps_line.split("touches:")[1].strip() if "touches:" in deps_line else field("touches")
            if "everything" in deps_line.split("touches:")[0]:
                depends = ["*"]  # expanded below, once every story is known
            rows.append({
                "id": sid, "feature": feat[1], "feature_title": feat[2], "wave": feat[3],
                "title": title.strip(), "runs_on": runs_on.strip(), "size": size,
                "depends": depends, "touches": touches, "behaviours": field("behaviours"),
                "status": "todo", "source": f"plan/features/{path.name}",
            })
    for r in rows:
        if r["depends"] == ["*"]:
            r["depends"] = [o["id"] for o in rows if o["feature"] != r["feature"]]
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", help="write one <id>.json per row instead of printing a list")
    args = ap.parse_args(argv)
    rows = spikes(ROOT) + stories(ROOT)
    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        for r in rows:
            (out / f"{r['id']}.json").write_text(json.dumps(r, indent=1))
    else:
        json.dump(rows, sys.stdout, indent=1)
    print(f"{len(rows)} rows ({sum(r['feature'] == 'SP' for r in rows)} spikes)", file=sys.stderr)


if __name__ == "__main__":
    main()
