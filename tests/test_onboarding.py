import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hivemind.onboarding import personalize, smoke_test
from hivemind.store import Hive
from scripts.register_agents import register_agents


ROOT = Path(__file__).resolve().parents[1]


class OnboardingTests(unittest.TestCase):
    def test_fresh_project_setup_and_rerun_in_isolated_bundle(self):
        with tempfile.TemporaryDirectory(prefix="Hive isolated setup ") as tmp:
            bundle = Path(tmp) / "HiveMind"
            project = Path(tmp) / "My Project"
            bundle.mkdir()
            project.mkdir()
            for name in ("bootstrap.py", "hive.py", "requirements.txt"):
                shutil.copy2(ROOT / name, bundle / name)
            shutil.copytree(ROOT / "hivemind", bundle / "hivemind",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.copytree(ROOT / "templates/vault", bundle / "templates/vault")
            try:
                (bundle / ".venv").symlink_to(ROOT / ".venv", target_is_directory=True)
            except OSError:
                self.skipTest("Local virtualenv symlinks are unavailable")
            for attempt in range(2):
                result = subprocess.run([sys.executable, str(bundle / "bootstrap.py"), str(project), "--skip-register"],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("MCP bridge: ready", result.stdout)
                self.assertIn(f"Project: ready ({0 if attempt else 5} file changes)", result.stdout)
            self.assertEqual((project / "AGENTS.md").read_text(encoding="utf-8").count("<!-- HIVEMIND:BEGIN -->"), 1)

    @unittest.skipUnless(os.name == "nt", "CMD quick start")
    def test_documented_one_liner_handles_fresh_and_existing_install(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        command = re.search(r"### 1 · Get HiveMind.*?```cmd\n([^\n]+)", readme, re.S).group(1)
        with tempfile.TemporaryDirectory(prefix="Hive onboarding ") as tmp:
            home = Path(tmp) / "Home with spaces"
            bin_dir = Path(tmp) / "fake-bin"
            home.mkdir()
            bin_dir.mkdir()
            (bin_dir / "launcher.cmd").write_bytes(b"@echo off\r\necho ONBOARDING-OK\r\n")
            (bin_dir / "git.cmd").write_bytes(
                b'@echo off\r\nmd "%USERPROFILE%\\HiveMind" >nul 2>nul\r\ncopy /y "%~dp0launcher.cmd" "%USERPROFILE%\\HiveMind\\hivemind.cmd" >nul\r\n')
            runner = Path(tmp) / "run.cmd"
            runner.write_text("@echo off\n" + command + "\n", encoding="utf-8")
            env = dict(os.environ, USERPROFILE=str(home), PATH=str(bin_dir) + os.pathsep + os.environ["PATH"])
            for _ in range(2):
                result = subprocess.run(["cmd.exe", "/d", "/c", str(runner)], env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr + f" files={list(Path(tmp).rglob('*'))}")
                self.assertTrue((home / "HiveMind/hivemind.cmd").is_file())
                self.assertIn("ONBOARDING-OK", result.stdout)

    def test_agent_registration_continues_after_one_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            commands = []

            def run(args, **kwargs):
                commands.append(args)
                if args[:4] == ["codex-exe", "mcp", "add", "hivemind"]:
                    return subprocess.CompletedProcess(args, 1, "", "Codex refused the config")
                return subprocess.CompletedProcess(args, 0, "hivemind configured", "")

            with patch("scripts.register_agents.shutil.which", side_effect=lambda name: name + "-exe"), \
                    patch("scripts.register_agents.config_locations", return_value={name: [] for name in ("codex", "grok", "antigravity")}), \
                    patch("scripts.register_agents.subprocess.run", side_effect=run):
                results = register_agents(tmp, sys.executable)
            self.assertEqual(results["codex"]["status"], "needs-action")
            self.assertEqual(results["grok"]["status"], "ready")
            self.assertEqual(results["antigravity"]["status"], "ready")
            self.assertEqual(sum(command[1:3] == ["mcp", "add"] for command in commands), 3)

    def test_missing_clients_are_reported_without_calls(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch("scripts.register_agents.shutil.which", return_value=None), \
                patch("scripts.register_agents.subprocess.run") as run:
            results = register_agents(tmp)
            self.assertTrue(all(item["status"] == "skipped" for item in results.values()))
            run.assert_not_called()

    def test_optional_profile_records_only_explicit_answers_and_preserves_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = iter(["Be concise and direct", ""])
            saved = personalize(tmp, lambda _: next(answers), io.StringIO())
            self.assertEqual(saved, ["communication-style"])
            hive = Hive(tmp)
            path = "01-Memory/User/Learned/communication-style.md"
            first = hive.read_note(path)
            self.assertIn("Be concise and direct", first["text"])
            self.assertIn("Basis: user-stated", first["text"])
            personalize(tmp, lambda _: "", io.StringIO())
            self.assertEqual(hive.read_note(path)["revision"], first["revision"])


class SmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_exercises_real_stdio_bridge_without_model(self):
        count = await smoke_test(ROOT, sys.executable, "hivemind")
        self.assertGreaterEqual(count, 3)


if __name__ == "__main__":
    unittest.main()
