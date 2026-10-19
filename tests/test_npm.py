"""The optional npm entry point must preserve shared state and terminal input."""
from pathlib import Path
import json
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
NPM = Path(shutil.which('npm')).resolve() if shutil.which('npm') else None
NPM_CLI = (NPM if NPM and NPM.suffix == '.js' else NPM.parent / 'node_modules/npm/bin/npm-cli.js' if NPM else None)


@unittest.skipUnless(shutil.which('node') and (ROOT / 'npm/launcher.test.cjs').is_file(), 'Node.js/source npm tests unavailable')
class NpmLauncherTests(unittest.TestCase):
    def test_node_launcher_regressions(self):
        result = subprocess.run([shutil.which('node'), '--test', str(ROOT / 'npm/launcher.test.cjs')],
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(NPM_CLI and NPM_CLI.is_file(), 'npm CLI unavailable')
    def test_package_allowlist_excludes_private_state_and_python_caches(self):
        result = subprocess.run([shutil.which('node'), str(NPM_CLI), 'pack', '--dry-run', '--ignore-scripts', '--json'],
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        files = [item['path'] for item in json.loads(result.stdout)[0]['files']]
        for name in files:
            self.assertFalse(name.startswith(('runtime/', 'vault/', 'cloud/', '.venv/', '.hivemind/')), name)
            self.assertNotIn('__pycache__', name)
            self.assertFalse(name.endswith(('.pyc', '.db', '.key', '.pem')), name)
        self.assertNotIn('hive.local.json', files)
        self.assertIn('npm/launcher.cjs', files)
        self.assertIn('bin/hivemind.cjs', files)
        for name in ('templates/project/hive', 'templates/project/hive.cmd', 'templates/project/hive.py',
                     'templates/vault/START.md', 'bootstrap.py', 'hive.py', 'requirements.txt'):
            self.assertIn(name, files)
        metadata = json.loads((ROOT / 'package.json').read_text())
        self.assertFalse(set(metadata.get('scripts', {})) & {'install', 'postinstall', 'preinstall', 'prepare'})


if __name__ == '__main__':
    unittest.main()
