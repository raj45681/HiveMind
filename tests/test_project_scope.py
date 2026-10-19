import asyncio
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import venv

import bootstrap
from hivemind import assisted, harnesses, project_scope, setup
from hivemind.project import attach
from hivemind.store import Hive, atomic_write
from scripts.register_agents import register_agents


SOURCE = Path(__file__).resolve().parents[1]


class ProjectScopeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='Hive local scope ')
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name).resolve()
        self.root, self.project, self.home = [self.folder / name for name in ('Shared Hive', 'My App', 'User')]
        for folder in (self.root, self.project, self.home):
            folder.mkdir()
        atomic_write(self.root / 'hive.local.json', json.dumps({'offline': True, 'projects': {'alpha': str(self.project)}}))

    def server(self, root=None):
        return {'command': sys.executable, 'args': [str(SOURCE / 'hive.py'), '--root', str(root or self.root), 'serve']}

    def test_all_project_adapters_leave_user_profiles_byte_identical(self):
        paths = {'.codex/config.toml': '# Personal settings\nmodel = "my-model"\n',
                 '.grok/config.toml': '# Other settings\n',
                 '.gemini/config/mcp_config.json': '{"mcpServers":{"other":{"command":"keep"}}}',
                 '.claude.json': '{"unrelated":true}', '.codeium/windsurf/mcp_config.json': '{}'}
        for name, text in paths.items():
            atomic_write(self.home / name, text)
        before = {p.relative_to(self.home): p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        with patch('hivemind.harnesses.subprocess.run') as launched:
            results = harnesses.install(self.root, self.project, 'alpha', harnesses.HARNESS,
                                       capture=True, home=self.home)
        launched.assert_not_called()
        self.assertEqual(before, {p.relative_to(self.home): p.read_bytes() for p in self.home.rglob('*') if p.is_file()})
        self.assertTrue(all(row['status'] == 'configured' for row in results.values()))
        self.assertEqual(results['codex']['mcp']['scope'], 'project')
        self.assertEqual(results['grok']['mcp']['scope'], 'project')
        self.assertEqual(results['cline']['mcp']['status'], 'launcher')
        self.assertEqual(results['windsurf']['mcp']['status'], 'cli-fallback')
        self.assertEqual(results['antigravity']['mcp']['status'], 'cli-fallback')
        for name in ('codex', 'grok'):
            self.assertTrue(Path(results[name]['capture']['path']).is_relative_to(self.project))

    def test_project_toml_preserves_comments_bom_crlf_and_other_tables(self):
        prefix = '\ufeff# Personal comments\r\nmodel = "keep"\r\n[mcp_servers.other]\r\ncommand = "other-tool"\r\n'
        path = self.project / '.codex/config.toml'
        atomic_write(path, prefix)
        result = harnesses.install_mcp(self.root, self.project, 'codex', home=self.home)
        self.assertTrue(result['changed'])
        self.assertTrue(path.read_bytes().startswith(prefix.encode()))
        body = tomllib.loads(path.read_bytes().decode('utf-8-sig'))
        self.assertEqual(body['mcp_servers']['other'], {'command': 'other-tool'})
        self.assertFalse(harnesses.install_mcp(self.root, self.project, 'codex', home=self.home)['changed'])
        self.assertEqual(list(self.home.iterdir()), [])

    def test_unmanaged_conflicting_toml_is_preserved(self):
        path = self.project / '.grok/config.toml'
        original = '[mcp_servers.hivemind]\ncommand="user-command"\n'
        atomic_write(path, original)
        with self.assertRaisesRegex(ValueError, 'unmanaged'):
            harnesses.install_mcp(self.root, self.project, 'grok', home=self.home)
        self.assertEqual(path.read_text(), original)

    def test_saved_user_scope_does_not_reenable_global_activation(self):
        config = json.loads((self.root / 'hive.local.json').read_text())
        config['project_onboarding'] = {'alpha': {'scope': 'user', 'harnesses': ['codex']}}
        atomic_write(self.root / 'hive.local.json', json.dumps(config))
        args = bootstrap.build_parser().parse_args([str(self.project), '--no-prompt'])
        choices = setup.choose(self.root, 'alpha', args, interactive=False)
        self.assertEqual(choices['scope'], 'project')

    def test_registration_defaults_to_project_files_without_vendor_commands(self):
        with patch('scripts.register_agents.shutil.which', return_value='fixture-cli'), \
                patch('hivemind.harnesses.subprocess.run') as launched:
            result = register_agents(self.root, project=self.project)
        launched.assert_not_called()
        self.assertEqual(result['codex']['scope'], 'project')
        self.assertEqual(result['grok']['scope'], 'project')
        self.assertEqual(result['antigravity']['status'], 'cli-fallback')

    def test_capture_without_project_cannot_fall_back_to_user_directory(self):
        with self.assertRaisesRegex(ValueError, 'project directory'):
            assisted.install_hooks(self.root, home=self.home)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_cleanup_preview_and_apply_preserve_unrelated_servers_and_hooks(self):
        server = self.server()
        other = {'command': 'other-tool', 'env': {'KEEP': 'dummy-value'}}
        config = self.home / '.gemini/config/mcp_config.json'
        atomic_write(config, json.dumps({'mcpServers': {'hivemind': server, 'other': other}, 'user': {'keep': True}}))
        toml_path = self.home / '.codex/config.toml'
        raw = '# Keep comments\nmodel = "keep"\n[mcp_servers.hivemind]\ncommand = ' + json.dumps(server['command'])
        raw += '\nargs = ' + json.dumps(server['args']) + '\n[mcp_servers.other]\ncommand = "other-tool"\n'
        atomic_write(toml_path, raw)
        hooks = self.home / '.codex/hooks.json'
        own = {'type': 'command', 'command': harnesses.command_string(self.root, 'codex')}
        kept = {'type': 'command', 'command': 'my-own-script'}
        atomic_write(hooks, json.dumps({'hooks': {'Stop': [{'matcher': 'keep', 'hooks': [own, kept]}]}, 'version': 1}))
        before = {p: p.read_bytes() for p in (config, toml_path, hooks)}
        preview = project_scope.cleanup(self.root, home=self.home)
        self.assertTrue(preview['ok'])
        self.assertEqual(len(preview['changes']), 3)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        result = project_scope.cleanup(self.root, apply=True, home=self.home)
        self.assertTrue(result['ok'])
        self.assertEqual(json.loads(config.read_text()), {'mcpServers': {'other': other}, 'user': {'keep': True}})
        self.assertEqual(tomllib.loads(toml_path.read_text()), {'model': 'keep', 'mcp_servers': {'other': {'command': 'other-tool'}}})
        self.assertTrue(toml_path.read_text().startswith('# Keep comments'))
        self.assertEqual(json.loads(hooks.read_text())['hooks']['Stop'][0], {'matcher': 'keep', 'hooks': [kept]})
        self.assertEqual(project_scope.cleanup(self.root, apply=True, home=self.home)['changes'], [])
        backups = list((self.root / 'runtime/adapter-backups').iterdir())
        self.assertEqual(len(backups), 3)

    def test_cleanup_leaves_other_hive_installations_and_inline_toml_unchanged(self):
        foreign = self.home / '.claude.json'
        atomic_write(foreign, json.dumps({'mcpServers': {'hivemind': self.server(self.folder / 'Other Hive')}}))
        inline = self.home / '.grok/config.toml'
        server = self.server()
        raw = '[mcp_servers]\nhivemind = { command = ' + json.dumps(server['command']) + ', args = ' + json.dumps(server['args']) + ' }\n'
        atomic_write(inline, raw)
        before = {p: p.read_bytes() for p in (foreign, inline)}
        result = project_scope.cleanup(self.root, apply=True, home=self.home)
        self.assertFalse(result['ok'])
        self.assertEqual(len(result['changes']), 1)
        self.assertEqual(result['changes'][0]['status'], 'needs-action')
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_cleanup_removes_owned_hooks_from_global_client_settings(self):
        for relative, key in (('.claude/settings.json', 'hooks'), ('.cursor/hooks.json', 'hooks'),
                              ('.gemini/config/hooks.json', 'hivemind')):
            body = {key: {'Stop': [{'hooks': [{'type': 'command', 'command': harnesses.command_string(self.root, 'claude')}]}]},
                    'my-setting': True}
            atomic_write(self.home / relative, json.dumps(body))
        result = project_scope.cleanup(self.root, apply=True, home=self.home)
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['changes']), 3)
        for relative, key in (('.claude/settings.json', 'hooks'), ('.cursor/hooks.json', 'hooks'),
                              ('.gemini/config/hooks.json', 'hivemind')):
            self.assertEqual(json.loads((self.home / relative).read_text()), {key: {'Stop': []}, 'my-setting': True})

    def test_cleanup_backs_up_only_owned_global_opencode_plugin(self):
        from hivemind.opencode_plugin import plugin_source
        plugin = self.home / '.config/opencode/plugins/hivemind.js'
        original = plugin_source(self.root)
        atomic_write(plugin, original)
        self.assertTrue(project_scope.cleanup(self.root, home=self.home)['ok'])
        self.assertEqual(plugin.read_text(), original)
        result = project_scope.cleanup(self.root, apply=True, home=self.home)
        self.assertTrue(result['ok'])
        self.assertFalse(plugin.exists())
        self.assertEqual(next((self.root / 'runtime/adapter-backups').glob('*.js')).read_text(), original)
        foreign = plugin_source(self.folder / 'Other Hive')
        atomic_write(plugin, foreign)
        self.assertEqual(project_scope.cleanup(self.root, apply=True, home=self.home)['changes'], [])
        self.assertEqual(plugin.read_text(), foreign)

    def test_project_helpers_share_one_home_and_preserve_rerun(self):
        first = asyncio.run(attach(self.root, self.project, 'alpha', skip_register=True))
        other = self.folder / 'Other App'
        other.mkdir()
        asyncio.run(attach(self.root, other, 'beta', skip_register=True))
        for project in (self.project, other):
            self.assertEqual(json.loads((project / '.hivemind/local.json').read_text())['hive_home'], str(self.root))
            self.assertFalse((project / 'vault').exists())
            self.assertFalse((project / 'runtime').exists())
            self.assertTrue((project / '.hivemind/hive.py').is_file())
        self.assertEqual(asyncio.run(attach(self.root, self.project, 'alpha', skip_register=True))['changed_files'], [])
        self.assertIn('context --query', (self.project / 'AGENTS.md').read_text())

    def test_cleanup_handles_known_config_alias_without_replacing_symlink(self):
        canonical = self.home / '.gemini/config/mcp_config.json'
        atomic_write(canonical, json.dumps({'mcpServers': {'hivemind': self.server(), 'other': {'command': 'keep'}}}))
        alias = self.home / '.gemini/antigravity/mcp_config.json'
        alias.parent.mkdir(parents=True)
        try:
            alias.symlink_to(canonical)
        except OSError:
            self.skipTest('File symlinks need platform permission')
        result = project_scope.cleanup(self.root, apply=True, home=self.home)
        self.assertTrue(result['ok'])
        self.assertTrue(alias.is_symlink())
        self.assertEqual(json.loads(alias.read_text()), {'mcpServers': {'other': {'command': 'keep'}}})

    def test_real_project_helper_selects_shared_home_and_project_id(self):
        asyncio.run(attach(self.root, self.project, 'alpha', skip_register=True))
        venv.EnvBuilder(with_pip=False).create(self.root / '.venv')
        atomic_write(self.root / 'hive.py', 'import json,sys,os\nprint(json.dumps({"args":sys.argv[1:],"cwd":os.getcwd()}))\n')
        result = subprocess.run([sys.executable, str(self.project / '.hivemind/hive.py'), 'handoff'],
                                cwd=self.folder, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['args'], ['--root', str(self.root), 'handoff', 'alpha'])
        self.assertEqual(Path(payload['cwd']), self.project)
        if os.name == 'nt':
            native = subprocess.run([str(self.project / '.hivemind/hive.cmd'), 'handoff'],
                                    cwd=self.folder, capture_output=True, text=True, timeout=30)
            self.assertEqual(native.returncode, 0, native.stderr)
            self.assertEqual(json.loads(native.stdout)['args'], payload['args'])

    def test_cline_launcher_overrides_only_child_mcp_environment(self):
        asyncio.run(attach(self.root, self.project, 'alpha', skip_register=True))
        harnesses.install_mcp(self.root, self.project, 'cline', home=self.home)
        python = self.root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        atomic_write(python, '')
        atomic_write(self.root / 'hive.py', '')
        path = self.project / '.hivemind/hive.py'
        spec = importlib.util.spec_from_file_location('project_cli_fixture', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(sys, 'argv', [str(path), 'launch', 'cline', 'work']), \
                patch.object(module.shutil, 'which', return_value='cline-fixture'), \
                patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as child, \
                patch.dict(os.environ, {'CLINE_MCP_SETTINGS_PATH': 'existing-global-choice'}):
            self.assertEqual(module.main(), 0)
            self.assertEqual(os.environ['CLINE_MCP_SETTINGS_PATH'], 'existing-global-choice')
            self.assertEqual(child.call_args.kwargs['env']['CLINE_MCP_SETTINGS_PATH'], str(self.project / '.hivemind/clients/cline.json'))
            self.assertEqual(child.call_args.args[0], ['cline-fixture', 'work'])


if __name__ == '__main__':
    unittest.main()
