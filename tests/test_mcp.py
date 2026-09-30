import asyncio
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from hivemind.server import build_server, server_token
from hivemind.transport import Local, backend

ROOT = Path(__file__).resolve().parents[1]


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_profile_exposes_memory_workflows_without_coordination_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            full = await build_server(tmp).list_tools()
            memory = await build_server(tmp, tool_profile="memory").list_tools()
            self.assertEqual({tool.name for tool in memory}, {
                "hive_context", "memory_search", "note_read", "memory_write", "memory_learn",
                "session_start", "session_checkpoint", "session_resume",
                "memory_relate", "memory_consolidate", "learning_review"})
            self.assertEqual(len(full), 22)
            full_bytes = len(json.dumps([tool.model_dump(mode="json") for tool in full]))
            memory_bytes = len(json.dumps([tool.model_dump(mode="json") for tool in memory]))
            self.assertLess(memory_bytes, full_bytes * .65)
            with self.assertRaises(ValueError):
                build_server(tmp, tool_profile="unknown")

    async def test_proxy_and_legacy_cloud_advertise_conservative_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            forwarded = {tool.name: tool for tool in await build_server(
                tmp, remote_url="https://example.invalid/mcp").list_tools()}
            for name in ("session_checkpoint", "memory_write", "memory_learn", "task_finish"):
                self.assertFalse(forwarded[name].annotations.idempotentHint)

            (Path(tmp) / "hive.local.json").write_text(json.dumps({
                "memory_url": "https://example.invalid"}), encoding="utf-8")
            cloud = {tool.name: tool for tool in await build_server(tmp).list_tools()}
            self.assertFalse(cloud["memory_write"].annotations.idempotentHint)
            self.assertFalse(cloud["memory_learn"].annotations.idempotentHint)
            self.assertTrue(cloud["session_checkpoint"].annotations.idempotentHint)
            self.assertTrue(cloud["task_finish"].annotations.idempotentHint)

    async def test_git_backed_session_does_not_hold_stdio_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, repo = Path(tmp) / "authority", Path(tmp) / "repo"
            root.mkdir()
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True, stdin=subprocess.DEVNULL)
            (repo / "app.txt").write_text("baseline", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "app.txt"], check=True, stdin=subprocess.DEVNULL)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=Hive Test",
                            "-c", "user.email=test@example.invalid", "commit", "-qm", "baseline"],
                           check=True, stdin=subprocess.DEVNULL)
            (root / "hive.local.json").write_text(json.dumps({"offline": True,
                "projects": {"app": str(repo)}}), encoding="utf-8")
            params = StdioServerParameters(command=sys.executable,
                args=[str(ROOT / "hive.py"), "--root", str(root), "serve"])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    started = await asyncio.wait_for(client.call_tool("session_start",
                        {"project": "app", "agent": "codex", "goal": "Check stdio Git snapshot"}), 20)
                    self.assertFalse(started.isError)
                    session = json.loads(started.content[0].text)["session"]
                    self.assertTrue(session["git"]["available"])
                    brief = await asyncio.wait_for(client.call_tool("hive_context",
                        {"project": "app", "budget_tokens": 512}), 20)
                    self.assertFalse(brief.isError)
                    self.assertEqual(json.loads(brief.content[0].text)["session"]["git_drift"], "match")
                    saved = await asyncio.wait_for(client.call_tool("session_checkpoint", {
                        "ident": session["id"], "expected_revision": 0,
                        "checkpoint": {"summary": "Stdio Git checkpoint saved", "status": "active"}}), 20)
                    self.assertFalse(saved.isError)
                    followup = await asyncio.wait_for(client.call_tool("task_list", {"limit": 1}), 5)
                    self.assertFalse(followup.isError)

    async def test_stdio_clients_share_notes_tasks_and_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "hive.py"), "--root", tmp, "serve"])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = {t.name: t for t in (await client.list_tools()).tools}
                    read_only = {"hive_context", "memory_search", "session_resume", "note_read",
                                 "task_get", "task_list", "message_inbox", "goal_status"}
                    additive = {"session_start", "task_create", "message_send", "memory_consolidate", "goal_create"}
                    mutating = {"session_checkpoint", "memory_write", "memory_learn",
                                "task_claim", "task_heartbeat", "task_finish", "memory_relate", "learning_review", "goal_control"}
                    idempotent = {"session_checkpoint", "memory_write", "memory_learn", "task_finish"}
                    self.assertEqual(set(tools), read_only | additive | mutating)
                    for name, tool in tools.items():
                        with self.subTest(tool=name):
                            self.assertIsNotNone(tool.annotations)
                            self.assertFalse(tool.annotations.openWorldHint)
                            self.assertEqual(tool.annotations.readOnlyHint, name in read_only)
                            if name in additive | mutating:
                                self.assertEqual(tool.annotations.destructiveHint, name in mutating)
                                self.assertEqual(tool.annotations.idempotentHint, name in idempotent)
                            else:
                                self.assertIsNone(tool.annotations.idempotentHint)
                    result = await client.call_tool("memory_write", {"path": "01-Memory/shared.md", "content": "# Shared\nMCP interoperability verified"})
                    self.assertFalse(result.isError)
                    first_revision = json.loads(result.content[0].text)["revision"]
                    updated = await client.call_tool("memory_write", {"path": "01-Memory/shared.md",
                        "content": "# Shared\nMCP interoperability verified again", "expected_revision": first_revision})
                    self.assertFalse(updated.isError)
                    procedure_args = {
                        "kind": "procedure", "key": "repair-login-cache", "summary": "Repair stale login cache",
                        "source": "verified test", "project": "app", "basis": "verified-result",
                        "trigger": "Login cache serves an old token", "steps": ["Clear the expired token", "Retry login"],
                        "evidence": "Login integration test passed"}
                    procedure = await client.call_tool("memory_learn", procedure_args)
                    self.assertFalse(procedure.isError)
                    procedure_path = json.loads(procedure.content[0].text)["path"]
                    self.assertEqual(procedure_path, "03-Projects/app/Procedures/repair-login-cache.md")
                    before_retry = await client.call_tool("note_read", {"path": procedure_path})
                    await client.call_tool("memory_learn", procedure_args)
                    after_retry = await client.call_tool("note_read", {"path": procedure_path})
                    self.assertEqual(json.loads(after_retry.content[0].text)["revision"],
                                     json.loads(before_retry.content[0].text)["revision"])
                    found = await client.call_tool("memory_search", {"query": "expired token", "project": "app"})
                    self.assertIn(procedure_path, found.content[0].text)
                    await client.call_tool("message_send", {"sender": "codex", "recipient": "grok", "body": "Read shared memory"})
                    started = await client.call_tool('session_start', {'project':'app', 'agent':'codex', 'goal':'Fix login'})
                    self.assertFalse(started.isError)
                    ident = json.loads(started.content[0].text)['session']['id']
                    saved = await client.call_tool('session_checkpoint', {'ident':ident, 'expected_revision':0,
                        'checkpoint':{'summary':'Login fixed.', 'status':'completed', 'completed':['Updated login'],
                                      'verification':['Login test passed'], 'next_steps':['Review change']}})
                    self.assertFalse(saved.isError)
            # A separate process sees the same persistent state, with no shared chat context.
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    result = await client.call_tool("memory_search", {"query": "interoperability"})
                    self.assertIn("shared.md", result.content[0].text)
                    historical = await client.call_tool("note_read", {"path": "01-Memory/shared.md",
                        "revision": first_revision, "include_history": True})
                    self.assertFalse(historical.isError)
                    self.assertIn("interoperability verified", historical.content[0].text)
                    self.assertIn("history", historical.content[0].text)
                    handoffs = await client.call_tool("memory_search", {"query": "Login fixed",
                        "project": "app", "include_handoffs": True})
                    self.assertFalse(handoffs.isError)
                    self.assertIn("checkpoint", handoffs.content[0].text)
                    result = await client.call_tool("message_inbox", {"recipient": "grok"})
                    self.assertIn("Read shared memory", result.content[0].text)
                    resumed = await client.call_tool('session_resume', {'project':'app'})
                    self.assertEqual(json.loads(resumed.content[0].text)['session']['id'], ident)
                    brief = await client.call_tool('hive_context', {'project':'app', 'budget_tokens':512})
                    self.assertFalse(brief.isError)
                    self.assertLessEqual(len(brief.content[0].text.encode('utf-8')), 2048)
                    self.assertIn('Login fixed.', brief.content[0].text)
                    result = await client.call_tool("note_read", {"path": "../outside.md"})
                    self.assertTrue(result.isError)

    async def test_http_auth_remote_client_and_stdio_bridge(self):
        with tempfile.TemporaryDirectory() as authority, tempfile.TemporaryDirectory() as device:
            token = server_token(authority)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            proc = subprocess.Popen([sys.executable, str(ROOT / "hive.py"), "--root", authority, "serve", "--http", "--port", str(port)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            url = f"http://127.0.0.1:{port}/mcp"
            try:
                async with httpx.AsyncClient() as http:
                    for attempt in range(60):
                        try:
                            response = await http.get(url)
                            break
                        except httpx.ConnectError:
                            await asyncio.sleep(.1)
                    else:
                        self.fail("HTTP server did not start")
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual((await http.get(url, headers={"Authorization": "Bearer incorrect"})).status_code, 401)
                async with backend(device, url, token) as api:
                    await api.call("memory_write", path="01-Memory/network.md", content="# Remote\nOne shared authority")
                    self.assertIn("network.md", json.dumps(await api.call("memory_search", query="authority")))
                # Future devices can reuse the same stdio configuration and forward over HTTP.
                params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "hive.py"), "--root", device, "serve"],
                                               env={"HIVE_URL": url, "HIVE_TOKEN": token})
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        result = await client.call_tool("note_read", {"path": "01-Memory/network.md"})
                        self.assertFalse(result.isError)
                        self.assertIn("One shared authority", result.content[0].text)
                self.assertFalse((Path(device) / "runtime" / "hivemind.db").exists())
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                proc.stderr.close()


class LocalDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_local_operation_does_not_queue_other_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Local(Path(tmp))
            started, release = threading.Event(), threading.Event()

            def slow_context(**_):
                started.set()
                release.wait(3)
                return {"done": True}

            with patch.object(local.hive, "context", side_effect=slow_context):
                began = time.monotonic()
                slow = asyncio.create_task(local.call("hive_context"))
                try:
                    self.assertTrue(await asyncio.wait_for(asyncio.to_thread(started.wait), 1))
                    self.assertEqual(await asyncio.wait_for(local.call("task_list", limit=1), 1), [])
                    self.assertFalse(slow.done())
                    self.assertLess(time.monotonic() - began, 2)
                finally:
                    release.set()
                    await slow
