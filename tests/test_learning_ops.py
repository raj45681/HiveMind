"""End-to-end local review boundaries for memory learning."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hivemind.learning import learning_note
from hivemind.store import Hive


ROOT = Path(__file__).resolve().parents[1]


class LearningOperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hive = Hive(self.tmp.name)

    def cli(self, *args):
        result = subprocess.run([sys.executable, str(ROOT / "hive.py"), "--root", self.tmp.name, *args],
                                check=True, capture_output=True, text=True)
        return json.loads(result.stdout)

    def test_candidate_requires_explicit_revision_checked_approval_and_preserves_scope(self):
        path, body = learning_note("preference", "bright-ui", "Prefer bright UI", "observed in task",
                                   project="alpha", basis="observation")
        saved = self.hive.write_memory(path, body)
        self.assertNotIn("bright-ui", json.dumps(self.hive.context(project="alpha", query="bright UI")))
        self.assertEqual(self.cli("candidate-inbox")["total"], 1)
        with self.assertRaises(subprocess.CalledProcessError):
            self.cli("candidate-approve", path, "--expected-revision", "stale")
        promoted = self.cli("candidate-approve", path, "--expected-revision", saved["revision"])
        self.assertIn("user-approved", self.hive.read_note(promoted["path"])["text"])
        self.assertIn("Prefer bright UI", json.dumps(self.hive.context(project="alpha")))
        self.assertNotIn("Prefer bright UI", json.dumps(self.hive.context(project="beta")))
        self.assertEqual(self.cli("candidate-inbox")["total"], 0)
        self.assertTrue((self.hive.vault / promoted["candidate_archived"]).exists())
        # A later observation with the same key can still be rejected and archived.
        another = self.hive.write_memory(path, body)
        rejected = self.cli("candidate-reject", path, "--expected-revision", another["revision"])
        self.assertEqual(rejected["decision"], "rejected")
        self.assertNotEqual(rejected["path"], promoted["candidate_archived"])
        self.assertTrue((self.hive.vault / rejected["path"]).exists())

    def test_procedure_report_retrieval_and_reversible_archive(self):
        path, body = learning_note("procedure", "cache-repair", "Repair stale cache", "session-1",
                                   project="alpha", basis="verified-result", trigger="Old cache entry",
                                   steps=["Clear stale entry", "Rebuild index"], evidence="Two tests passed")
        saved = self.hive.write_memory(path, body)
        self.assertIn(path, [hit["path"] for hit in self.hive.search("stale cache", project="alpha")])
        self.hive.read_note(path)
        report = self.cli("procedure-report", "--project", "alpha")
        self.assertEqual(report["procedure_count"], 1)
        self.assertEqual(report["procedures"][0]["uses"], 0)
        with self.assertRaises(subprocess.CalledProcessError):
            self.cli("procedure-used", path, "--expected-revision", "stale",
                     "--source", "task-1", "--evidence", "Two tests passed")
        self.cli("procedure-used", path, "--expected-revision", saved["revision"],
                 "--source", "task-1", "--evidence", "Two tests passed")
        self.assertEqual(self.cli("procedure-report", "--project", "alpha")["procedures"][0]["uses"], 1)
        archived = self.cli("procedure-archive", path, "--expected-revision", saved["revision"])
        self.assertFalse(self.hive.search("stale cache", project="alpha"))
        restored = self.cli("procedure-unarchive", archived["path"], "--expected-revision", archived["revision"])
        self.assertEqual(restored["path"], path)
        self.assertIn(path, [hit["path"] for hit in self.hive.search("stale cache", project="alpha")])

    def test_review_stages_only_a_draft_and_keeps_it_out_of_recall(self):
        started = self.hive.session_start("alpha", "codex", "Repair cosmic widget")
        ident = started["session"]["id"]
        self.hive.session_checkpoint(ident, {"summary": "Cosmic widget repaired", "status": "completed",
            "completed": ["Changed widget algorithm"], "verification": ["Widget test passed"]}, 0)
        packet = self.cli("review-checkpoint", ident, "--budget", "512", "--stage")
        self.assertEqual(packet["model_calls"], 0)
        self.assertLessEqual(packet["estimated_tokens"], 512)
        self.assertIn("Review-Queue", packet["staged"]["path"])
        again = self.cli("review-checkpoint", ident, "--budget", "512", "--stage")
        self.assertTrue(again["staged"]["existing"])
        self.assertNotIn(packet["staged"]["path"],
                         [hit["path"] for hit in self.hive.search("cosmic widget", project="alpha")])

    def test_procedure_requires_actual_verification_and_steps(self):
        with self.assertRaises(ValueError):
            learning_note("procedure", "guess", "Maybe useful", "guess", trigger="Maybe",
                          steps=["Try it"], evidence="unverified", basis="observation")


if __name__ == "__main__":
    unittest.main()
