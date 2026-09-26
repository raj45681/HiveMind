import asyncio
import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from hivemind.server import server_token
from hivemind.transport import backend

ROOT = Path(__file__).resolve().parents[1]


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_clients_share_notes_tasks_and_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "hive.py"), "--root", tmp, "serve"])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    names = {t.name for t in (await client.list_tools()).tools}
                    self.assertEqual(len(names), 16)
                    result = await client.call_tool("memory_write", {"path": "01-Memory/shared.md", "content": "# Shared\nMCP interoperability verified"})
                    self.assertFalse(result.isError)
                    first_revision = json.loads(result.content[0].text)["revision"]
                    updated = await client.call_tool("memory_write", {"path": "01-Memory/shared.md",
                        "content": "# Shared\nMCP interoperability verified again", "expected_revision": first_revision})
                    self.assertFalse(updated.isError)
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
