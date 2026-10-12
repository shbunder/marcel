"""Tests for the board helpers. Run: python3 -m unittest discover .claude/board"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import board  # noqa: E402
import stories  # noqa: E402


def row(sid, status="todo", depends=(), touches="", **kw):
    return {"id": sid, "status": status, "depends": list(depends), "touches": touches, "wave": "1",
            "runs_on": "cloud", "size": "S", "title": sid, **kw}


class Overlap(unittest.TestCase):
    def test_prefix_paths_overlap(self):
        self.assertTrue(board.overlap("hub/src/ hub/tests/", "hub/tests/test_x.py"))

    def test_siblings_do_not_overlap(self):
        self.assertFalse(board.overlap("hub/src/marcel_hub/projects/", "hub/src/marcel_hub/memory/"))
        self.assertFalse(board.overlap("ios/Marcel/Chat/", "ios/Marcel/Chatter/"))

    def test_everything_overlaps_all(self):
        self.assertTrue(board.overlap("everything (one-off)", "ios/"))


class Ready(unittest.TestCase):
    def rows(self, *rs):
        return {r["id"]: r for r in rs}

    def test_waits_for_undone_dependency_and_spike(self):
        rows = self.rows(row("SP1", "todo"), row("S-01.1", "done"), row("S-03.1", depends=["S-01.1", "SP1"]))
        self.assertEqual(board.blockers(rows, "S-03.1"), ["waits for SP1 (todo)"])
        rows["SP1"]["status"] = "done"
        self.assertEqual([s["id"] for s in board.ready(rows)], ["S-03.1"])

    def test_refuses_story_whose_files_are_in_flight(self):
        rows = self.rows(row("S-04.1", "doing", touches="hub/"), row("S-04.2", touches="hub/src/auth/"))
        self.assertIn("touches the same files as S-04.1 (doing)", board.blockers(rows, "S-04.2"))
        self.assertEqual(board.ready(rows), [])

    def test_filters_by_machine(self):
        rows = self.rows(row("S-12.1", runs_on="Mac"), row("S-04.1"), row("S-03.2", runs_on="cloud + NUC"))
        self.assertEqual([s["id"] for s in board.ready(rows, "Mac")], ["S-12.1"])
        self.assertEqual([s["id"] for s in board.ready(rows, "NUC")], ["S-03.2"])


class Sync(unittest.TestCase):
    def test_keeps_status_and_refreshes_definitions(self):
        on_board = {"S-04.1": row("S-04.1", "doing", title="Old", owner="mac-1")}
        plan = {"S-04.1": row("S-04.1", "todo", title="New"), "S-04.2": row("S-04.2")}
        writes, gone = board.sync(on_board, plan)
        upd = next(w for w in writes if w["doc_id"] == "S-04.1")
        self.assertEqual(upd["op"], "update")
        self.assertEqual(upd["data"], {"title": "New"})
        self.assertNotIn("status", upd["data"])
        self.assertEqual(next(w for w in writes if w["doc_id"] == "S-04.2")["op"], "set")
        self.assertEqual(gone, [])


class PlanParser(unittest.TestCase):
    def test_reads_every_story_and_spike_from_the_plan(self):
        rows = stories.spikes(stories.ROOT) + stories.stories(stories.ROOT)
        ids = [r["id"] for r in rows]
        self.assertEqual(len(ids), len(set(ids)), "story ids must be unique")
        self.assertIn("SP5", ids)
        self.assertEqual(next(r for r in rows if r["id"] == "SP5")["status"], "done")
        acceptance = next(r for r in rows if r["id"] == "S-16.1")  # "depends: everything above"
        self.assertIn("S-12.10", acceptance["depends"])
        self.assertNotIn("S-16.1", acceptance["depends"])
        for r in rows:
            for machine in r["runs_on"].split(" + "):
                self.assertIn(machine, {"cloud", "NUC", "Mac", "iPhone"}, r["id"])
            for d in r["depends"]:
                self.assertIn(d, ids, f"{r['id']} depends on unknown {d}")

    def test_cli_writes_one_file_per_row(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run([sys.executable, str(HERE / "stories.py"), "--out-dir", d], check=True,
                           capture_output=True)
            files = list(Path(d).glob("*.json"))
            self.assertGreater(len(files), 60)
            self.assertIn("depends", json.loads(files[0].read_text()))


if __name__ == "__main__":
    unittest.main()
