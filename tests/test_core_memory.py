"""End-to-end memory lifecycle, task context, consolidation, and outcome learning."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hivemind.learning import learning_note
from hivemind.memory import fields
from hivemind.store import Hive


class CoreMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hive = Hive(self.tmp.name)

    def learn(self, key, summary='Serialize credential refresh with a mutex.', project='app', kind='solution', **extras):
        args = {'project': project, 'basis': 'verified-result', 'evidence': 'Synthetic fixture assertion passed', **extras}
        if kind == 'decision':
            args.pop('evidence')
        path, content = learning_note(kind, key, summary, 'synthetic fixture', **args)
        saved = self.hive.write_memory(path, content)
        return path, saved['revision']

    def task(self, **changes):
        return self.hive.create_task({'title': 'Improve authentication', 'objective': 'Handle credential renewal',
                                     'project': 'app', 'agent': 'codex', 'acceptance': ['Verify behavior'], **changes})

    def finish(self, task, **changes):
        claim = self.hive.claim('fixture', 'codex', task['id'])
        return self.hive.finish(task['id'], claim['claim_token'], {'status': 'done', 'summary': 'Serialized credential renewal',
                                'verification': ['Synthetic regression passed'], **changes})

    def test_replacement_preserves_old_revision_and_changes_default_recall(self):
        old, a = self.learn('old', 'Credential renewal uses parallel refresh.')
        new, b = self.learn('new', 'Credential renewal now uses serialized refresh.')
        saved = self.hive.memory_relate(path=new, related=old, relation='supersedes',
                                      expected_revision=b, related_revision=a, source='Current architecture decision')
        self.assertTrue(saved['saved'])
        self.assertNotIn(old, [hit['path'] for hit in self.hive.search('credential renewal', project='app')])
        history = self.hive.search('credential renewal', project='app', archive=True)
        older = next(hit for hit in history if hit['path'] == old)
        self.assertEqual(older['state'], 'superseded')
        self.assertEqual(older['replaced_by'], new)
        self.assertIn('parallel', self.hive.read_note(old, revision=a)['text'])
        with self.assertRaises(ValueError):
            self.hive.memory_relate(path=old, related=new, relation='supersedes',
                                   expected_revision=saved['revisions'][old], related_revision=b, source='Cycle attempt')

    def test_explicit_conflicts_are_symmetric_visible_and_revision_checked(self):
        a, ar = self.learn('first')
        b, br = self.learn('second', 'Permit parallel credential refresh.')
        relation = self.hive.memory_relate(path=a, related=b, relation='conflicts', expected_revision=ar,
                                         related_revision=br, source='Conflicting design notes')
        hits = self.hive.search('credential refresh', project='app')
        self.assertTrue(all('warning' in hit for hit in hits))
        self.assertIn(b, next(hit for hit in hits if hit['path'] == a)['conflicts'])
        with self.assertRaises(ValueError):
            self.hive.memory_relate(path=a, related=b, relation='resolve', expected_revision=ar,
                                   related_revision=br, source='Stale review')
        self.hive.memory_relate(path=a, related=b, relation='resolve', expected_revision=relation['revisions'][a],
                               related_revision=relation['revisions'][b], source='Sources reconciled')
        self.assertFalse(any('warning' in hit for hit in self.hive.search('credential refresh', project='app')))

    def test_competing_current_claims_are_flagged_without_inventing_truth(self):
        self.learn('a', topic='auth.refresh', claim='serialize')
        self.learn('b', topic='auth.refresh', claim='parallel')
        self.assertTrue(all('warning' in hit for hit in self.hive.search('credential refresh', project='app')))
        brief = self.hive.context(project='app', query='credential refresh', budget_tokens=1000)
        self.assertTrue(any('warning' in note for note in brief['relevant']))

    def test_relationship_failure_restores_both_notes_and_database(self):
        a, ar = self.learn('a')
        b, br = self.learn('b')
        original = {name: self.hive.note_path(name).read_bytes() for name in (a, b)}
        index = self.hive._index_note
        calls = []
        def fail_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise OSError('fixture write failure')
            return index(*args, **kwargs)
        with patch.object(self.hive, '_index_note', side_effect=fail_second), self.assertRaises(OSError):
            self.hive.memory_relate(path=a, related=b, relation='conflicts', expected_revision=ar,
                                   related_revision=br, source='Fixture')
        self.assertEqual(original, {name: self.hive.note_path(name).read_bytes() for name in (a, b)})
        self.assertFalse(any('warning' in hit for hit in self.hive.search('credential refresh', project='app')))

    def test_legacy_scoped_solutions_are_isolated_in_keyword_and_semantic_recall(self):
        legacy = '01-Memory/Solutions/legacy.md'
        self.hive.write_memory(legacy, '# Legacy\n\nKind: solution\nProject: alpha\n\nViolet semaphore handles credential renewal.\n')
        self.assertEqual(self.hive.search('violet semaphore', project='beta'), [])
        from hivemind import semantic
        vector = [1.0] + [0.0] * 383
        with patch.object(semantic, 'ready', return_value=True), \
                patch.object(semantic, '_embed', side_effect=lambda root, texts: [vector] * len(texts)):
            self.assertEqual(self.hive.search('violet semaphore', project='beta'), [])
            self.assertEqual(self.hive.search('violet semaphore', project='alpha')[0]['path'], legacy)

    def test_task_brief_finds_file_linked_decisions_and_dependency_evidence(self):
        path, _ = self.learn('renewal', 'Use a mutex for renewal.', files=['src/auth.py'])
        parent = self.task(title='Investigate renewal')
        self.finish(parent)
        task = self.task(title='Change unrelated labels', files=['src/auth.py'], depends_on=[parent['id']])
        for budget in (512, 1000, 1800):
            brief = self.hive.context(project='app', task_id=task['id'], budget_tokens=budget)
            self.assertLessEqual(len(json.dumps(brief, ensure_ascii=False).encode()), budget * 4)
            self.assertIn(path, [note['path'] for note in brief['relevant']])
        brief = self.hive.context(project='app', task_id=task['id'], budget_tokens=1800)
        self.assertEqual(brief['task']['dependencies'][0]['status'], 'done')
        self.assertIn('Synthetic regression passed', brief['task']['dependencies'][0]['verification'])
        with self.assertRaises(ValueError):
            self.hive.context(project='other', task_id=task['id'])

    def test_cached_code_relationships_extend_file_linked_context(self):
        import hashlib
        repo = Path(self.tmp.name) / 'repo'
        repo.mkdir()
        self.hive.root.joinpath('hive.local.json').write_text(json.dumps({'projects': {'app': str(repo)}, 'graphify_projects': ['app']}))
        key = hashlib.sha256(str(repo.resolve()).encode()).hexdigest()[:16]
        folder = self.hive.runtime / 'code-index' / key
        folder.mkdir(parents=True)
        (folder / 'index.json').write_text(json.dumps({'graph': {'nodes': [
            {'id': 'a', 'file': 'src/auth.py'}, {'id': 'b', 'file': 'src/cache.py'}],
            'links': [{'source': 'a', 'target': 'b'}]}}))
        path, _ = self.learn('related-cache', 'Serialize renewal.', files=['src/cache.py'])
        brief = self.hive.context(project='app', files=['src/auth.py'], budget_tokens=1000)
        self.assertIn('src/cache.py', brief['related_files'])
        self.assertIn(path, [note['path'] for note in brief['relevant']])

    def test_completed_task_generates_draft_and_acceptance_creates_scoped_learning(self):
        task = self.task(files=['src/auth.py'])
        finished = self.finish(task)
        proposal = finished['learning_proposal']
        self.assertTrue(proposal['saved'])
        self.assertEqual(self.hive.search('serialized credential renewal', project='app'), [])
        accepted = self.hive.learning_review(proposal['path'], proposal['revision'], accept=True)
        self.assertTrue(accepted['saved'])
        self.assertIn(accepted['target'], [note['path'] for note in self.hive.search('serialized credential renewal', project='app')])
        self.assertEqual(self.hive.search('serialized credential renewal', project='other'), [])
        with self.assertRaises(ValueError):
            self.hive.learning_review(proposal['path'], proposal['revision'], accept=True)

    def test_consolidation_is_reviewed_preserves_history_and_carries_files(self):
        a, ar = self.learn('duplicate-a', files=['src/auth.py'])
        b, br = self.learn('duplicate-b', files=['src/cache.py'])
        preview = self.hive.memory_consolidate('app', paths=[a, b])
        self.assertFalse(preview['staged'])
        staged = self.hive.memory_consolidate('app', paths=[a, b], stage=True)['groups'][0]
        again = self.hive.memory_consolidate('app', paths=[a, b], stage=True)['groups'][0]
        self.assertEqual(staged, again)
        accepted = self.hive.learning_review(staged['path'], staged['revision'], accept=True)
        self.assertIn('src/cache.py', self.hive.read_note(accepted['target'])['text'])
        paths = [hit['path'] for hit in self.hive.search('credential refresh', project='app')]
        self.assertIn(accepted['target'], paths)
        self.assertNotIn(a, paths)
        self.assertNotIn(b, paths)
        self.assertIn('Serialize', self.hive.read_note(a, revision=ar)['text'])
        self.assertIn('Serialize', self.hive.read_note(b, revision=br)['text'])

    def test_source_drift_blocks_consolidation_without_partial_changes(self):
        a, ar = self.learn('a')
        b, br = self.learn('b')
        draft = self.hive.memory_consolidate('app', paths=[a, b], stage=True)['groups'][0]
        self.hive.write_memory(b, '# Changed\n\nProject: app\n\nNew evidence.\n', br)
        with self.assertRaises(ValueError):
            self.hive.learning_review(draft['path'], draft['revision'], accept=True)
        self.assertEqual(self.hive.read_note(a)['revision'], ar)
        self.assertEqual(fields(self.hive.read_note(draft['path'])['text'])['Status'], 'draft')

    def test_shared_consolidation_remains_shared(self):
        a, _ = self.learn('a', project='')
        b, _ = self.learn('b', project='')
        draft = self.hive.memory_consolidate('', paths=[a, b], stage=True)['groups'][0]
        accepted = self.hive.learning_review(draft['path'], draft['revision'], accept=True)
        self.assertTrue(accepted['target'].startswith('01-Memory/Solutions/'))
        self.assertIn(accepted['target'], [hit['path'] for hit in self.hive.search('credential refresh', project='other')])

    def test_oversized_proposal_is_rejected_before_creating_an_unreviewable_draft(self):
        a, _ = self.learn('long', summary='x' * 3000, kind='procedure', trigger='x' * 100,
                          steps=['Inspect ' + 'x' * 340] * 8, evidence='x' * 1400, applicability='x' * 100)
        b, _ = self.learn('short', kind='procedure', trigger='Credentials stale', steps=['Refresh credentials'])
        with self.assertRaises(ValueError):
            self.hive.memory_consolidate('app', paths=[a, b], stage=True)
        self.assertEqual(list((self.hive.vault / '03-Projects' / 'app').glob('Review-Queue/PROPOSAL-*.md')), [])

    def test_explicit_successful_procedure_use_improves_ranking_at_current_revision(self):
        a, _ = self.learn('a', kind='procedure', trigger='Credentials stale', steps=['Acquire mutex', 'Refresh credentials'])
        b, br = self.learn('b', kind='procedure', trigger='Credentials stale', steps=['Acquire mutex', 'Refresh credentials'])
        task = self.task()
        result = self.finish(task, used_procedures=[{'path': b, 'revision': br, 'evidence': 'Applied steps and regression passed'}])
        self.assertEqual(result['recorded_procedure_uses'], 1)
        self.assertEqual(self.hive.search('credential refresh', project='app')[0]['path'], b)

    def test_cross_scope_relationships_and_path_escape_are_rejected(self):
        a, ar = self.learn('a')
        b, br = self.learn('b', project='other')
        with self.assertRaises(ValueError):
            self.hive.memory_relate(path=a, related=b, relation='conflicts', expected_revision=ar,
                                   related_revision=br, source='Invalid cross scope')
        with self.assertRaises(ValueError):
            self.task(files=['../private.py'])
