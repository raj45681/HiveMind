"""Real stdio MCP round trip across all core memory and planning workflows."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


class CoreMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_memory_task_brief_consolidation_outcomes_and_goal_activation(self):
        with tempfile.TemporaryDirectory() as tmp:
            params = StdioServerParameters(command=sys.executable, args=[str(ROOT / 'hive.py'), '--root', tmp, 'serve'])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    async def call(name, **args):
                        response = await client.call_tool(name, args)
                        self.assertFalse(response.isError, response.content)
                        return json.loads(response.content[0].text)
                    old = await call('memory_learn', kind='decision', key='old-auth', summary='Credential renewal uses parallel refresh.',
                                     source='Synthetic old design', project='app', files=['src/auth.py'])
                    new = await call('memory_learn', kind='decision', key='new-auth', summary='Credential renewal uses a mutex.',
                                     source='Synthetic current design', project='app', files=['src/auth.py'])
                    await call('memory_relate', path=new['path'], related=old['path'], relation='supersedes',
                               expected_revision=new['revision'], related_revision=old['revision'], source='Synthetic review')
                    found = await call('memory_search', query='credential renewal', project='app')
                    self.assertNotIn(old['path'], [hit['path'] for hit in found])
                    self.assertIn(old['path'], next(hit for hit in found if hit['path'] == new['path'])['supersedes'])
                    goal = await call('goal_create', spec={'key': 'core-flow', 'title': 'Inspect authentication',
                                      'objective': 'Synthetic test of coordination', 'project': 'app', 'tasks': [
                                          {'key': 'inspect', 'title': 'Inspect unrelated names', 'objective': 'Inspect only', 'files': ['src/auth.py'], 'agent': 'codex', 'acceptance': ['Report evidence']},
                                          {'key': 'review', 'title': 'Review findings', 'objective': 'Review only', 'agent': 'codex', 'depends_on': ['inspect'], 'acceptance': ['Read prior evidence']} ]})
                    self.assertEqual(goal['status'], 'draft')
                    active = await call('goal_control', ident=goal['id'], action='activate', expected_revision=goal['revision'])
                    first, second = active['nodes']
                    brief = await call('hive_context', project='app', task_id=first['task'], budget_tokens=1000)
                    self.assertIn(new['path'], [note['path'] for note in brief['relevant']])
                    claim = await call('task_claim', worker='fixture', agent='codex', ident=first['task'])
                    completed = await call('task_finish', ident=first['task'], token=claim['claim_token'], result={
                        'status': 'done', 'summary': 'Renewal uses a mutex', 'verification': ['Synthetic evidence inspected']})
                    draft = completed['learning_proposal']
                    accepted = await call('learning_review', path=draft['path'], expected_revision=draft['revision'], accept=True)
                    self.assertTrue(accepted['saved'])
                    ready = await call('goal_status', ident=goal['id'])
                    self.assertEqual(ready['nodes'][1]['status'], 'ready')
                    brief = await call('hive_context', project='app', task_id=second['task'], budget_tokens=1800)
                    self.assertEqual(brief['task']['dependencies'][0]['verification'], ['Synthetic evidence inspected'])
                    sources = []
                    for key in ('first-cache', 'second-cache'):
                        sources.append(await call('memory_learn', kind='solution', key=key, summary='Quartz cache eviction clears expired entries.',
                                                   source='Synthetic cache test', project='app', basis='verified-result', evidence='Synthetic assertion passed'))
                    merged = await call('memory_consolidate', project='app', paths=[note['path'] for note in sources], stage=True)
                    proposal = merged['groups'][0]
                    accepted = await call('learning_review', path=proposal['path'], expected_revision=proposal['revision'], accept=True)
                    found = await call('memory_search', query='quartz cache eviction', project='app')
                    self.assertIn(accepted['target'], [hit['path'] for hit in found])
                    self.assertFalse(set(note['path'] for note in sources) & set(hit['path'] for hit in found))
