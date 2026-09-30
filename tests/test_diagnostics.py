from contextlib import asynccontextmanager, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

import hive
from hivemind.diagnostics import inspect, human

ROOT = Path(__file__).resolve().parents[1]


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_core_health_optional_absence_and_legacy_json_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = await inspect(Path(tmp), ROOT, "", "", {})
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["task_access"], "ok")
        self.assertEqual(report["model_calls"], 0)
        optional = {row["name"]: row for row in report["checks"]}
        self.assertEqual(optional["semantic_memory"]["status"], "optional")
        self.assertEqual(optional["code_index"]["status"], "optional")
        self.assertIn("HiveMind health: OK", human(report))

    async def test_missing_sdk_fails_and_supplies_a_quoted_repair(self):
        pins = {"ok": False, "pinned": {"mcp": "1.30.0"}, "installed": {"mcp": None}, "mismatched": {"mcp": {}}}
        with tempfile.TemporaryDirectory(prefix="hive with spaces ") as tmp, \
                patch("hivemind.diagnostics.local_dependency_status", return_value=pins):
            report = await inspect(Path(tmp), ROOT, "", "", {})
        self.assertFalse(report["ok"])
        check = next(row for row in report["checks"] if row["name"] == "bridge_dependencies")
        self.assertIn("pip install -r", check["repair"])
        self.assertIn("requirements.txt", human(report))

    async def test_coordinator_failure_is_structured_and_redacts_secrets(self):
        @asynccontextmanager
        async def unavailable(*args):
            raise ValueError("Sensitive credential do-not-print")
            yield
        with tempfile.TemporaryDirectory() as tmp, patch("hivemind.diagnostics.backend", unavailable):
            report = await inspect(Path(tmp), ROOT, "https://user:private@example.test/mcp?token=private", "", {})
        self.assertFalse(report["ok"])
        self.assertEqual(report["coordinator"], "https://example.test/mcp")
        self.assertNotIn("private", json.dumps(report))
        self.assertNotIn("do-not-print", json.dumps(report))
        self.assertIn("authentication", human(report))

    async def test_degraded_optional_model_is_warning_not_core_failure(self):
        status = {"status": "degraded", "model": "test", "detail": "Offline cache missing"}
        with tempfile.TemporaryDirectory() as tmp, patch("hivemind.semantic.health", return_value=status):
            report = await inspect(Path(tmp), ROOT, "", "", {})
        self.assertTrue(report["ok"])
        check = next(row for row in report["checks"] if row["name"] == "semantic_memory")
        self.assertEqual(check["status"], "warning")
        self.assertIn("semantic-setup", check["repair"])

    async def test_invalid_profile_fails_before_claiming_mcp_is_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = await inspect(Path(tmp), ROOT, "", "", {"tool_profile": "invalid"})
        self.assertFalse(report["ok"])
        check = next(row for row in report["checks"] if row["name"] == "tool_profile")
        self.assertIn("hive.local.json", check["repair"])

    async def test_partial_semantic_install_warns_and_can_be_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            from hivemind.semantic import interpreter
            python = interpreter(tmp)
            python.parent.mkdir(parents=True)
            python.touch()
            report = await inspect(Path(tmp), ROOT, "", "", {})
        self.assertTrue(report["ok"])
        check = next(row for row in report["checks"] if row["name"] == "semantic_memory")
        self.assertEqual(check["status"], "warning")
        self.assertIn("semantic-setup", check["repair"])


class DiagnosticCLITests(unittest.TestCase):
    def test_json_and_human_cli_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = [sys.executable, str(ROOT / "hive.py"), "--root", tmp, "doctor"]
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertTrue(json.loads(result.stdout)["ok"])
            result = subprocess.run(command + ["--human"], capture_output=True, text=True, check=True)
            self.assertIn("HiveMind health: OK", result.stdout)

    def test_failed_required_check_sets_exit_code_with_json_report(self):
        with patch.object(sys, "argv", ["hive.py", "doctor"]), \
                patch.object(hive, "execute", AsyncMock(return_value={"ok": False, "checks": []})), \
                redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as raised:
                hive.main()
        self.assertEqual(raised.exception.code, 1)
        self.assertFalse(json.loads(output.getvalue())["ok"])
