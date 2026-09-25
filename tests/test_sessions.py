from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from hivemind.session_runner import run_session
from hivemind.store import Hive
from hivemind.transport import Local
from scripts.package import build_bundle


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hive = Hive(self.tmp.name)
        self.session = self.hive.session_start('app', 'codex', 'Fix login')['session']

    def test_restart_revision_conflicts_and_idempotent_retry(self):
        packet = {'summary':'Fixed login.', 'completed':['Updated validation'],
                  'verification':['Login regression passed'], 'next_steps':['Review'], 'status':'completed'}
        first = self.hive.session_checkpoint(self.session['id'], packet, 0)
        self.assertTrue(first['saved'])
        self.assertTrue(first['markdown_saved'])
        again = Hive(self.tmp.name).session_checkpoint(self.session['id'], packet, 0)
        self.assertEqual(again['session']['revision'], 1)
        self.assertEqual(Hive(self.tmp.name).session_resume('app')['session']['checkpoint']['next_steps'], ['Review'])
        with self.assertRaisesRegex(ValueError, 'Checkpoint changed'):
            self.hive.session_checkpoint(self.session['id'], {'summary':'Overwrite'}, 0)
        with self.assertRaisesRegex(ValueError, 'verification'):
            self.hive.session_checkpoint(self.session['id'], {'summary':'Done', 'status':'completed'}, 1)
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.hive.session_resume('other', self.session['id'])
        self.assertIsNone(self.hive.session_resume('other')['session'])

    def test_concurrent_writers_have_one_winner_and_history_survives_backup(self):
        def save(n):
            try:
                return self.hive.session_checkpoint(self.session['id'], {'summary':f'Milestone {n}'}, 0)['saved']
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(save, range(4))), 1)
        archive = Path(self.tmp.name) / 'backup.zip'
        build_bundle(self.hive.root, archive, include_state=True)
        restored = Path(self.tmp.name) / 'restored'
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(restored)
        hive = Hive(restored / 'HiveMind')
        self.assertEqual(hive.session_resume('app')['session']['revision'], 1)
        with hive.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM checkpoints').fetchone()[0], 2)

    def test_database_survives_failed_markdown_export(self):
        with patch('hivemind.store.atomic_write', side_effect=OSError('disk unavailable')):
            saved = self.hive.session_checkpoint(self.session['id'], {'summary':'Durable milestone.'}, 0)
        self.assertTrue(saved['saved'])
        self.assertFalse(saved['markdown_saved'])
        self.assertEqual(Hive(self.tmp.name).session_resume('app')['session']['checkpoint']['summary'], 'Durable milestone.')
        retry = self.hive.session_checkpoint(self.session['id'], {'summary':'Durable milestone.'}, 0)
        self.assertTrue(retry['markdown_saved'])


class WrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrapper_preserves_agent_checkpoint_and_reports_failed_exit_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {'projects':{'app':tmp}, 'agents':{'codex':sys.executable}}
            (root / 'hive.local.json').write_text(json.dumps(config))
            fixture = root / 'fixture.py'
            fixture.write_text('''import os, sys
sys.path.insert(0, sys.argv[1])
from hivemind.store import Hive
h = Hive(sys.argv[2])
s = h.session_resume('app', os.environ['HIVE_SESSION_ID'])['session']
h.session_checkpoint(s['id'], {'summary':'Agent report preserved.', 'status':'completed',
    'completed':['Reviewed source'], 'verification':['Fixture checked'], 'next_steps':['User review']}, s['revision'])
''')
            api = Local(root)
            source = str(Path(__file__).resolve().parents[1])
            result = await run_session(root, api, 'app', 'codex', [str(fixture), source, tmp], config)
            self.assertTrue(result['checkpoint_saved'])
            packet = api.hive.session_resume('app')['session']
            self.assertEqual(packet['revision'], 2)
            self.assertEqual(packet['status'], 'completed')
            self.assertEqual(packet['checkpoint']['verification'], ['Fixture checked'])
            with patch.object(api.hive, 'session_checkpoint', side_effect=OSError('fixture save failure')):
                failed = await run_session(root, api, 'app', 'codex', ['-c','pass'], config)
            self.assertFalse(failed['checkpoint_saved'])
            recovery = root / 'runtime/session-recovery' / (failed['session'] + '.json')
            self.assertEqual(json.loads(recovery.read_text())['checkpoint']['status'], 'needs_handoff')

    async def test_successful_exit_without_report_is_not_completed_and_git_changes_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / 'repo'
            repo.mkdir()
            subprocess.run(['git','init',str(repo)], check=True, capture_output=True)
            file = repo / 'file with spaces.txt'
            file.write_text('before')
            subprocess.run(['git','-C',str(repo),'add','.'], check=True, capture_output=True)
            subprocess.run(['git','-C',str(repo),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','Fixture'], check=True, capture_output=True)
            config = {'offline':True, 'projects':{'app':str(repo)}, 'agents':{'codex':sys.executable}}
            (root / 'hive.local.json').write_text(json.dumps(config))
            fixture = root / 'fixture.py'
            fixture.write_text('from pathlib import Path\nimport os\nassert os.environ["HIVE_SESSION_ID"].startswith("SESSION-")\nPath("file with spaces.txt").write_text("after")\n')
            api = Local(root)
            result = await run_session(root, api, 'app', 'codex', [str(fixture)], config)
            self.assertEqual(result['exit_code'], 0)
            resumed = api.hive.session_resume('app')['session']
            self.assertEqual(resumed['status'], 'needs_handoff')
            self.assertIn('file with spaces.txt', resumed['git']['observed_files'])
            self.assertEqual(resumed['checkpoint']['verification'], [])

    async def test_failed_process_records_interruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {'projects':{'app':tmp}, 'agents':{'codex':sys.executable}}
            (root / 'hive.local.json').write_text(json.dumps(config))
            api = Local(root)
            result = await run_session(root, api, 'app', 'codex', ['-c','raise SystemExit(7)'], config)
            self.assertEqual(result['exit_code'], 7)
            self.assertEqual(api.hive.session_resume('app')['session']['status'], 'interrupted')


if __name__ == '__main__':
    unittest.main()
