import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hivemind.project import BEGIN, END, attach, managed_block, prepare
from hivemind.store import Hive


class ProjectTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Hive home"
        self.root.mkdir()
        self.project = Path(self.temp.name) / "My Project"
        self.project.mkdir()

    async def test_preserves_existing_rules_and_is_idempotent(self):
        original = b"\xef\xbb\xbf# My rules\r\n\r\nRun npm test.\r\n"
        (self.project / "AGENTS.md").write_bytes(original)
        (self.project / "AGENTS.override.md").write_text("# Override\nPreserve me\n")
        (self.project / "GEMINI.md").write_text("# Gemini\nExisting policy\n")
        (self.project / ".gitignore").write_text("node_modules/\n")
        first = await attach(self.root, self.project, skip_register=True)
        self.assertEqual(first["project"], "my-project")
        self.assertTrue((self.project / "AGENTS.md").read_bytes().startswith(original))
        self.assertEqual((Path(first["backups"]) / "AGENTS.md").read_bytes(), original)
        snapshot = {p.relative_to(self.project): p.read_bytes() for p in self.project.rglob("*") if p.is_file()}
        second = await attach(self.root, self.project, skip_register=True)
        self.assertEqual(second["changed_files"], [])
        self.assertEqual(snapshot, {p.relative_to(self.project): p.read_bytes() for p in self.project.rglob("*") if p.is_file()})
        self.assertEqual((self.project / "AGENTS.md").read_text(encoding="utf-8").count(BEGIN), 1)
        hive = Hive(self.root)
        note = hive.read_note("03-Projects/my-project/Current-State.md")
        hive.write_memory(note["path"], "# My real progress", note["revision"])
        await attach(self.root, self.project, skip_register=True)
        self.assertEqual(hive.read_note(note["path"])["text"], "# My real progress")

    async def test_dry_run_has_no_files_or_database_side_effects(self):
        result = await attach(self.root, self.project, dry_run=True)
        self.assertEqual(result["project"], "my-project")
        self.assertEqual(list(self.project.iterdir()), [])
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_malformed_markers_and_foreign_manifest_fail_before_writes(self):
        (self.project / "AGENTS.md").write_text(BEGIN + "\nbroken")
        with self.assertRaises(ValueError):
            await attach(self.root, self.project, skip_register=True)
        self.assertEqual(list(self.root.iterdir()), [])
        (self.project / "AGENTS.md").write_text("normal")
        (self.project / ".hivemind").mkdir()
        (self.project / ".hivemind/project.json").write_text('{"unrelated":true}')
        with self.assertRaises(ValueError):
            await attach(self.root, self.project, skip_register=True)
        self.assertEqual((self.project / "AGENTS.md").read_text(), "normal")

    async def test_stable_project_id_survives_clone_on_new_device(self):
        await attach(self.root, self.project, name="shared-app", skip_register=True)
        other_home = Path(self.temp.name) / "device-two-hive"
        other_home.mkdir()
        # Same tracked project identity, with a different device's Hive install.
        result = await attach(other_home, self.project, skip_register=True)
        self.assertEqual(result["project"], "shared-app")
        local = json.loads((self.project / ".hivemind/local.json").read_text())
        self.assertEqual(local["hive_home"], str(other_home.resolve()))

    async def test_does_not_remap_existing_project_or_rename_memory(self):
        await attach(self.root, self.project, name="app", skip_register=True)
        with self.assertRaises(ValueError):
            await attach(self.root, self.project, name="renamed", skip_register=True)
        other = Path(self.temp.name) / "other"
        other.mkdir()
        with self.assertRaises(ValueError):
            await attach(self.root, other, name="app", skip_register=True)
        self.assertFalse((other / "AGENTS.md").exists())

    async def test_registration_failure_keeps_project_untouched(self):
        with patch("hivemind.project.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "fixture failure")):
            with self.assertRaisesRegex(ValueError, "registration failed"):
                await attach(self.root, self.project)
        self.assertEqual(list(self.project.iterdir()), [])

    async def test_preserves_edit_during_registration(self):
        rules = self.project / "AGENTS.md"
        rules.write_text("old")
        def concurrent_edit(*args, **kwargs):
            rules.write_text("user changed this")
            return subprocess.CompletedProcess([], 0, "", "")
        with patch("hivemind.project.subprocess.run", side_effect=concurrent_edit):
            with self.assertRaisesRegex(ValueError, "changed during installation"):
                await attach(self.root, self.project)
        self.assertEqual(rules.read_text(), "user changed this")
        self.assertFalse((self.project / ".hivemind/project.json").exists())

    async def test_installer_rejects_file_symlink_outside_project(self):
        outside = Path(self.temp.name) / "outside.md"
        outside.write_text("do not modify")
        try:
            (self.project / "AGENTS.md").symlink_to(outside)
        except OSError:
            self.skipTest("Symlinks require platform permission")
        with self.assertRaises(ValueError):
            await attach(self.root, self.project, skip_register=True)
        self.assertEqual(outside.read_text(), "do not modify")

    def test_replacing_managed_section_preserves_surrounding_user_text(self):
        original = "before\n" + BEGIN + "\nold\n" + END + "\nafter\n"
        self.assertEqual(managed_block(original, "new"), "before\n" + BEGIN + "\nnew\n" + END + "\nafter\n")


if __name__ == "__main__":
    unittest.main()
