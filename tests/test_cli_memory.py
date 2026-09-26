"""Exercise the documented local inspection commands without an agent session."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hivemind.store import Hive


ROOT = Path(__file__).resolve().parents[1]


class LocalMemoryCLITests(unittest.TestCase):
    def test_history_diff_restore_audit_and_handoff_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            hive = Hive(tmp)
            path = "01-Memory/Solutions/example.md"
            first = hive.write_memory(path, "# Example\n\nFirst\n")
            second = hive.write_memory(path, "# Example\n\nSecond\n", first["revision"])

            def cli(*args):
                result = subprocess.run([sys.executable, str(ROOT / "hive.py"), "--root", tmp, *args],
                                        capture_output=True, text=True, check=True)
                return json.loads(result.stdout)

            self.assertEqual(len(cli("history", path)["versions"]), 2)
            self.assertIn("+Second", cli("diff", path, first["revision"])["diff"])
            self.assertEqual(cli("read", path, "--revision", first["revision"])["text"], "# Example\n\nFirst\n")
            cli("restore", path, first["revision"], "--expected-revision", second["revision"])
            self.assertEqual(hive.read_note(path)["text"], "# Example\n\nFirst\n")
            self.assertTrue(cli("memory-audit")["read_only"])
            session = hive.session_start("sample", "codex", "Solve cosmic widget")
            hive.session_checkpoint(session["session"]["id"],
                {"summary": "Cosmic widget solved", "status": "completed", "completed": ["Fixed widget"],
                 "verification": ["Tests passed"]}, 0)
            self.assertEqual(cli("handoff-search", "cosmic widget", "--project", "sample")[0]["source_type"],
                             "checkpoint")


if __name__ == "__main__":
    unittest.main()
