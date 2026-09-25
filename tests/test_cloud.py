import json
import tempfile
import unittest
from pathlib import Path
import httpx
from hivemind.cloud import CloudMemory
from hivemind.learning import learning_note
from hivemind.store import Hive


class LearningTests(unittest.TestCase):
    def test_preferences_cross_projects_and_observations_stay_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            hive = Hive(tmp)
            for basis in ('user-stated', 'observation'):
                path, text = learning_note('preference', basis, 'Prefer Python', 'User message', basis=basis)
                hive.write_memory(path, text)
            profile = json.dumps(hive.context(project='brand-new'))
            self.assertIn('Prefer Python', profile)
            self.assertNotIn('Candidates', profile)
            self.assertEqual(len(hive.search('Prefer Python')), 1)
            path, text = learning_note('preference', 'project-taste', 'Use React here', 'User message', project='alpha', basis='user-stated')
            hive.write_memory(path, text)
            self.assertNotIn('Use React here', json.dumps(hive.context(project='beta')))
            with self.assertRaises(ValueError):
                learning_note('solution', 'fix', 'Guess', 'trace')
            with self.assertRaises(ValueError):
                learning_note('decision', 'db', 'SQLite', 'trace')


class CloudTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_outbox_persists_retries_and_stale_reads_are_labelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            online = True
            writes = []
            def respond(request):
                if not online:
                    raise httpx.ConnectError('Offline', request=request)
                self.assertEqual(request.headers['oai-sites-authorization'], 'Bearer private-key')
                body = json.loads(request.content)
                if body['tool'] == 'memory_write':
                    writes.append(body['args'])
                    return httpx.Response(200, json={'result':{'path':body['args']['path'], 'revision':'r1', 'saved':True}})
                return httpx.Response(200, json={'result':{'instructions':[], 'preferences':['shared taste']}})
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
                cloud=CloudMemory(tmp,'https://memory.example','private-key',http)
                await cloud.call('hive_context')
                online=False
                queued=await cloud.call('memory_write',path='01-Memory/offline.md',content='Verified fix',expected_revision='new')
                self.assertFalse(queued['saved']); self.assertTrue(queued['queued'])
                self.assertTrue((await cloud.call('hive_context'))['sync']['stale'])
                with self.assertRaisesRegex(ValueError,'no cached'):
                    await cloud.call('note_read',path='unknown.md')
                # Simulate restarting the process on this device.
                cloud=CloudMemory(tmp,'https://memory.example','private-key',http)
                online=True
                self.assertEqual(await cloud.flush(),{})
                self.assertEqual(len(writes),1)
                self.assertEqual(await cloud.flush(),{})
                self.assertEqual(len(writes),1)

    async def test_conflicts_and_url_scoping_preserve_unacknowledged_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            def respond(request):
                return httpx.Response(409,json={'error':'Note changed; read current revision'})
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
                cloud=CloudMemory(tmp,'https://one.example','private-key',http)
                with self.assertRaisesRegex(ValueError,'Note changed'):
                    await cloud.call('memory_write',path='01-Memory/a.md',content='a',expected_revision='old')
                self.assertEqual(cloud.counts(),{'conflict':1})
                self.assertEqual(await cloud.flush(),{'conflict':1})
                other=CloudMemory(tmp,'https://two.example','private-key',http)
                self.assertEqual(await other.flush(),{})

    def test_remote_urls_require_https_and_no_embedded_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            for url in ('http://remote.example','https://user:secret@remote.example','https://remote.example/?key=secret'):
                with self.assertRaises(ValueError):
                    CloudMemory(tmp,url,'token')
