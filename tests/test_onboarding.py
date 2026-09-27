import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from bootstrap import ensure_bridge_dependencies
from hivemind.dependencies import local_dependency_status
from hivemind.onboarding import choose_setup, personalize, setup_extras, smoke_test
from hivemind.store import Hive
from scripts.register_agents import register_agents


ROOT = Path(__file__).resolve().parents[1]


class OnboardingTests(unittest.TestCase):
    def test_bridge_reconciles_pin_even_when_imports_work(self):
        mismatch = {"ok": False, "mismatched": {"mcp": {"required": "1.30.0", "installed": "1.29.0"}}}
        matching = {"ok": True, "mismatched": {}}
        with patch("bootstrap.bridge_dependencies", side_effect=[mismatch, matching]), \
                patch("bootstrap.subprocess.run", side_effect=[
                    subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0),
                    subprocess.CompletedProcess([], 0)]) as run:
            self.assertTrue(ensure_bridge_dependencies(Path("python"))["ok"])
            self.assertIn("pip", run.call_args_list[1].args[0])
        status = local_dependency_status(ROOT / "requirements.txt")
        self.assertIn("mcp", status["pinned"])

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
                                        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("MCP bridge: ready", result.stdout)
                self.assertIn(f"Project: ready ({0 if attempt else 5} file changes)", result.stdout)
            self.assertEqual((project / "AGENTS.md").read_text(encoding="utf-8").count("<!-- HIVEMIND:BEGIN -->"), 1)
            config = json.loads((bundle / "hive.local.json").read_text(encoding="utf-8"))
            self.assertEqual(config["onboarding_defaults"], {"graphify": False, "semantic": False})
            generic = subprocess.run([sys.executable, str(bundle / "bootstrap.py"), str(project),
                                      "--other-client", "--tool-profile", "memory"],
                                     stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
            self.assertEqual(generic.returncode, 0, generic.stdout + generic.stderr)
            self.assertIn("Other MCP client: configure a stdio server", generic.stdout)
            self.assertIn('"--profile", "memory"', generic.stdout)
            self.assertEqual(json.loads((bundle / "hive.local.json").read_text())["tool_profile"], "memory")

    def test_first_run_menu_offers_core_semantic_graphify_both_and_all(self):
        args = SimpleNamespace(with_graphify=False, with_semantic=False, personalize=False,
                               configure=False, no_prompt=False, dry_run=False)
        expected = {
            "0": (False, False, False),
            "1": (False, True, False),
            "2": (True, False, False),
            "3": (True, True, False),
            "4": (True, True, True),
        }
        for choice, (graphify, semantic, profile) in expected.items():
            with self.subTest(choice=choice):
                output = io.StringIO()
                result = choose_setup(args, {}, True, interactive=True,
                                      input_fn=lambda _: choice, output=output)
                self.assertEqual((result["graphify"], result["semantic"], result["personalize"]),
                                 (graphify, semantic, profile))
                self.assertTrue(result["persist"])
                self.assertIn("All of the above", output.getvalue())
        generic = choose_setup(args, {}, True, interactive=True,
                               input_fn=lambda _: "5", output=io.StringIO())
        self.assertTrue(generic["other_client"])
        self.assertFalse(generic["semantic"] or generic["graphify"])

    def test_saved_defaults_and_reconfigure_without_surprise_prompts(self):
        args = SimpleNamespace(with_graphify=False, with_semantic=False, personalize=False,
                               configure=False, no_prompt=False, dry_run=False)
        saved = {"onboarding_defaults": {"graphify": True, "semantic": True}}
        result = choose_setup(args, saved, False, interactive=True,
                              input_fn=lambda _: self.fail("Rerun should not prompt"))
        self.assertTrue(result["graphify"] and result["semantic"])
        self.assertFalse(result["menu_shown"])
        args.configure = True
        changed = choose_setup(args, saved, False, interactive=True,
                               input_fn=lambda _: "0", output=io.StringIO())
        self.assertFalse(changed["graphify"] or changed["semantic"])
        self.assertTrue(changed["persist"])
        with self.assertRaisesRegex(ValueError, "interactive terminal"):
            choose_setup(args, saved, False, interactive=False)

    def test_noninteractive_first_run_and_explicit_flags(self):
        args = SimpleNamespace(with_graphify=False, with_semantic=False, personalize=False,
                               configure=False, no_prompt=False, dry_run=False)
        result = choose_setup(args, {}, True, interactive=False,
                              input_fn=lambda _: self.fail("Unattended setup should not prompt"))
        self.assertFalse(result["graphify"] or result["semantic"])
        args.with_semantic = True
        selected = choose_setup(args, {}, True, interactive=True,
                                input_fn=lambda _: self.fail("Explicit flags should not prompt"))
        self.assertTrue(selected["semantic"])
        self.assertFalse(selected["graphify"])

    def test_optional_features_report_graphify_fallback_even_with_zero_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hive.local.json").write_text('{"offline":true}', encoding="utf-8")
            results = iter([
                subprocess.CompletedProcess([], 0, '{"installed":true}', ""),
                subprocess.CompletedProcess([], 0, '{"status":"unavailable","reason":"Graphify dependency unavailable"}', ""),
            ])
            with patch("hivemind.semantic.ready", return_value=False), \
                    patch("hivemind.onboarding.subprocess.run", side_effect=lambda *a, **k: next(results)):
                extras = setup_extras(root, sys.executable, "app", {"semantic": True, "graphify": True})
            self.assertEqual(extras["Semantic"]["status"], "ready")
            self.assertEqual(extras["Graphify"]["status"], "needs-action")
            self.assertIn("dependency unavailable", extras["Graphify"]["detail"])

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
            def closed_input(_):
                raise EOFError
            personalize(tmp, closed_input, io.StringIO())
            self.assertEqual(hive.read_note(path)["revision"], first["revision"])


class SmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_exercises_real_stdio_bridge_without_model(self):
        count = await smoke_test(ROOT, sys.executable, "hivemind")
        self.assertGreaterEqual(count, 3)


if __name__ == "__main__":
    unittest.main()
