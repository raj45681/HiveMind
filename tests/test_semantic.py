"""Local semantic retrieval, including a real paraphrase when the model is installed."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hivemind.store import Hive, atomic_write
from hivemind import semantic

ROOT = Path(__file__).resolve().parents[1]


class SemanticFallbackTests(unittest.TestCase):
    def test_setup_requires_offline_embedding_before_ready_marker(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(semantic, 'interpreter', return_value=Path(sys.executable)), \
                patch.object(semantic.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), \
                patch.object(semantic, '_embed', side_effect=[[[0.0] * 384], RuntimeError('offline cache absent')]) as embed:
            with self.assertRaisesRegex(RuntimeError, 'offline cache absent'):
                semantic.setup(folder)
            self.assertEqual([call.kwargs['download'] for call in embed.call_args_list], [True, False])
            self.assertFalse((Path(folder) / 'runtime/tools/semantic-ready.json').exists())

    def test_marker_without_working_model_is_degraded(self):
        with patch.object(semantic, 'ready', return_value=True), \
                patch.object(semantic, '_embed', side_effect=RuntimeError('cache absent')):
            status = semantic.health(ROOT)
        self.assertEqual(status['status'], 'degraded')
        self.assertIn('cache absent', status['detail'])

    def test_keyword_search_still_works_without_optional_model(self):
        with tempfile.TemporaryDirectory() as folder:
            hive = Hive(folder)
            hive.write_memory('01-Memory/Solutions/auth.md', '# Authentication\n\nRefresh the credentials.')
            with patch.object(semantic, 'ready', return_value=False):
                self.assertEqual(hive.search('credentials')[0]['path'], '01-Memory/Solutions/auth.md')
                self.assertEqual(hive.search('unrelated'), [])
            with patch.object(semantic, 'ready', return_value=True), patch.object(semantic, '_embed', side_effect=RuntimeError('model unavailable')):
                self.assertEqual(hive.search('credentials')[0]['path'], '01-Memory/Solutions/auth.md')
                self.assertIn('model unavailable', (hive.runtime / 'semantic-last-error.log').read_text())
                brief = hive.context(query='credentials', budget_tokens=1000)
                self.assertIn('degraded', brief['retrieval_warning'])


@unittest.skipUnless(semantic.ready(ROOT), 'Optional local FastEmbed model is not installed')
class RealSemanticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hive = Hive(self.temp.name)
        original = semantic._embed
        self.real_embed = original
        self.patch_embed = patch.object(semantic, '_embed', side_effect=lambda root, texts: original(ROOT, texts))
        self.patch_ready = patch.object(semantic, 'ready', return_value=True)
        self.patch_embed.start()
        self.patch_ready.start()
        self.addCleanup(self.patch_embed.stop)
        self.addCleanup(self.patch_ready.stop)

    def test_offline_health_detects_missing_model_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            marker = root / 'runtime/tools/semantic-ready.json'
            marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({'model': semantic.MODEL, 'version': semantic.VERSION}))
            with patch.object(semantic, 'interpreter', return_value=semantic.interpreter(ROOT)), \
                    patch.object(semantic, '_embed', side_effect=self.real_embed):
                status = semantic.health(root)
            self.assertEqual(status['status'], 'degraded')
            self.assertIn('Offline model probe failed', status['detail'])

    def test_paraphrase_recall_scope_context_and_reindex(self):
        target = '03-Projects/app/Solutions/auth-race.md'
        self.hive.write_memory(target, '# Credential renewal\n\nA race in refreshing access credentials caused repeated authentication failures. The fix serialized renewal and replayed blocked requests.')
        self.hive.write_memory('03-Projects/other/Solutions/auth.md', '# Credential renewal\n\nA race in refreshing access credentials occurred elsewhere.')
        self.hive.write_memory('01-Memory/Solutions/cache.md', '# Cache eviction\n\nThe eviction policy discarded stale database entries.')
        self.hive.write_memory('01-Memory/Candidates/auth.md', '# Credential renewal\n\nA race in refreshing access credentials caused repeated authentication failures.')
        atomic_write(self.hive.note_path('99-Archive/auth.md'), '# Credential renewal\n\nA race in refreshing access credentials caused repeated authentication failures.')
        query = 'Why did simultaneous API traffic trigger surprise logout?'
        with patch.object(semantic, 'ready', return_value=False):
            self.assertEqual(self.hive.search(query, project='app'), [])
        results = self.hive.search(query, project='app')
        self.assertEqual(results[0]['path'], target)
        self.assertIn('authentication failures', results[0]['excerpt'])
        self.assertNotIn('03-Projects/other/', json.dumps(results))
        self.assertNotIn('Candidates', json.dumps(results))
        self.assertNotIn('99-Archive', json.dumps(results))
        self.assertNotIn('cache.md', json.dumps(results))
        self.assertEqual(self.hive.search('How to repair a leaking bathroom faucet?', project='app'), [])
        brief = self.hive.context(project='app', query=query, budget_tokens=1800)
        self.assertIn('authentication failures', json.dumps(brief['relevant']))
        self.assertLessEqual(brief['budget']['estimated_tokens'], 1800)
        self.hive.write_memory(target, '# Credential renewal\n\nThe fix now serializes renewal and logs retries.',
                               expected_revision=results[0]['revision'])
        updated = self.hive.search(query, project='app')
        self.assertNotEqual(updated[0]['revision'], results[0]['revision'])
        self.assertNotIn('authentication failures', json.dumps(updated))
        self.hive.note_path(target).unlink()
        self.assertNotIn(target, [item['path'] for item in self.hive.search(query, project='app')])


if __name__ == '__main__':
    unittest.main()
