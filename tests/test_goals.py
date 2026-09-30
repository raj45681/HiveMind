"""Dependency scheduling uses fixture workers only, never paid agent sessions."""
import asyncio
import json
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, AsyncMock

from hivemind.goals import run
from hivemind.store import Hive
from hivemind.transport import Local


class GoalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hive = Hive(self.tmp.name)
        self.config = {'machine': 'fixture', 'agents': {'codex': sys.executable}}

    def node(self, key, deps=None, access='read'):
        return {'key': key, 'title': key, 'objective': 'Exercise scheduler', 'agent': 'codex',
                'access': access, 'acceptance': ['Fixture completes'], 'depends_on': deps or []}

    def goal(self, tasks, **extra):
        return self.hive.goal_create({'key': 'fixture', 'title': 'Fixture goal', 'objective': 'Test a dependency graph',
                                     'project': 'app', 'tasks': tasks, **extra})

    async def worker(self, root, api, ident, config):
        task = await api.call('task_get', ident=ident)
        claim = await api.call('task_claim', worker='fixture', agent=task['agent'], ident=ident, machine='fixture')
        self.assertIsNotNone(claim)
        await asyncio.sleep(.01)
        return await api.call('task_finish', ident=ident, token=claim['claim_token'], result={
            'status': 'done', 'summary': 'Fixture completed', 'verification': ['Fixture assertion passed']})

    async def test_draft_is_repeatable_and_dry_run_does_not_launch_or_create_tasks(self):
        goal = self.goal([self.node('inspect')])
        self.assertEqual(self.goal([self.node('inspect')])['id'], goal['id'])
        with patch('hivemind.worker.run_task', AsyncMock()) as worker:
            preview = await run(self.hive, goal['id'], self.config)
            self.assertFalse(preview['will_launch_model'])
            worker.assert_not_called()
        self.assertEqual(self.hive.list_tasks(), [])
        with self.assertRaises(ValueError):
            await run(self.hive, goal['id'], self.config, execute=True)

    async def test_ready_tasks_execute_in_dependency_order_and_usage_persists(self):
        goal = self.goal([self.node('a'), self.node('b', ['a'])], max_tasks=2)
        active = self.hive.goal_control(goal['id'], 'activate', goal['revision'])
        self.assertEqual([node['status'] for node in active['nodes']], ['ready', 'pending'])
        with patch('hivemind.worker.run_task', side_effect=self.worker):
            result = await run(self.hive, goal['id'], self.config, execute=True)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['usage']['launches'], 2)
        self.assertGreater(result['usage']['seconds'], 0)
        exhausted = await run(self.hive, goal['id'], self.config, execute=True)
        self.assertEqual(exhausted['stopped'], 'execution_budget_exhausted')

    async def test_write_dependency_requires_explicit_integration_even_for_direct_claims(self):
        goal = self.goal([self.node('write', access='write'), self.node('review', ['write'])])
        active = self.hive.goal_control(goal['id'], 'activate', 0)
        first, second = active['nodes']
        claim = self.hive.claim('fixture', 'codex', first['task'])
        self.hive.finish(first['task'], claim['claim_token'], {'status': 'done', 'summary': 'Changed code', 'verification': ['Fixture passed']})
        current = self.hive.goal_status(goal['id'])
        self.assertEqual(current['nodes'][1]['status'], 'awaiting_integration')
        self.assertIsNone(self.hive.claim('fixture', 'codex', second['task']))
        ready = self.hive.goal_control(goal['id'], 'integrate', current['revision'], key='write', source='Fixture change reviewed and integrated')
        self.assertEqual(ready['nodes'][1]['status'], 'ready')

    async def test_failed_dependency_is_not_retried_or_dispatched(self):
        goal = self.goal([self.node('a'), self.node('b', ['a'])])
        active = self.hive.goal_control(goal['id'], 'activate', 0)
        task = active['nodes'][0]['task']
        claim = self.hive.claim('fixture', 'codex', task)
        self.hive.finish(task, claim['claim_token'], {'status': 'failed', 'summary': 'Fixture failed'})
        with patch('hivemind.worker.run_task', AsyncMock()) as worker:
            result = await run(self.hive, goal['id'], self.config, execute=True)
            worker.assert_not_called()
        self.assertEqual(result['nodes'][1]['status'], 'dependency_blocked')

    async def test_terminal_write_waits_for_integration_before_goal_completion(self):
        goal = self.goal([self.node('write', access='write')])
        active = self.hive.goal_control(goal['id'], 'activate', 0)
        await self.worker(self.hive.root, Local(self.hive.root),
                          active['nodes'][0]['task'], self.config)
        current = self.hive.goal_status(goal['id'])
        self.assertEqual(current['status'], 'awaiting_integration')
        completed = self.hive.goal_control(goal['id'], 'integrate', current['revision'],
                                           key='write', source='Fixture write reviewed and integrated')
        self.assertEqual(completed['status'], 'completed')

    async def test_dispatch_error_does_not_retry_a_still_pending_node(self):
        goal = self.goal([self.node('a')], max_tasks=3)
        self.hive.goal_control(goal['id'], 'activate', 0)
        with patch('hivemind.worker.run_task', AsyncMock(side_effect=ValueError('Fixture claim race'))) as worker:
            result = await run(self.hive, goal['id'], self.config, execute=True)
        self.assertEqual(worker.await_count, 1)
        self.assertEqual(result['usage']['launches'], 1)
        self.assertEqual(result['nodes'][0]['status'], 'ready')

    async def test_lost_controller_retains_time_reservation_after_explicit_release(self):
        import subprocess
        goal = self.goal([self.node('a')], max_seconds=30)
        self.hive.goal_control(goal['id'], 'activate', 0)
        code = '''
import asyncio, os, sys
from unittest.mock import patch
from hivemind.goals import run
from hivemind.store import Hive
async def stopped(*args):
    os._exit(0)
with patch('hivemind.worker.run_task', side_effect=stopped):
    asyncio.run(run(Hive(sys.argv[1]), sys.argv[2],
                    {'agents': {'codex': sys.executable}}, execute=True))
'''
        subprocess.run([sys.executable, '-c', code, str(self.hive.root), goal['id']], check=True, timeout=10)
        current = self.hive.goal_status(goal['id'])
        self.assertEqual(current['usage']['seconds'], 30)
        with self.hive.connect(write=True) as c:
            c.execute('UPDATE goals SET runner_until=? WHERE id=?', (time.time() - 1, goal['id']))
        self.hive.goal_control(goal['id'], 'release', current['revision'], source='Fixture child has exited')
        exhausted = await run(self.hive, goal['id'], self.config, execute=True)
        self.assertEqual(exhausted['stopped'], 'execution_budget_exhausted')

    async def test_parallel_and_per_run_task_limits_are_enforced(self):
        goal = self.goal([self.node('a'), self.node('b'), self.node('c')], max_tasks=3, max_parallel=2)
        self.hive.goal_control(goal['id'], 'activate', 0)
        simultaneous, peak = 0, 0
        async def worker(*args):
            nonlocal simultaneous, peak
            simultaneous += 1
            peak = max(peak, simultaneous)
            try:
                return await self.worker(*args)
            finally:
                simultaneous -= 1
        with patch('hivemind.worker.run_task', side_effect=worker):
            result = await run(self.hive, goal['id'], self.config, execute=True, max_tasks=2, max_parallel=4)
        self.assertEqual(peak, 2)
        self.assertEqual(result['launched_this_run'], 2)
        self.assertEqual(sum(node['status'] == 'done' for node in result['nodes']), 2)

    async def test_timeout_cancels_only_the_runner_workers_and_releases_ownership(self):
        goal = self.goal([self.node('a')])
        self.hive.goal_control(goal['id'], 'activate', 0)
        cancelled = asyncio.Event()
        async def blocked(*args):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()
        with patch('hivemind.worker.run_task', side_effect=blocked):
            result = await run(self.hive, goal['id'], self.config, execute=True, max_seconds=1)
        self.assertTrue(cancelled.is_set())
        self.assertIsNone(result['runner'])
        self.assertEqual(result['usage']['launches'], 1)

    async def test_stale_runner_requires_explicit_review_and_invalid_limits_are_rejected(self):
        goal = self.goal([self.node('a')])
        active = self.hive.goal_control(goal['id'], 'activate', 0)
        with self.hive.connect(write=True) as c:
            c.execute('UPDATE goals SET runner=?,runner_until=? WHERE id=?', ('lost-worker', time.time() - 10, goal['id']))
        with self.assertRaises(ValueError):
            await run(self.hive, goal['id'], self.config, execute=True)
        released = self.hive.goal_control(goal['id'], 'release', active['revision'], source='Fixture process confirmed stopped')
        self.assertIsNone(released['runner'])
        with self.assertRaises(ValueError):
            await run(self.hive, goal['id'], self.config, execute=True, max_tasks=-1)

    async def test_cycles_unknown_dependencies_and_duplicate_keys_are_rejected(self):
        for tasks in ([self.node('a', ['a'])], [self.node('a', ['missing'])], [self.node('a'), self.node('a')]):
            with self.assertRaises(ValueError):
                self.goal(tasks)
        self.assertEqual(self.hive.list_tasks(), [])

    async def test_empty_graph_proposes_reviewable_plan_implementation_and_review(self):
        goal = self.goal([])
        self.assertEqual([node['key'] for node in goal['nodes']], ['plan', 'implement', 'review'])
        self.assertEqual(goal['status'], 'draft')
        self.assertIn('n0 --> n1', goal['mermaid'])

    async def test_cli_goal_preview_round_trip_never_executes_agents(self):
        import subprocess
        root = Path(__file__).resolve().parents[1]
        path = Path(self.tmp.name) / 'goal.json'
        path.write_text(json.dumps({'key': 'cli', 'title': 'CLI goal', 'objective': 'Inspect a project', 'project': 'app'}))
        command = [sys.executable, str(root / 'hive.py'), '--root', self.tmp.name]
        created = json.loads(subprocess.run(command + ['goal-create', str(path)], capture_output=True, text=True, check=True).stdout)
        preview = json.loads(subprocess.run(command + ['goal-run', created['id']], capture_output=True, text=True, check=True).stdout)
        self.assertFalse(preview['will_launch_model'])
