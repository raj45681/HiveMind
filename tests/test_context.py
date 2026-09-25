import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from hivemind.context import excerpt, size
from hivemind.store import Hive, atomic_write

ROOT = Path(__file__).resolve().parents[1]


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hive = Hive(self.tmp.name)

    def test_complete_excerpts_and_unicode_envelope_budget(self):
        first, long, last = 'A complete sentence.', 'X' * 900 + '.', 'Another complete sentence.'
        text, omitted = excerpt(first + '\n\n' + long + '\n\n' + last, 80)
        self.assertEqual(text, first + '\n' + last)
        self.assertTrue(omitted)
        for name in ('HIVE', 'Personality', 'Working-Style'):
            atomic_write(self.hive.vault / f'00-System/{name}.md', 'Use complete sentences.\n\n' * 50)
        self.hive.write_memory('01-Memory/User/Preferences.md', '# Taste\n\n喜欢简洁的解释。\n\n' * 30)
        self.hive.write_memory('03-Projects/app/Current-State.md', '# State\n\nTests pass.\n\n' * 100)
        for budget in (512, 700, 1000, 1800, 8192):
            result = self.hive.context(project='app', query='Tests', budget_tokens=budget)
            self.assertLessEqual(size(result), budget * 4)
            self.assertLessEqual(result['budget']['estimated_tokens'], budget)
            for card in result['instructions'] + result['preferences']:
                self.assertNotIn('…', card['text'])
        with self.assertRaises(ValueError):
            self.hive.context(budget_tokens=20)
        (self.hive.root / 'hive.local.json').write_text('{"context_budget_tokens":700}')
        self.assertEqual(self.hive.context()['budget']['requested_tokens'], 700)

    def test_scope_literal_project_ids_and_relevance_freshness(self):
        for path in ('03-Projects/app_one/fix.md', '03-Projects/appXone/fix.md', '01-Memory/Solutions/fix.md'):
            self.hive.write_memory(path, '# Cache\n\nCache invalidation fix verified.')
        scoped = self.hive.search('cache', project='app_one')
        self.assertEqual(scoped[0]['path'], '03-Projects/app_one/fix.md')
        self.assertNotIn('appXone', json.dumps(scoped))
        self.assertIn('Solutions', json.dumps(scoped))
        for name in ('a-old', 'z-fresh'):
            self.hive.write_memory(f'01-Memory/{name}.md', '# Identical\n\nSame freshness search terms.')
        old = self.hive.note_path('01-Memory/a-old.md')
        os.utime(old, (time.time() - 400 * 86400,) * 2)
        ranked = self.hive.search('freshness')
        self.assertEqual(ranked[0]['path'], '01-Memory/z-fresh.md')

    def test_project_preferences_latest_session_and_original_notes_unchanged(self):
        # Real starter rules exercise competition for room in the smallest brief.
        for template in (ROOT / 'templates/vault').rglob('*.md'):
            atomic_write(self.hive.vault / template.relative_to(ROOT / 'templates/vault'), template.read_text(encoding='utf-8'))
        self.hive.write_memory('03-Projects/app/Preferences/style.md', '# Style\nUse the existing design system.')
        self.hive.write_memory('03-Projects/other/Preferences/style.md', '# Other\nUse a different design system.')
        original = self.hive.read_note('03-Projects/app/Preferences/style.md')
        session = self.hive.session_start('app', 'codex', 'Fix login')['session']
        self.hive.session_checkpoint(session['id'], {'summary':'Login fixed.', 'completed':['Fixed login'],
            'verification':['Login test passed'], 'next_steps':['Review the change'], 'status':'completed'}, 0)
        result = self.hive.context(project='app', query='design', budget_tokens=1800)
        self.assertEqual(result['session']['id'], session['id'])
        self.assertIn('existing design', json.dumps(result['project_preferences']))
        self.assertNotIn('different design', json.dumps(result))
        tiny = self.hive.context(project='app', budget_tokens=512)
        self.assertIsNotNone(tiny['session'])
        self.assertLessEqual(size(tiny), 2048)
        self.assertEqual(self.hive.read_note(original['path']), original)


if __name__ == '__main__':
    unittest.main()
