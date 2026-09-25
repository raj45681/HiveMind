from pathlib import Path
import tempfile
import unittest
import zipfile

from hivemind.seed import seed_vault
from scripts.package import build_bundle


class SeedTests(unittest.TestCase):
    def test_fresh_clone_gets_defaults_without_overwriting_personal_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = root / 'templates/vault/00-System/Personality.md'
            template.parent.mkdir(parents=True)
            template.write_text('Generic default')
            self.assertEqual(seed_vault(root), ['00-System/Personality.md'])
            local = root / 'vault/00-System/Personality.md'
            self.assertEqual(local.read_text(), 'Generic default')
            local.write_text('My own style')
            self.assertEqual(seed_vault(root), [])
            self.assertEqual(local.read_text(), 'My own style')

    def test_source_bundle_contains_templates_but_no_personal_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = root / 'templates/vault/START.md'
            template.parent.mkdir(parents=True)
            template.write_text('Generic start')
            seed_vault(root)
            (root / 'vault/private.md').write_text('Private memory')
            (root / 'cloud-defaults.json').write_text('{"memory_url":"https://private.example"}')
            archive = root / 'source.zip'
            build_bundle(root, archive)
            with zipfile.ZipFile(archive) as bundle:
                self.assertIn('HiveMind/templates/vault/START.md', bundle.namelist())
                self.assertFalse(any(n.startswith('HiveMind/vault/') for n in bundle.namelist()))
                self.assertNotIn('HiveMind/cloud-defaults.json', bundle.namelist())


if __name__ == '__main__':
    unittest.main()
