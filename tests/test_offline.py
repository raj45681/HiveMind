import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from hivemind.cloud import cloud_settings
from hivemind.store import Hive
from hivemind.transport import backend, connection
from scripts.package import build_bundle

ROOT = Path(__file__).resolve().parents[1]


class OfflineTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_ignores_config_environment_and_captured_remote_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'hive.local.json').write_text(json.dumps({
                'offline': True, 'memory_url': 'https://old.example',
                'coordinator_url': 'https://old.example/mcp'}))
            with patch.dict(os.environ, {'HIVE_URL': 'https://env.example/mcp',
                                        'HIVE_MEMORY_URL': 'https://env.example', 'HIVE_TOKEN': 'unused'}), \
                    patch('httpx.AsyncClient', side_effect=AssertionError('No HTTP permitted')):
                self.assertEqual(connection(root)[:2], ('', ''))
                self.assertEqual(cloud_settings(root), ('', ''))
                async with backend(root, 'https://captured.example/mcp', 'unused') as first:
                    await first.call('memory_learn', kind='preference', key='local-first',
                                     summary='Keep memory local', source='User request', basis='user-stated')
                    await first.call('message_send', sender='codex', recipient='grok', body='Completed offline setup')
                async with backend(root) as second:
                    context = await second.call('hive_context', project='new-project')
                    self.assertIn('Keep memory local', json.dumps(context))
                    self.assertIn('Completed offline setup', json.dumps(await second.call('message_inbox', recipient='grok')))
            self.assertFalse((root / 'runtime/cloud-sync.db').exists())

    async def test_explicit_reconnect_validates_remote_even_when_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'hive.local.json').write_text('{"offline":true}')
            with self.assertRaisesRegex(ValueError, 'HTTPS'):
                async with backend(tmp, 'http://remote.example/mcp', 'token', respect_offline=False):
                    self.fail('Invalid remote must not succeed as a local connection')


class BundleTests(unittest.TestCase):
    def test_restore_notes_tasks_messages_without_credentials_or_device_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Original Hive'
            hive = Hive(root)
            hive.write_memory('01-Memory/User/taste.md', '# Taste\nPrefer concise summaries')
            task = hive.create_task({'title': 'Inspect', 'objective': 'Inspect a file', 'acceptance': ['Evidence']})
            hive.send('codex', 'grok', 'Ready for review', task['id'])
            (root / 'hive.local.json').write_text('{"memory_url":"https://old.example"}')
            (root / 'runtime/cloud-token').write_text('private-test-secret')
            destination = Path(tmp) / 'backup.zip'
            result = build_bundle(root, destination, include_state=True)
            self.assertTrue(result['task_database'])
            with zipfile.ZipFile(destination) as archive:
                self.assertEqual([n for n in archive.namelist() if '/runtime/' in n], ['HiveMind/runtime/hivemind.db'])
                self.assertNotIn('HiveMind/hive.local.json', archive.namelist())
                archive.extractall(Path(tmp) / 'restore')
            restored = Hive(Path(tmp) / 'restore/HiveMind')
            self.assertIn('concise', restored.read_note('01-Memory/User/taste.md')['text'])
            self.assertEqual(restored.get_task(task['id'])['spec']['title'], 'Inspect')
            self.assertIn('Ready for review', json.dumps(restored.inbox('grok')))
            with closing(sqlite3.connect(restored.db)) as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            original = destination.read_bytes()
            with self.assertRaises(FileExistsError):
                build_bundle(root, destination, include_state=True)
            self.assertEqual(destination.read_bytes(), original)

    @unittest.skipUnless(os.name == 'nt', 'Windows CMD launcher')
    def test_cmd_preserves_failure_status_and_project_paths_with_spaces(self):
        invalid = subprocess.run(['cmd.exe', '/d', '/c', str(ROOT / 'hivemind.cmd'), '--invalid-option'], capture_output=True)
        self.assertNotEqual(invalid.returncode, 0)
        with tempfile.TemporaryDirectory(prefix='Hive project ') as tmp:
            result = subprocess.run(['cmd.exe', '/d', '/c', str(ROOT / 'hivemind.cmd'), tmp, '--dry-run', '--skip-register'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
