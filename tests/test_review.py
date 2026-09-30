import os
import tempfile
import time
import unittest

from hivemind.learning import learning_note
from hivemind.review import render
from hivemind.store import Hive, atomic_write


class ReviewDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hive = Hive(self.tmp.name)

    def candidate(self, project, key="bright"):
        path, text = learning_note("preference", key, "Bright interface", "observed task",
                                   project=project, basis="observation")
        self.hive.write_memory(path, text)
        return path

    def test_scoped_dashboard_links_existing_review_without_promoting_notes(self):
        candidate = self.candidate("alpha")
        other = self.candidate("beta")
        shared = self.candidate("")
        path, text = learning_note("procedure", "repair", "Fix widget", "checked task", project="alpha",
                                  basis="verified-result", trigger="Widget failure", steps=["Repair widget"], evidence="Widget tests passed")
        self.hive.write_memory(path, text)
        stamp = time.time() - 200 * 86400
        os.utime(self.hive.note_path(path), (stamp, stamp))
        session = self.hive.session_start("alpha", "codex", "Fix cosmic widget")["session"]
        from hivemind.learning_ops import review_checkpoint
        draft = review_checkpoint(self.hive, session["id"], stage=True)["staged"]["path"]
        original = self.hive.note_path(candidate).read_bytes()
        result = render(self.hive, project="alpha")
        page = self.hive.note_path("Review.md").read_text(encoding="utf-8")
        self.assertEqual((result["candidates"], result["drafts"], result["stale_procedures"]), (2, 1, 1))
        self.assertIn(candidate, page)
        self.assertIn(shared, page)
        self.assertNotIn(other, page)
        self.assertIn(draft, page)
        self.assertIn(path, page)
        self.assertEqual(self.hive.note_path(candidate).read_bytes(), original)
        self.assertNotIn(candidate, [hit["path"] for hit in self.hive.search("Bright interface", project="alpha")])
        raw = self.hive.note_path(draft).read_text(encoding="utf-8")
        self.hive.note_path(draft).write_text(raw.replace("Status: draft", "Status: reviewed"), encoding="utf-8")
        self.assertEqual(render(self.hive, project="alpha")["drafts"], 0)

    def test_bounded_entries_and_personal_dashboard_preservation(self):
        self.candidate("alpha", "one")
        self.candidate("alpha", "two")
        report = render(self.hive, limit=1)
        self.assertEqual(report["candidates"], 2)
        self.assertTrue(report["truncated"])
        atomic_write(self.hive.note_path("Review.md"), "# My personal review\n")
        with self.assertRaisesRegex(ValueError, "personal note"):
            render(self.hive)
        report = self.hive.export()
        self.assertFalse(report["review_dashboard"]["saved"])
        self.assertEqual(self.hive.note_path("Review.md").read_text(encoding="utf-8"), "# My personal review\n")

    def test_export_creates_empty_review_and_links_it_from_home(self):
        result = self.hive.export()
        self.assertTrue(result["review_dashboard"]["saved"])
        self.assertIn("[[Review|Memory review]]", self.hive.note_path("Home.md").read_text(encoding="utf-8"))
        page = self.hive.note_path("Review.md").read_text(encoding="utf-8")
        self.assertIn("No preferences await approval", page)
        self.assertIn("No checkpoint drafts", page)
        self.assertIn("No procedures", page)
