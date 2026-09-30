"""Incremental indexing must preserve live edits, history, and vault boundaries."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hivemind.store import Hive, atomic_write, file_signature, INDEX_RECHECK_SECONDS


class IndexingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hive = Hive(self.tmp.name)
        self.name = "01-Memory/Solutions/entry.md"
        self.path = self.hive.note_path(self.name)
        atomic_write(self.path, "# Entry\n\nOriginal vocabulary.\n")

    def test_cache_survives_new_process_and_does_not_read_unchanged_notes(self):
        self.assertEqual(self.hive.index()["read_notes"], 1)
        reopened = Hive(self.tmp.name)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("Unchanged note was read")):
            report = reopened.index()
        self.assertEqual(report["indexed_notes"], 1)
        self.assertEqual(report["read_notes"], 0)

    def test_manual_edit_and_atomic_replace_are_detected_with_history(self):
        self.hive.index()
        before = self.path.stat()
        self.path.write_text("# Entry\n\nModified vocabulary.\n", encoding="utf-8")
        os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(self.hive.index()["read_notes"], 1)
        self.assertEqual(self.hive.search("Modified")[0]["path"], self.name)
        atomic_write(self.path, "# Entry\n\nReplaced vocabulary.\n")
        os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(self.hive.index()["updated_notes"], 1)
        self.assertEqual(self.hive.search("Replaced")[0]["path"], self.name)
        self.assertEqual(len(self.hive.note_history(self.name)["versions"]), 3)

    def test_add_rename_delete_and_invalid_content_remove_stale_results(self):
        self.hive.index()
        renamed = self.hive.note_path("01-Memory/Solutions/renamed.md")
        self.path.rename(renamed)
        report = self.hive.index()
        self.assertEqual((report["updated_notes"], report["removed_notes"]), (1, 1))
        self.assertEqual(self.hive.search("Original")[0]["path"], "01-Memory/Solutions/renamed.md")
        renamed.write_bytes(b"\xff\xfe")
        self.assertEqual(self.hive.index()["removed_notes"], 1)
        self.assertEqual(self.hive.search("Original"), [])
        atomic_write(renamed, "# Entry\n\nRestored vocabulary.\n")
        self.assertEqual(self.hive.index()["updated_notes"], 1)
        renamed.unlink()
        self.assertEqual(self.hive.index()["removed_notes"], 1)

    def test_full_reconciliation_detects_content_when_cached_metadata_matches(self):
        self.hive.index()
        self.path.write_text("# Entry\n\nChanged vocabulary.\n", encoding="utf-8")
        # Model metadata-preserving external tools/coarse filesystem timestamps.
        with self.hive.connect(write=True) as c:
            c.execute("UPDATE note_files SET signature=? WHERE path=?", (file_signature(self.path.stat()), self.name))
        self.assertEqual(self.hive.index()["read_notes"], 0)
        self.assertEqual(self.hive.index(force=True)["updated_notes"], 1)
        self.path.write_text("# Entry\n\nPeriodic vocabulary.\n", encoding="utf-8")
        with self.hive.connect(write=True) as c:
            c.execute("UPDATE note_files SET signature=? WHERE path=?", (file_signature(self.path.stat()), self.name))
            c.execute("UPDATE index_state SET value=value-?", (INDEX_RECHECK_SECONDS + 1,))
        self.assertTrue(self.hive.index()["full_check"])
        self.assertEqual(self.hive.search("Periodic")[0]["path"], self.name)

    def test_upgrade_preserves_old_fts_content_as_a_revision(self):
        self.hive.index()
        with self.hive.connect(write=True) as c:
            c.execute("DROP TABLE note_files")
            c.execute("DROP TABLE index_state")
            c.execute("DELETE FROM note_versions")
        self.path.write_text("# Entry\n\nNew vocabulary.\n", encoding="utf-8")
        migrated = Hive(self.tmp.name)
        migrated.index()
        versions = migrated.note_history(self.name)["versions"]
        self.assertEqual(len(versions), 2)
        self.assertTrue(any("Original" in migrated.read_note(self.name, revision=row["revision"])["text"] for row in versions))

    def test_external_file_and_directory_symlinks_cannot_enter_index(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "secret.md"
            target.write_text("# Secret\n\nExternal sentinel.\n", encoding="utf-8")
            try:
                (self.hive.vault / "linked.md").symlink_to(target)
                (self.hive.vault / "linked-dir").symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("Creating symlinks requires privileges on this platform")
            self.hive.index()
            self.assertEqual(self.hive.search("sentinel"), [])
            with self.hive.connect() as c:
                self.assertEqual(c.execute("SELECT count(*) FROM notes").fetchone()[0], 1)

    def test_tool_write_updates_cache_without_duplicate_history(self):
        self.hive.index()
        current = self.hive.read_note(self.name)
        self.hive.write_memory(self.name, "# Entry\n\nTool vocabulary.\n", current["revision"])
        self.assertEqual(self.hive.index()["read_notes"], 0)
        self.assertEqual(len(self.hive.note_history(self.name)["versions"]), 2)
