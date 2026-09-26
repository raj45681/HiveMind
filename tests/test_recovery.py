"""Recovery snapshots preserve file bytes without trusting stale previews."""
import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hivemind import recovery
from hivemind.store import Hive


ROOT = Path(__file__).resolve().parents[1]


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q", str(self.repo), in_repo=False)
        (self.repo / "app.py").write_text("original\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        (self.repo / ".env").write_text("private=secret\n", encoding="utf-8")
        self.git("add", "app.py", ".gitignore")
        self.git("add", "-f", ".env")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "-qm", "baseline")
        self.head = self.git("rev-parse", "HEAD")
        (self.root / "hive.local.json").write_text(json.dumps({
            "projects": {"app": str(self.repo)}, "recovery_projects": ["app"]}), encoding="utf-8")
        self.hive = Hive(self.root)

    def git(self, *args, in_repo=True):
        command = ["git"] + (["-C", str(self.repo)] if in_repo else []) + list(args)
        return subprocess.run(command, check=True, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True).stdout.strip()

    def cli(self, *args):
        result = subprocess.run([sys.executable, str(ROOT / "hive.py"), "--root", str(self.root), *args],
                                check=True, capture_output=True, text=True)
        return json.loads(result.stdout)

    def test_milestone_preview_conflict_restore_and_undo(self):
        started = self.hive.session_start("app", "codex", "Fix app")
        ident = started["session"]["id"]
        snapshot = started["recovery_snapshot"]
        self.assertTrue(snapshot["saved"])
        self.assertEqual(snapshot["excluded"], 1)
        self.assertNotIn(".env", recovery._load(self.hive, snapshot["id"])[1]["files"])
        (self.repo / "app.py").write_text("agent edit\n", encoding="utf-8")
        (self.repo / "ignored.txt").write_text("ignored", encoding="utf-8")
        self.assertEqual(self.hive.session_resume("app", ident)["session"]["git_drift"]["status"], "changed")
        self.assertEqual(self.hive.context(project="app", budget_tokens=1000)["session"]["recovery_snapshot"], snapshot["id"])
        preview = self.cli("recovery-diff", snapshot["id"], "--path", "app.py")
        self.assertIn("+agent edit", preview["diff"])
        self.assertTrue(preview["restore_allowed"])
        (self.repo / "app.py").write_text("later hand edit\n", encoding="utf-8")
        with self.assertRaises(subprocess.CalledProcessError):
            self.cli("recovery-restore", snapshot["id"], "app.py",
                     "--expected-current", preview["current_sha256"])
        self.assertEqual((self.repo / "app.py").read_text(), "later hand edit\n")
        fresh = self.cli("recovery-diff", snapshot["id"], "--path", "app.py")
        restored = self.cli("recovery-restore", snapshot["id"], "app.py",
                            "--expected-current", fresh["current_sha256"])
        self.assertEqual((self.repo / "app.py").read_text(), "original\n")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.head)
        with self.assertRaises(subprocess.CalledProcessError):
            self.cli("recovery-undo", restored["undo_id"], "--expected-current", "stale")
        (self.repo / "app.py").write_text("post-restore hand edit\n", encoding="utf-8")
        hand_hash = hashlib.sha256((self.repo / "app.py").read_bytes()).hexdigest()
        with self.assertRaises(ValueError):
            recovery.undo_restore(self.hive, restored["undo_id"], hand_hash)
        (self.repo / "app.py").write_text("original\n", encoding="utf-8")
        undone = self.cli("recovery-undo", restored["undo_id"],
                          "--expected-current", restored["revision"])
        self.assertTrue(undone["undone"])
        self.assertEqual((self.repo / "app.py").read_text(), "later hand edit\n")
        saved = self.hive.session_checkpoint(ident, {"summary": "Milestone after edit"}, 0)
        self.assertTrue(saved["recovery_snapshot"]["saved"])
        self.assertEqual(len(self.cli("recovery-list", "--project", "app")["snapshots"]), 2)

    def test_head_change_and_excluded_file_cannot_restore(self):
        started = self.hive.session_start("app", "codex", "Fix app")
        ident = started["recovery_snapshot"]["id"]
        with self.assertRaises(ValueError):
            recovery.preview(self.hive, ident, ".env")
        (self.repo / "app.py").write_text("new commit\n", encoding="utf-8")
        self.git("add", "app.py")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "-qm", "changed")
        preview = recovery.preview(self.hive, ident, "app.py")
        self.assertFalse(preview["restore_allowed"])
        with self.assertRaisesRegex(ValueError, "HEAD or branch"):
            recovery.restore_file(self.hive, ident, "app.py", preview["current_sha256"])

    def test_deleted_untracked_file_can_be_restored_then_undone(self):
        scratch = self.repo / "scratch" / "notes.txt"
        scratch.parent.mkdir()
        scratch.write_text("unfinished work\n", encoding="utf-8")
        started = self.hive.session_start("app", "codex", "Continue unfinished work")
        ident = started["recovery_snapshot"]["id"]
        scratch.unlink()
        preview = recovery.preview(self.hive, ident, "scratch\\notes.txt")
        self.assertEqual(preview["current_sha256"], "missing")
        restored = recovery.restore_file(self.hive, ident, "scratch\\notes.txt", "missing")
        self.assertEqual(scratch.read_text(), "unfinished work\n")
        recovery.undo_restore(self.hive, restored["undo_id"], restored["revision"])
        self.assertFalse(scratch.exists())
        with self.assertRaises(ValueError):
            recovery.preview(self.hive, ident, "../outside.txt")

    def test_corrupt_archive_is_rejected(self):
        started = self.hive.session_start("app", "codex", "Fix app")
        ident = started["recovery_snapshot"]["id"]
        archive = self.root / "runtime" / "recovery" / (ident + ".zip")
        archive.write_bytes(archive.read_bytes() + b"tampered")
        with self.assertRaisesRegex(ValueError, "archive changed"):
            recovery.preview(self.hive, ident, "app.py")

    def test_snapshot_failure_does_not_lose_session(self):
        with patch("hivemind.recovery.MAX_RAW_BYTES", 1):
            started = self.hive.session_start("app", "codex", "Do work")
        self.assertTrue(started["saved"])
        self.assertFalse(started["recovery_snapshot"]["saved"])
        self.assertEqual(self.hive.session_resume("app", started["session"]["id"])["session"]["revision"], 0)

    def test_worktree_checkpoint_and_opt_in_cli(self):
        self.cli("recovery-disable", "app")
        first = self.hive.session_start("app", "codex", "No snapshots")
        self.assertNotIn("recovery_snapshot", first)
        self.cli("recovery-enable", "app")
        worktree = self.root / "worktree"
        self.git("worktree", "add", "-b", "recovery-test", str(worktree), "HEAD")
        started = self.hive.session_start("app", "codex", "Worktree fix")
        saved = self.hive.session_checkpoint(started["session"]["id"],
                                             {"summary": "Worktree prepared"}, 0, workspace=worktree)
        snapshot = saved["recovery_snapshot"]
        self.assertTrue(snapshot["saved"])
        self.assertEqual(recovery._load(self.hive, snapshot["id"])[0]["workspace"], str(worktree))
        (worktree / "app.py").write_text("worktree edit\n", encoding="utf-8")
        preview = recovery.preview(self.hive, snapshot["id"], "app.py")
        restored = recovery.restore_file(self.hive, snapshot["id"], "app.py", preview["current_sha256"])
        self.assertEqual((worktree / "app.py").read_text(), "original\n")
        self.assertEqual((self.repo / "app.py").read_text(), "original\n")
        pruned = self.cli("recovery-prune", "app", "--keep", "1", "--drop-undos")
        self.assertEqual(pruned["kept"], 1)
        self.assertEqual(pruned["undo_records_removed"], 1)
        with self.assertRaises(ValueError):
            recovery.undo_restore(self.hive, restored["undo_id"], restored["revision"])


if __name__ == "__main__":
    unittest.main()
