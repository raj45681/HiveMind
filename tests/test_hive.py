import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from hivemind.store import Hive, atomic_write
from hivemind.worker import prepare_workspace, parse_result, run_task
from hivemind.transport import Local

ROOT = Path(__file__).resolve().parents[1]


def spec(**overrides):
    return {"title": "Inspect source", "objective": "Read source and report findings", "acceptance": ["Cite evidence"], **overrides}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hive = Hive(self.temp.name)

    def test_concurrent_claims_have_one_winner(self):
        task = self.hive.create_task(spec())
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda n: self.hive.claim(f"worker{n}", "codex", task["id"]), range(8)))
        self.assertEqual(sum(x is not None for x in results), 1)
        self.assertNotIn("claim_token", self.hive.get_task(task["id"]))
        self.assertNotIn(next(x["claim_token"] for x in results if x), (self.hive.vault / "Home.md").read_text())

    def test_dependencies_and_machine_assignment(self):
        first = self.hive.create_task(spec())
        second = self.hive.create_task(spec(depends_on=[first["id"]], machine="linux"))
        self.assertIsNone(self.hive.claim("b", "codex", second["id"], "linux"))
        claim = self.hive.claim("a", "codex", first["id"])
        self.hive.finish(first["id"], claim["claim_token"], {"status": "done", "summary": "Checked", "verification": ["Evidence checked"]})
        self.assertIsNone(self.hive.claim("b", "codex", second["id"], "windows"))
        self.assertIsNotNone(self.hive.claim("b", "codex", second["id"], "linux"))

    def test_stale_claim_cannot_finish_or_restart_automatically(self):
        task = self.hive.create_task(spec())
        claim = self.hive.claim("a", "codex", task["id"])
        with self.hive.connect(write=True) as c:
            c.execute("UPDATE tasks SET lease=? WHERE id=?", (time.time() - 1, task["id"]))
        self.assertIsNone(self.hive.claim("b", "codex", task["id"]))
        self.assertEqual(self.hive.get_task(task["id"])["status"], "blocked")
        with self.assertRaises(ValueError):
            self.hive.finish(task["id"], claim["claim_token"], {"status": "done", "summary": "No", "verification": ["No"]})
        self.hive.requeue(task["id"])
        new = self.hive.claim("b", "codex", task["id"])
        self.assertNotEqual(new["claim_token"], claim["claim_token"])

    def test_owner_and_evidence_required(self):
        task = self.hive.create_task(spec())
        claim = self.hive.claim("a", "codex", task["id"])
        with self.assertRaises(ValueError):
            self.hive.finish(task["id"], "wrong", {"status": "blocked", "summary": "No"})
        with self.assertRaises(ValueError):
            self.hive.finish(task["id"], claim["claim_token"], {"status": "done", "summary": "No evidence"})

    def test_memory_compare_and_swap_and_windows_newlines(self):
        path = "01-Memory/test.md"
        self.hive.write_memory(path, "# Test\n\nFirst")
        note = self.hive.read_note(path)
        self.hive.write_memory(path, "# Test\n\nSecond", note["revision"])
        with self.assertRaises(ValueError):
            self.hive.write_memory(path, "Oops", note["revision"])
        self.hive.note_path(path).write_bytes(b"# Windows\r\n\r\nCRLF")
        note = self.hive.read_note(path)
        self.hive.write_memory(path, "# Normalized\n", note["revision"])

    def test_note_history_diff_and_checked_restore(self):
        path = "01-Memory/history.md"
        first = self.hive.write_memory(path, "# History\n\nFirst\n")
        second = self.hive.write_memory(path, "# History\n\nSecond\n", first["revision"])
        self.hive.note_path(path).write_bytes(b"# History\r\n\r\nManual edit\r\n")
        self.hive.index()
        versions = self.hive.note_history(path)["versions"]
        self.assertEqual(len(versions), 3)
        self.assertIn("-Second", self.hive.note_diff(path, second["revision"])["diff"])
        with self.assertRaises(ValueError):
            self.hive.restore_note(path, first["revision"], second["revision"])
        current = self.hive.read_note(path)
        self.hive.restore_note(path, first["revision"], current["revision"])
        self.assertEqual(self.hive.read_note(path)["text"], "# History\n\nFirst\n")
        self.assertEqual(self.hive.read_note(path, revision=versions[0]["revision"])["text"],
                         "# History\r\n\r\nManual edit\r\n")
        self.assertEqual(len(self.hive.read_note(path, include_history=True)["history"]), 3)

    def test_read_only_audit_flags_structural_issues(self):
        good = "# Preference\n\nKind: preference\nBasis: user-stated\nProject: cross-project\nRecorded: 2026-01-01\nSource: user\n\nKeep responses concise."
        self.hive.write_memory("01-Memory/Solutions/one.md", good)
        self.hive.write_memory("01-Memory/Solutions/two.md", good)
        self.hive.write_memory("03-Projects/alpha/Decisions/bad.md",
                               "# Bad\n\nKind: decision\nBasis: observation\nProject: beta\n")
        before = self.hive.note_path("01-Memory/Solutions/two.md").read_bytes()
        result = self.hive.memory_audit(project="alpha")
        self.assertTrue(result["read_only"])
        self.assertIn("duplicate", {issue["code"] for issue in result["issues"]})
        self.assertIn("project_mismatch", {issue["code"] for issue in result["issues"]})
        self.assertEqual(self.hive.note_path("01-Memory/Solutions/two.md").read_bytes(), before)

    def test_handoff_search_is_scoped_and_opt_in(self):
        session = self.hive.session_start("alpha", "codex", "Investigate cosmic widget")
        self.hive.session_checkpoint(session["session"]["id"],
            {"summary": "Cosmic widget fixed with bounded cache", "completed": ["Changed cache"],
             "verification": ["Unit tests passed"], "status": "completed"}, 0)
        task = self.hive.create_task(spec(project="alpha", title="Cosmic widget"))
        claim = self.hive.claim("worker", "codex", task["id"])
        self.hive.finish(task["id"], claim["claim_token"],
                         {"status": "done", "summary": "Cosmic widget handoff", "verification": ["Checked"]})
        self.hive.send("codex", "grok", "Cosmic widget needs review", task["id"])
        hits = self.hive.handoff_search("cosmic widget", project="alpha")
        self.assertEqual({hit["source_type"] for hit in hits}, {"checkpoint", "task_handoff", "message"})
        self.assertEqual(self.hive.handoff_search("cosmic widget", project="beta"), [])
        self.assertTrue(any(hit["source_type"] == "checkpoint" for hit in
                            self.hive.search("cosmic widget", project="alpha", include_handoffs=True)))
        self.assertFalse(any(hit.get("source_type") for hit in self.hive.search("cosmic widget", project="alpha")))

    def test_memory_boundary_and_personality_protection(self):
        for path in ("../outside.md", ".obsidian/config.md", "00-System/HIVE.md", "C:/secret.md"):
            with self.assertRaises(ValueError):
                self.hive.write_memory(path, "overwrite")
        for path in ("../outside.md", "C:/secret.md", "01-Memory/a.txt"):
            with self.assertRaises(ValueError):
                self.hive.read_note(path)

    def test_search_is_bounded_fresh_and_excludes_archive(self):
        for n in range(8):
            self.hive.write_memory(f"01-Memory/{n}.md", "# Auth\n\nrefresh tokens")
        atomic_write(self.hive.vault / "99-Archive" / "old.md", "# Old\nuniquearchive")
        self.assertEqual(len(self.hive.search("refresh", 100)), 5)
        self.assertEqual(self.hive.search("uniquearchive"), [])
        self.assertEqual(len(self.hive.search("uniquearchive", archive=True)), 1)
        self.hive.note_path("01-Memory/0.md").unlink()
        self.hive.index()
        self.assertFalse(any(x["path"] == "01-Memory/0.md" for x in self.hive.search("refresh")))
        self.assertEqual(self.hive.search("\" OR * ()"), [])

    def test_message_cursor_and_size(self):
        self.hive.send("codex", "grok", "Review this", "")
        first = self.hive.inbox("grok")
        self.assertEqual(len(first["messages"]), 1)
        self.assertEqual(self.hive.inbox("grok", first["next_cursor"])["messages"], [])
        with self.assertRaises(ValueError):
            self.hive.send("a", "b", "x" * 1601)


class WorktreeTests(unittest.TestCase):
    def test_write_is_isolated_and_dirty_repo_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
            (repo / "hello.txt").write_text("original")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=Hive Test", "-c", "user.email=test@example.invalid",
                            "commit", "-m", "Fixture"], check=True, capture_output=True)
            config = {"projects": {"sample": str(repo)}}
            worktree = prepare_workspace(root, {"project": "sample", "access": "write"}, "TASK-test", config)
            (worktree / "hello.txt").write_text("changed")
            self.assertEqual((repo / "hello.txt").read_text(), "original")
            (repo / "hello.txt").write_text("dirty")
            with self.assertRaises(ValueError):
                prepare_workspace(root, {"project": "sample", "access": "write"}, "TASK-next", config)


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_child_process_to_verified_handoff_for_each_adapter(self):
        # A real subprocess simulates vendor output; no model is invoked.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = Local(root)
            fixture = root / "fixture.py"
            fixture.write_text('''import json, sys
from pathlib import Path
agent, run_dir = sys.argv[1], Path(sys.argv[2])
sys.stdin.read()
payload = {"status":"done","summary":"Fixture checked","artifacts":[],"verification":["Fixture evidence"],"unresolved":[]}
if agent == "codex":
    (run_dir / "result.json").write_text(json.dumps(payload))
elif agent == "grok":
    print(json.dumps({"result": json.dumps(payload)}))
else:
    print(json.dumps({"event":"init","init":{}}))
    print(json.dumps({"event":"result","result":{"status":"SUCCESS","structured_output":payload}}))
''')
            for agent in ("codex", "grok", "antigravity"):
                task = local.hive.create_task(spec(agent=agent))
                def fake_command(name, prompt, run_dir, access, config):
                    schema = json.loads((run_dir / "schema.json").read_text())
                    self.assertEqual(set(schema["required"]), set(schema["properties"]))
                    return [sys.executable, str(fixture), name, str(run_dir)], b"fixture"
                with patch("hivemind.worker.command", side_effect=fake_command):
                    result = await run_task(root, local, task["id"], {})
                self.assertEqual(result["status"], "done")
                self.assertTrue(result['session_checkpoint']['saved'])
                self.assertEqual(local.hive.session_resume('hivemind')['session']['status'], 'completed')
                self.assertTrue((root / "vault" / "06-Handoffs" / (task["id"] + ".md")).exists())

    async def test_dry_run_does_not_claim_or_call_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Local(tmp)
            task = local.hive.create_task(spec())
            result = await run_task(Path(tmp), local, task["id"], {}, dry_run=True)
            self.assertFalse(result["will_launch_model"])
            self.assertEqual(local.hive.get_task(task["id"])["status"], "pending")

    async def test_failed_dispatch_blocks_with_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Local(tmp)
            task = local.hive.create_task(spec(project="missing"))
            with self.assertRaises(ValueError):
                await run_task(Path(tmp), local, task["id"], {"projects": {}})
            self.assertEqual(local.hive.get_task(task["id"])["status"], "blocked")
            self.assertEqual(local.hive.session_resume('missing')['session']['status'], 'blocked')
            self.assertTrue((local.hive.vault / "06-Handoffs" / (task["id"] + ".md")).exists())

    def test_result_adapters_reject_errors(self):
        payload = {"status": "done", "summary": "Read", "verification": ["Checked"]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "stdout.log").write_text(json.dumps({"status": "SUCCESS", "structured_output": payload}))
            self.assertEqual(parse_result("antigravity", path).status, "done")
            (path / "stdout.log").write_text(json.dumps({"result": json.dumps(payload)}))
            self.assertEqual(parse_result("grok", path).status, "done")
            (path / "result.json").write_text(json.dumps(payload))
            self.assertEqual(parse_result("codex", path).status, "done")
            (path / "stdout.log").write_text(json.dumps({"status": "ERROR", "structured_output": payload}))
            with self.assertRaises(ValueError):
                parse_result("antigravity", path)


if __name__ == "__main__":
    unittest.main()
