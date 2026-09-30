import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from hivemind import code_index as code
from hivemind.server import build_server

ROOT = Path(__file__).resolve().parents[1]


class CodeIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Hive code ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Hive home"
        self.project = Path(self.temp.name) / "My project"
        self.root.mkdir()
        self.project.mkdir()
        self.config = {"offline": True, "projects": {"app": str(self.project)}, "graphify_projects": ["app"]}
        self.write_config()
        (self.project / "app.py").write_text("def login():\n    return True\n")

    def write_config(self):
        (self.root / "hive.local.json").write_text(json.dumps(self.config))

    def adapter(self, root, payload):
        if payload["operation"] == "build":
            source = Path(payload["source"])
            self.assertEqual(sorted(p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()),
                             sorted(payload["files"]))
            return {"graph": {"nodes": [], "links": []}, "nodes": len(payload["files"]), "edges": 1}
        return {"lines": [f"NODE 登录{i} [src=app.py loc=L1]" for i in range(80)], "matched": True}

    def test_refresh_add_edit_delete_and_bounded_unicode_replies(self):
        with patch.object(code, "installed", return_value=True), patch.object(code, "run_adapter", side_effect=self.adapter) as adapter:
            first = code.operate(self.root, "app", "login", 256)
            self.assertEqual(first["status"], "ready")
            self.assertTrue(first["refreshed"])
            self.assertGreater(first["omitted_lines"], 0)
            self.assertLessEqual(len(code.compact(first).encode("utf-8")), 1024)
            self.assertFalse(code.operate(self.root, "app", "login")["refreshed"])
            (self.project / "other.py").write_text("def other(): pass\n")
            self.assertEqual(code.operate(self.root, "app", "other")["files"], 2)
            (self.project / "app.py").write_text("def logout(): pass\n")
            self.assertTrue(code.operate(self.root, "app", "logout")["refreshed"])
            (self.project / "other.py").unlink()
            self.assertEqual(code.operate(self.root, "app", "logout")["files"], 1)
            builds = [c for c in adapter.call_args_list if c.args[1]["operation"] == "build"]
            self.assertEqual(len(builds), 4)

    def test_git_ignored_private_and_large_files_excluded(self):
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        (self.project / ".gitignore").write_text("ignored.py\n")
        (self.project / "ignored.py").write_text("secret = 'do not index'\n")
        for folder in ("vault", "runtime", "node_modules", ".hidden"):
            (self.project / folder).mkdir()
            (self.project / folder / "secret.py").write_text("private = True\n")
        (self.project / "private.md").write_text("Personal preferences")
        (self.project / "settings.json").write_text('{"token":"secret"}')
        (self.project / "large.py").write_bytes(b"x" * (code.MAX_FILE + 1))
        files, _, skipped, mode = code.sources(self.root, self.project)
        self.assertEqual(list(files), ["app.py"])
        self.assertEqual(skipped, 1)
        self.assertEqual(mode, "git-visible source files")

    def test_symlink_source_is_not_followed(self):
        outside = self.root / "private.py"
        outside.write_text("secret = True\n")
        try:
            (self.project / "linked.py").symlink_to(outside)
        except OSError:
            self.skipTest("Symlinks require OS permission")
        self.assertNotIn("linked.py", code.sources(self.root, self.project)[0])

    def test_equivalent_project_root_keeps_sources_and_fingerprint(self):
        canonical = code.sources(self.root, self.project.resolve())
        alias = self.project / '..' / self.project.name
        selected = code.sources(self.root, alias)
        self.assertEqual(list(selected[0]), ['app.py'])
        self.assertEqual(selected[:2], canonical[:2])

    def test_missing_disabled_empty_invalid_and_limits_fall_back(self):
        with patch.object(code, "installed", return_value=False):
            self.assertEqual(code.operate(self.root, "app", "login")["status"], "unavailable")
        self.config["graphify_projects"] = []
        self.write_config()
        self.assertEqual(code.operate(self.root, "app", "login")["status"], "disabled")
        self.assertEqual(code.operate(self.root, "app", "login", 1)["status"], "invalid")
        self.config["graphify_projects"] = ["app"]
        self.write_config()
        with patch.object(code, "installed", return_value=True), patch.object(code, "MAX_TOTAL", 1):
            self.assertEqual(code.operate(self.root, "app", "login")["status"], "unavailable")
        (self.project / "app.py").unlink()
        with patch.object(code, "installed", return_value=True):
            self.assertEqual(code.operate(self.root, "app", "login")["status"], "empty")

    def test_corrupt_cache_rebuild_and_failed_refresh_never_returns_stale(self):
        with patch.object(code, "installed", return_value=True), patch.object(code, "run_adapter", side_effect=self.adapter):
            code.operate(self.root, "app", "login")
            index = next((self.root / "runtime/code-index").glob("*/index.json"))
            index.write_text("broken")
            self.assertTrue(code.operate(self.root, "app", "login")["refreshed"])
            original = index.read_bytes()
            (self.project / "app.py").write_text("def changed(): pass\n")
            with patch.object(code, "run_adapter", side_effect=subprocess.TimeoutExpired("fixture", 120)):
                result = code.operate(self.root, "app", "changed")
                self.assertEqual(result["status"], "unavailable")
                self.assertNotIn("text", result)
            self.assertEqual(index.read_bytes(), original)
            self.assertTrue(code.operate(self.root, "app", "changed")["refreshed"])

    def test_lock_contention_returns_fallback_and_recovers(self):
        with patch.object(code, "installed", return_value=True), patch.object(code, "run_adapter", side_effect=self.adapter):
            code.operate(self.root, "app", "login")
            lock = next((self.root / "runtime/code-index").glob("*/index.lock"))
            with code.locked(lock):
                self.assertEqual(code.operate(self.root, "app", "login")["status"], "unavailable")
            self.assertEqual(code.operate(self.root, "app", "login")["status"], "ready")


class CodeMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_tool_discovery_and_missing_dependency_over_stdio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = {t.name for t in await build_server(root).list_tools()}
            self.assertNotIn("code_query", names)
            (root / "hive.local.json").write_text(json.dumps({"offline": True,
                "graphify_projects": ["app"], "projects": {"app": str(root)}}))
            params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "hive.py"), "--root", tmp, "serve"])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = {t.name: t for t in (await client.list_tools()).tools}
                    names = set(tools)
                    self.assertEqual(len(names), 23)
                    self.assertIn("code_query", names)
                    self.assertFalse(tools["code_query"].annotations.readOnlyHint)
                    self.assertFalse(tools["code_query"].annotations.destructiveHint)
                    self.assertFalse(tools["code_query"].annotations.idempotentHint)
                    self.assertFalse(tools["code_query"].annotations.openWorldHint)
                    result = await client.call_tool("code_query", {"project": "app", "query": "login"})
                    self.assertFalse(result.isError)
                    self.assertEqual(json.loads(result.content[0].text)["status"], "unavailable")
                    memory = await client.call_tool("memory_search", {"query": "login"})
                    self.assertFalse(memory.isError)


@unittest.skipUnless(code.interpreter(ROOT).exists(), "Optional installed Graphify required")
class RealGraphifyTests(unittest.TestCase):
    setUp = CodeIndexTests.setUp
    write_config = CodeIndexTests.write_config
    # Only this test uses the optional package; unit tests above remain offline
    # and install-free on a fresh clone. No paid agent is invoked.
    def test_real_unicode_source_relations_refresh_and_no_network(self):
        (self.project / "auth.py").write_text("def authenticate():\n    return '你好'\n", encoding="utf-8")
        (self.project / "app.py").write_text("from auth import authenticate\ndef login():\n    return authenticate()\ndef 登录():\n    return login()\n", encoding="utf-8")
        (self.project / "greet.ts").write_text("export function greet(name: string) { return name; }\n")
        with patch.object(code, "interpreter", return_value=code.interpreter(ROOT)):
            result = code.operate(self.root, "app", "login authenticate", 1000)
            self.assertEqual(result["status"], "ready", result)
            self.assertIn("app.py", result["text"])
            self.assertIn("auth.py", result["text"])
            self.assertIn("EDGE ", result["text"])
            self.assertLessEqual(len(code.compact(result).encode("utf-8")), 4000)
            self.assertFalse(code.operate(self.root, "app", "login")["refreshed"])
            self.assertIn("登录", code.operate(self.root, "app", "登录")["text"])
            self.assertIn("greet.ts", code.operate(self.root, "app", "greet")["text"])
            (self.project / "auth.py").write_text("def authenticate():\n    return False\n")
            self.assertTrue(code.operate(self.root, "app", "authenticate")["refreshed"])

    def test_real_mcp_query_while_client_keeps_stdin_open(self):
        # A venv subprocess inheriting MCP stdin can hang on Windows. Exercise
        # the actual parser behind a live MCP server, not only mocked failures.
        server = ("import sys; from pathlib import Path; import hivemind.code_index as c; "
                  "from hivemind.server import build_server; "
                  "c.interpreter=lambda root: Path(sys.argv[2]); "
                  "build_server(Path(sys.argv[1])).run(transport='stdio')")
        async def check():
            params = StdioServerParameters(command=sys.executable, args=["-c", server, str(self.root), str(code.interpreter(ROOT))], cwd=str(ROOT))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    for budget in (256, 1000):
                        response = await client.call_tool("code_query", {"project": "app", "query": "login", "budget_tokens": budget})
                        self.assertFalse(response.isError)
                        self.assertIsNone(response.structuredContent)  # avoid duplicating the bounded text payload
                        raw = response.content[0].text
                        self.assertEqual(json.loads(raw)["status"], "ready", raw)
                        self.assertLessEqual(len(raw.encode("utf-8")), 4 * budget)
                        self.assertIn("app.py", raw)
        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
