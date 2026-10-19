import base64
import asyncio
import getpass
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from unittest.mock import Mock, patch

import bootstrap
from hivemind import assisted, credentials, harnesses, setup
from hivemind.opencode_plugin import plugin_source
from hivemind.store import Hive, atomic_write


class HarnessSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Hive adapters ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'user'
        self.project = self.root / 'Project with spaces'
        self.home.mkdir()
        self.project.mkdir()
        atomic_write(self.root / 'hive.local.json', json.dumps({'offline': True,
            'projects': {'alpha': str(self.project), 'beta': str(self.root / 'Other')}}))
        self.hive = Hive(self.root)

    def args(self, *flags):
        return bootstrap.build_parser().parse_args([str(self.project), *flags])

    def test_all_json_adapters_preserve_other_servers_and_rerun_without_changes(self):
        for name in harnesses.HARNESS:
            if name in {'codex', 'grok'}:
                continue
            with self.subTest(name=name):
                target, boundary = harnesses.mcp_target(name, self.project, self.home, 'project')
                key = 'mcp' if name == 'opencode' else 'servers' if name == 'vscode' else 'mcpServers'
                other = {'command': 'existing-tool', 'args': ['keep this'], 'env': {'EXISTING': 'preserve'}}
                atomic_write(target, json.dumps({key: {'other': other}, 'unrelated': [1, 2]}))
                first = harnesses.install_mcp(self.root, self.project, name, home=self.home)
                body = json.loads(target.read_text())
                self.assertEqual(body[key]['other'], other)
                self.assertEqual(body['unrelated'], [1, 2])
                self.assertIn('--root', body[key]['hivemind'].get('args', body[key]['hivemind'].get('command')))
                self.assertTrue(first['changed'])
                self.assertFalse(harnesses.install_mcp(self.root, self.project, name, home=self.home)['changed'])

    def test_user_vscode_uses_documented_copilot_global_format(self):
        result = harnesses.install_mcp(self.root, self.project, 'vscode', scope='user', home=self.home)
        body = json.loads(Path(result['path']).read_text())
        self.assertIn('hivemind', body['mcpServers'])
        self.assertEqual(Path(result['path']), self.home / '.copilot/mcp-config.json')

    def test_cline_discovers_existing_extension_config(self):
        path = self.home / 'AppData/Roaming/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json'
        atomic_write(path, '{"mcpServers":{"other":{"command":"keep"}}}')
        result = harnesses.install_mcp(self.root, self.project, 'cline', scope='user', home=self.home)
        self.assertEqual(Path(result['path']), path)
        self.assertIn('other', json.loads(path.read_text())['mcpServers'])

    def test_jsonc_and_symlink_escapes_are_preserved(self):
        target = self.project / 'opencode.jsonc'
        original = '{\n // personal settings\n "mcp": {}\n}'
        target.write_text(original)
        with self.assertRaisesRegex(ValueError, 'JSONC'):
            harnesses.install_mcp(self.root, self.project, 'opencode', home=self.home)
        self.assertEqual(target.read_text(), original)
        outside = self.root / 'outside'
        outside.mkdir()
        try:
            (self.project / '.cursor').symlink_to(outside, target_is_directory=True)
        except OSError:
            return
        with self.assertRaisesRegex(ValueError, 'outside'):
            harnesses.install_mcp(self.root, self.project, 'cursor', home=self.home)
        self.assertEqual(list(outside.iterdir()), [])

    def test_windows_cmd_shell_is_not_commandcode(self):
        with patch('hivemind.harnesses.shutil.which', side_effect=lambda name:
                   'C:/Windows/System32/cmd.exe' if name == 'cmd' else None):
            self.assertIsNone(harnesses.executable('commandcode'))

    def test_instructions_preserve_existing_personality_and_have_one_managed_block(self):
        path = self.project / 'CLAUDE.md'
        path.write_text('My own project rules.\n')
        harnesses.install_instructions(self.root, self.project, 'claude', 'alpha')
        self.assertTrue(path.read_text().startswith('My own project rules.\n'))
        self.assertFalse(harnesses.install_instructions(self.root, self.project, 'claude', 'alpha')['changed'])
        self.assertEqual(path.read_text().count('<!-- HIVEMIND:BEGIN -->'), 1)

    def test_core_and_harness_adapter_share_one_idempotent_gemini_pointer(self):
        from hivemind.project import attach, prepare
        asyncio.run(attach(self.root, self.project, 'alpha', skip_register=True))
        harnesses.install_instructions(self.root, self.project, 'gemini', 'alpha')
        self.assertFalse(harnesses.install_instructions(self.root, self.project, 'antigravity', 'alpha')['changed'])
        _, changes, _ = prepare(self.root, self.project, 'alpha')
        self.assertNotIn(self.project / 'GEMINI.md', changes)

    def test_every_native_capture_adapter_is_idempotent_and_keeps_other_settings(self):
        for name in ('codex', 'grok', 'claude', 'commandcode', 'gemini', 'antigravity', 'cursor', 'opencode'):
            with self.subTest(name=name):
                first = harnesses.install_capture(self.root, name, self.project, self.home)
                self.assertTrue(first['changed'])
                path = Path(first['path'])
                if name != 'opencode':
                    body = json.loads(path.read_text())
                    body['unrelated'] = 'Keep my settings'
                    atomic_write(path, json.dumps(body))
                second = harnesses.install_capture(self.root, name, self.project, self.home)
                self.assertFalse(second['changed'])
                self.assertNotIn('OPENROUTER_API_KEY', path.read_text())
                if name == 'gemini':
                    self.assertEqual(json.loads(path.read_text())['hooks']['AfterAgent'][0]['hooks'][0]['timeout'], 5000)
                if name == 'antigravity':
                    self.assertIn('--event Stop', path.read_text())

    def test_one_client_failure_does_not_prevent_other_adapters(self):
        with patch('hivemind.harnesses.install_mcp', side_effect=[ValueError('fixture conflict'), {'status': 'configured'}]):
            result = harnesses.install(self.root, self.project, 'alpha', ['claude', 'cursor'], home=self.home)
        self.assertEqual(result['claude']['status'], 'needs-action')
        self.assertEqual(result['cursor']['status'], 'configured')
        self.assertTrue((self.project / 'CLAUDE.md').exists())

    def test_free_policy_is_per_project_and_off_overrides_wildcard(self):
        assisted.configure(self.root, ['*'], daily_limit=1000)
        assisted.configure_project(self.root, 'alpha', model='provider/small:free', daily_limit=14)
        assisted.configure_project(self.root, 'beta', mode='off')
        self.assertEqual(assisted.settings(self.root, 'alpha')['model'], 'provider/small:free')
        self.assertEqual(assisted.settings(self.root, 'alpha')['daily_limit'], 14)
        self.assertEqual(assisted.settings(self.root)['daily_limit'], 1000)
        self.assertTrue(assisted.enabled(self.root, 'alpha'))
        self.assertFalse(assisted.enabled(self.root, 'beta'))
        assisted.configure_project(self.root, 'beta', model='other/model:free', auto_run=False)
        self.assertEqual(assisted.settings(self.root, 'alpha')['model'], 'provider/small:free')
        with patch('hivemind.assisted.subprocess.Popen') as launched:
            assisted.kick(self.root, 'beta')
            launched.assert_not_called()

    def test_project_and_paid_budget_reservations_survive_failed_requests(self):
        assisted.configure_project(self.root, 'alpha', mode='cheap', model='provider/tiny', daily_limit=10,
                                   daily_usd=.02, prompt_price=.1, completion_price=.2)
        assisted.record(self.hive, 'alpha', 'claude', 'one', 'user', 'Keep useful progress')
        job = assisted._job(self.hive)
        policy = assisted.settings(self.root, 'alpha')
        with patch('hivemind.assisted.time.time', return_value=1000000):
            self.assertEqual(assisted._reserve(self.hive, job, policy), 0)
        with patch('hivemind.assisted.time.time', return_value=1000010):
            self.assertGreater(assisted._reserve(self.hive, job, policy), 0)
        with self.hive.connect() as connection:
            row = connection.execute('SELECT count(*),sum(charge) FROM assist_requests').fetchone()
        self.assertEqual(row[0], 1)
        self.assertAlmostEqual(row[1], .0105)
        with self.assertRaises(ValueError):
            assisted.configure_project(self.root, 'beta', model='provider/paid')
        with self.assertRaises(ValueError):
            assisted.configure_project(self.root, 'beta', mode='cheap', model='provider/paid')

    def test_cheap_inference_uses_price_caps_and_accounts_reported_cost(self):
        assisted.configure_project(self.root, 'alpha', mode='cheap', model='provider/tiny', auto_run=False,
                                   daily_usd=.1, prompt_price=.1, completion_price=.2)
        assisted.record(self.hive, 'alpha', 'claude', 'one', 'user', 'Keep useful progress')
        body = {}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size):
                return json.dumps({'choices': [{'message': {'content': '{"progress":"Pending work","memories":[]}'}}],
                                   'usage': {'cost': .0002}, 'model': 'provider/tiny'}).encode()
        class Opener:
            def open(self, request, timeout):
                body.update(json.loads(request.data))
                return Response()
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'fixture-key'}), \
                patch('urllib.request.build_opener', return_value=Opener()):
            result = assisted.run(self.hive, 'alpha', 1)
        self.assertEqual(result['processed'], 1)
        self.assertEqual(body['provider']['max_price'], {'prompt': .1, 'completion': .2})
        self.assertNotIn('tools', body)
        self.assertAlmostEqual(assisted.status(self.hive, 'alpha')['reserved_or_spent_usd_last_24h'], .0002)
        self.assertEqual(assisted.status(self.hive, 'beta')['requests_last_24h'], 0)

    def test_native_capture_opt_out_stops_existing_user_hooks_but_keeps_mcp_checkpoints(self):
        assisted.configure_project(self.root, 'alpha', auto_run=False)
        cfg = assisted.config(self.root)
        cfg['project_onboarding'] = {'alpha': {'capture': False}}
        atomic_write(self.root / 'hive.local.json', json.dumps(cfg))
        response = assisted.hook(self.root, 'codex', {'hook_event_name': 'UserPromptSubmit',
            'cwd': str(self.project), 'session_id': 'one', 'prompt': 'Private native activity'})
        self.assertFalse(response['saved'])
        session = self.hive.session_start('alpha', 'claude', 'Save explicit progress')['session']
        result = self.hive.session_checkpoint(session['id'], {'summary': 'An explicit progress checkpoint'}, 0)
        self.assertTrue(result['assisted_learning']['saved'])

    def test_private_key_roundtrip_and_environment_precedence_without_config_leak(self):
        with patch.dict(os.environ, {}, clear=True):
            credentials.save_key(self.root, 'fixture-private-key')
            self.assertEqual(credentials.get_key(self.root), 'fixture-private-key')
            self.assertTrue(credentials.key_status(self.root)['available'])
            if os.name == 'nt':
                stored = json.loads((self.root / 'runtime/secrets/openrouter.json').read_text())
                self.assertNotIn(b'fixture-private-key', base64.b64decode(stored['value']))
            os.environ['OPENROUTER_API_KEY'] = 'environment-fixture'
            self.assertEqual(credentials.get_key(self.root), 'environment-fixture')
        self.assertNotIn('fixture-private-key', (self.root / 'hive.local.json').read_text())
        self.assertEqual(list(self.project.rglob('*')), [])

    def test_guided_setup_asks_for_hidden_key_and_saves_project_taste(self):
        answers = iter(['claude,opencode', 'free', '', '25', 'yes', 'manual',
                        'An offline note manager', 'Keep explanations short'])
        with patch('hivemind.setup.inventory', return_value=[]):
            choices = setup.choose(self.root, 'alpha', self.args(), interactive=True,
                                   input_fn=lambda _: next(answers), output=io.StringIO())
        hidden = []
        def secret(prompt):
            hidden.append(prompt)
            return 'fixture-wizard-key'
        with patch.dict(os.environ, {}, clear=True), patch('hivemind.setup.install', return_value={}):
            setup.apply(self.root, self.project, 'alpha', choices, secret_fn=secret, output=io.StringIO())
        self.assertEqual(len(hidden), 1)
        self.assertEqual(assisted.settings(self.root, 'alpha')['daily_limit'], 25)
        self.assertFalse(assisted.settings(self.root, 'alpha')['auto_run'])
        note = self.hive.read_note('03-Projects/alpha/Preferences/onboarding-taste.md')['text']
        self.assertIn('Basis: user-stated', note)
        self.assertIn('Keep explanations short', note)
        self.assertNotIn('fixture-wizard-key', (self.root / 'hive.local.json').read_text())
        again = setup.choose(self.root, 'alpha', self.args(), interactive=True,
                             input_fn=lambda _: self.fail('Saved project should not reprompt'))
        self.assertEqual(again['harnesses'], ['claude', 'opencode'])
        self.assertEqual(again['model'], 'openrouter/free')

    def test_free_model_menu_accepts_a_number_and_keeps_custom_ids_available(self):
        from hivemind.openrouter import FREE_MODELS
        for answer, expected in [('2', FREE_MODELS[1][0]), ('provider/custom:free', 'provider/custom:free')]:
            with self.subTest(answer=answer):
                answers = iter(['none', 'free', answer, '25', 'yes', 'auto', '', ''])
                output = io.StringIO()
                with patch('hivemind.setup.inventory', return_value=[]):
                    choices = setup.choose(self.root, 'alpha', self.args(), interactive=True,
                        input_fn=lambda _: next(answers), output=output)
                self.assertEqual(choices['model'], expected)
                self.assertIn('6. LiquidAI', output.getvalue())

    def test_interactive_setup_always_prompts_even_with_a_stored_key(self):
        output = io.StringIO()
        secret = Mock(return_value='')
        with patch.dict(os.environ, {}, clear=True), patch('hivemind.setup.inventory', return_value=[]), \
                patch('hivemind.setup.install', return_value={}):
            credentials.save_key(self.root, 'fixture-existing-key')
            path = self.root / 'runtime/secrets/openrouter.json'
            before = path.read_bytes()
            choices = setup.choose(self.root, 'alpha', self.args('--learning', 'free'), interactive=True)
            setup.apply(self.root, self.project, 'alpha', choices, secret_fn=secret, output=output)
            secret.assert_called_once()
            self.assertIn('keep the existing key', secret.call_args.args[0])
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(credentials.get_key(self.root), 'fixture-existing-key')
        self.assertIn('private device storage', output.getvalue())
        self.assertNotIn('fixture-existing-key', output.getvalue())

    def test_key_prompt_accepts_replacement_and_reports_environment_override(self):
        output = io.StringIO()
        secret = Mock(return_value='fixture-new-key')
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'fixture-env-key'}, clear=True), \
                patch('hivemind.setup.inventory', return_value=[]), patch('hivemind.setup.install', return_value={}):
            choices = setup.choose(self.root, 'alpha', self.args('--learning', 'free'), interactive=True)
            setup.apply(self.root, self.project, 'alpha', choices, secret_fn=secret, output=output)
            secret.assert_called_once()
            self.assertEqual(credentials.get_key(self.root), 'fixture-env-key')
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(credentials.get_key(self.root), 'fixture-new-key')
        self.assertIn('OPENROUTER_API_KEY remains active', output.getvalue())
        for key in ['fixture-new-key', 'fixture-env-key']:
            self.assertNotIn(key, output.getvalue())
            self.assertNotIn(key, (self.root / 'hive.local.json').read_text())

    def test_missing_key_can_be_skipped_without_enabling_a_model_call(self):
        output = io.StringIO()
        secret = Mock(return_value='')
        with patch.dict(os.environ, {}, clear=True), patch('hivemind.setup.inventory', return_value=[]), \
                patch('hivemind.setup.install', return_value={}):
            choices = setup.choose(self.root, 'alpha', self.args('--learning', 'free'), interactive=True)
            setup.apply(self.root, self.project, 'alpha', choices, secret_fn=secret, output=output)
            secret.assert_called_once()
            self.assertIn('skip for now', secret.call_args.args[0])
            self.assertFalse(credentials.key_status(self.root)['available'])
            self.assertEqual(assisted.status(self.hive)['requests_last_24h'], 0)
        self.assertFalse((self.root / 'runtime/secrets/openrouter.json').exists())
        self.assertIn('activity remains queued', output.getvalue())

    def test_saved_project_still_shows_the_key_step_without_repeating_other_questions(self):
        assisted.configure_project(self.root, 'alpha', mode='free', auto_run=False)
        cfg = assisted.config(self.root)
        cfg['project_onboarding'] = {'alpha': {'harnesses': [], 'scope': 'project', 'capture': False}}
        atomic_write(self.root / 'hive.local.json', json.dumps(cfg))
        with patch('hivemind.setup.inventory', return_value=[]):
            choices = setup.choose(self.root, 'alpha', self.args(), interactive=True,
                input_fn=lambda _: self.fail('Saved project options should not reprompt'))
        self.assertTrue(choices['ask_key'])

    def test_key_prompt_is_suppressed_for_noninteractive_dry_run_and_no_prompt(self):
        for flags, interactive in [(('--no-prompt',), True), (('--dry-run',), True), ((), False)]:
            with self.subTest(flags=flags, interactive=interactive), patch('hivemind.setup.inventory', return_value=[]):
                choices = setup.choose(self.root, 'alpha', self.args('--learning', 'free', *flags),
                    interactive=interactive, input_fn=lambda _: self.fail('Unexpected prompt'))
                self.assertFalse(choices['ask_key'])
        with patch('hivemind.setup.inventory', return_value=[]):
            off = setup.choose(self.root, 'alpha', self.args('--learning', 'off'), interactive=True)
        self.assertFalse(off['ask_key'])

    def test_cheap_mode_also_has_the_explicit_key_step(self):
        with patch('hivemind.setup.inventory', return_value=[]):
            choices = setup.choose(self.root, 'alpha', self.args('--learning', 'cheap', '--model',
                'provider/tiny', '--daily-usd', '1', '--prompt-price', '.1', '--completion-price', '.2'),
                interactive=True)
        self.assertTrue(choices['ask_key'])

    def test_key_entry_never_falls_back_to_echoed_input(self):
        def unsafe_input(prompt):
            warnings.warn('Hidden input unavailable', getpass.GetPassWarning)
            self.fail('Must stop before reading a key without hidden input')
        before = (self.root / 'hive.local.json').read_bytes()
        with patch('hivemind.setup.inventory', return_value=[]), patch('hivemind.setup.install') as install:
            choices = setup.choose(self.root, 'alpha', self.args('--learning', 'free'), interactive=True)
            with self.assertRaises(getpass.GetPassWarning):
                setup.apply(self.root, self.project, 'alpha', choices, secret_fn=unsafe_input, output=io.StringIO())
            install.assert_not_called()
        self.assertEqual((self.root / 'hive.local.json').read_bytes(), before)
        self.assertFalse((self.root / 'runtime/secrets/openrouter.json').exists())

    def test_noninteractive_and_dry_run_do_not_prompt_for_keys_or_enable_learning(self):
        for flags in (('--no-prompt',), ('--dry-run',)):
            choices = setup.choose(self.root, 'alpha', self.args(*flags), interactive=True,
                                   input_fn=lambda _: self.fail('Unexpected prompt'))
            self.assertEqual(choices['mode'], 'off')
            self.assertFalse(choices['ask_key'])
        with self.assertRaises(ValueError):
            setup.choose(self.root, 'alpha', self.args('--set-key', '--no-prompt'), interactive=True)

    def test_native_payloads_record_expected_roles_and_redact(self):
        assisted.configure_project(self.root, 'alpha', auto_run=False)
        fixtures = [
            ('claude', {'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.project), 'session_id': 'claude1', 'prompt': 'Prefer direct answers'}, 'user'),
            ('gemini', {'hook_event_name': 'AfterAgent', 'cwd': str(self.project), 'session_id': 'gemini1', 'prompt_response': 'Still need tests'}, 'assistant'),
            ('cursor', {'hook_event_name': 'afterAgentResponse', 'workspace_roots': [str(self.project)], 'conversation_id': 'cursor1', 'generation_id': 'g1', 'text': 'Completed an edit'}, 'assistant'),
            ('antigravity', {'hook_event_name': 'PostToolUse', 'workspacePaths': [str(self.project)], 'conversationId': 'agy1', 'stepIdx': 3, 'toolCall': {'name': 'run_command', 'args': {'CommandLine': 'password=hidden-secret'}}}, 'tool'),
            ('commandcode', {'hook_event_name': 'Stop', 'cwd': str(self.project), 'session_id': 'cmd1'}, 'tool'),
        ]
        for name, payload, role in fixtures:
            with self.subTest(name=name):
                self.assertTrue(assisted.hook(self.root, name, payload)['saved'])
                rows = assisted.read_events(self.hive, 'alpha')
                self.assertEqual(rows[0]['role'], role)
                self.assertNotIn('hidden-secret', rows[0]['text'])

    @unittest.skipUnless(shutil.which('node'), 'Node needed for OpenCode plugin integration fixture')
    def test_real_opencode_plugin_batches_text_and_ignores_reasoning(self):
        assisted.configure_project(self.root, 'alpha', auto_run=False)
        source = plugin_source(self.root)
        encoded = base64.b64encode(source.encode()).decode()
        script = '''
const {HiveMind} = await import("data:text/javascript;base64," + process.argv[1]);
const hooks = await HiveMind({directory: process.argv[2]});
const event = async (type, properties) => hooks.event({event: {type, properties}});
await event("message.updated", {info: {id:"u1", sessionID:"session1", role:"user"}});
await event("message.part.updated", {part: {id:"p1", messageID:"u1", sessionID:"session1", type:"text", text:"Prefer concise explanations"}});
await event("message.part.updated", {part: {id:"r1", messageID:"u1", sessionID:"session1", type:"reasoning", text:"SECRET REASONING"}});
await event("message.updated", {info: {id:"a1", sessionID:"session1", role:"assistant"}});
await event("message.part.updated", {part: {id:"p2", messageID:"a1", sessionID:"session1", type:"text", text:"Work remains"}});
await event("session.idle", {sessionID:"session1"});
'''
        result = subprocess.run(['node', '--input-type=module', '-e', script, encoded, str(self.project)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = assisted.read_events(self.hive, 'alpha')
        self.assertEqual({event['role'] for event in events}, {'user', 'assistant'})
        self.assertFalse(any('SECRET REASONING' in event['text'] for event in events))
        self.assertEqual(assisted.status(self.hive)['requests_last_24h'], 0)


if __name__ == '__main__':
    unittest.main()
