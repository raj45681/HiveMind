"""New MCP clients can share memory and ownership without a built-in CLI adapter."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import ValidationError

from hivemind.models import TaskSpec
from hivemind.session_runner import run_session
from hivemind.store import Hive, atomic_write
from hivemind.transport import Local
from hivemind.worker import run_task


ROOT = Path(__file__).resolve().parents[1]


class GenericHarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hive = Hive(self.tmp.name)

    def test_new_harness_shares_context_sessions_and_task_ownership(self):
        atomic_write(self.hive.vault / '00-System/HIVE.md', 'Keep durable context concise.')
        atomic_write(self.hive.vault / '05-Agents/cursor.md', 'Use the project test command.')
        atomic_write(self.hive.vault / '05-Agents/Codex.md', 'Codex-only instructions.')
        brief = self.hive.context(agent='cursor', project='app', budget_tokens=1000)
        self.assertIn('05-Agents/cursor.md', [note['path'] for note in brief['instructions']])
        neutral = self.hive.context(project='app', budget_tokens=1000)
        self.assertNotIn('05-Agents/Codex.md', [note['path'] for note in neutral['instructions']])
        session = self.hive.session_start('app', 'cursor', 'Fix the build')['session']
        self.assertEqual(session['agent'], 'cursor')
        self.assertEqual(self.hive.session_resume('app')['session']['id'], session['id'])

        spec = {'title': 'Review build', 'objective': 'Review the build failure',
                'project': 'app', 'agent': 'cursor', 'acceptance': ['Explain the cause']}
        task = self.hive.create_task(spec)
        claim = self.hive.claim('cursor:worker', 'cursor', task['id'])
        self.assertEqual(claim['agent'], 'cursor')
        self.hive.finish(task['id'], claim['claim_token'],
                         {'status': 'done', 'summary': 'Found the cause', 'verification': ['Reviewed log']})
        self.assertEqual(self.hive.get_task(task['id'])['status'], 'done')

    def test_harness_identity_is_validated(self):
        for bad in ('../private', 'Uppercase', 'a/b', '', 'a' * 65):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.hive.context(agent=bad)
            with self.assertRaises(ValueError):
                self.hive.session_start('app', bad, 'Work')
            with self.assertRaises(ValidationError):
                TaskSpec(title='Review', objective='Review work', project='app',
                         agent=bad, acceptance=['Done'])

    def test_client_info_reports_local_stdio_command_and_workflow(self):
        root = Path(self.tmp.name).resolve()
        project = root / 'example'
        project.mkdir()
        (project / 'AGENTS.md').write_text('# Project instructions\n')
        (root / 'hive.py').write_text('# installed entry point\n')
        (root / 'hive.local.json').write_text(json.dumps({'projects': {'example': str(project)}}))
        result = subprocess.run([sys.executable, str(ROOT / 'hive.py'), '--root', str(root),
                                 'client-info', 'example'], capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
        self.assertEqual(info['project'], 'example')
        self.assertEqual(info['mcp']['transport'], 'stdio')
        self.assertEqual(info['mcp']['args'], [str(root / 'hive.py'), 'serve'])
        self.assertEqual(info['workflow_file'], str(project / 'AGENTS.md'))


class GenericHarnessMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_unadapted_headless_worker_does_not_claim_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api = Local(root)
            task = api.hive.create_task({'title': 'Review', 'objective': 'Review work',
                'project': 'app', 'agent': 'cursor', 'acceptance': ['Find issue']})
            with self.assertRaisesRegex(ValueError, 'No headless CLI adapter'):
                await run_task(root, api, task['id'], {}, dry_run=False)
            self.assertEqual(api.hive.get_task(task['id'])['status'], 'pending')

    async def test_session_wrapper_can_launch_a_configured_generic_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'repo'
            project.mkdir()
            config = {'projects': {'app': str(project)}, 'agents': {'cursor': sys.executable}}
            (root / 'hive.local.json').write_text(json.dumps(config))
            result = await run_session(root, Local(root), 'app', 'cursor', ['-c', 'pass'], config)
            self.assertEqual(result['exit_code'], 0)
            self.assertTrue(result['checkpoint_saved'])
            self.assertEqual(Local(root).hive.session_resume('app')['session']['agent'], 'cursor')

    async def test_stdio_schema_accepts_a_new_harness(self):
        with tempfile.TemporaryDirectory() as tmp:
            params = StdioServerParameters(command=sys.executable,
                args=[str(ROOT / 'hive.py'), '--root', tmp, 'serve'])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                    self.assertIn('session_start', tools)
                    context = await client.call_tool('hive_context',
                        {'project': 'app', 'agent': 'cursor', 'budget_tokens': 512})
                    self.assertFalse(context.isError)
                    started = await client.call_tool('session_start',
                        {'project': 'app', 'agent': 'cursor', 'goal': 'Review build'})
                    self.assertFalse(started.isError)
                    self.assertEqual(json.loads(started.content[0].text)['session']['agent'], 'cursor')
                    task = await client.call_tool('task_create', {'spec': {
                        'title': 'Review build', 'objective': 'Review the build', 'project': 'app',
                        'agent': 'cursor', 'acceptance': ['Explain failure']}})
                    self.assertFalse(task.isError)
                    ident = json.loads(task.content[0].text)['id']
                    claimed = await client.call_tool('task_claim',
                        {'worker': 'cursor:worker', 'agent': 'cursor', 'ident': ident})
                    self.assertFalse(claimed.isError)
                    self.assertEqual(json.loads(claimed.content[0].text)['agent'], 'cursor')


if __name__ == '__main__':
    unittest.main()
