"""Deterministic continuity and fewer requests, without vendor agent sessions."""
import io
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hivemind import assisted, assisted_worker, handoff
from hivemind.store import Hive, atomic_write

ROOT = Path(__file__).resolve().parents[1]


class EfficientHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Hive efficient ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hive = Hive(self.root)
        atomic_write(self.root / 'hive.local.json', json.dumps({'offline': True,
            'projects': {'alpha': str(self.root), 'beta': str(self.root / 'other')}}))
        assisted.configure(self.root, ['alpha', 'beta'], auto_run=False)
        self.key = patch.dict(os.environ, {'OPENROUTER_API_KEY': 'fixture-only'})
        self.key.start()
        self.addCleanup(self.key.stop)

    def record(self, text, role='user', native='one', captured_at=None, project='alpha'):
        return assisted.record(self.hive, project, 'codex', native, role, text, captured_at=captured_at)

    def fake(self, model, events, prior, key):
        output = assisted.Extraction(progress='Changed the parser; regression test remains.', memories=[])
        output._usage = {'prompt_tokens': 100, 'completion_tokens': 25, 'total_tokens': 125, 'cached_tokens': 40}
        return output, 'fixture/free'

    def checkpoint(self):
        session = self.hive.session_start('alpha', 'codex', 'Fix the parser')['session']
        result = self.hive.session_checkpoint(session['id'], {'summary': 'Parser handles escaped quotes.',
            'completed': ['Fixed escaped quotes'], 'changed_files': ['parser.py'],
            'verification': ['Parser regression passed'], 'blockers': ['Release review pending'],
            'next_steps': ['Review and run the integration test'], 'status': 'active'}, 0)
        return result['session']

    def test_lifecycle_noise_and_structured_checkpoints_never_need_inference(self):
        for _ in range(50):
            self.record('{"event":"SessionEnd","reason":"other"}', 'tool')
        self.checkpoint()
        with patch('hivemind.assisted.infer', side_effect=AssertionError('noise must not use a model')):
            result = assisted.run(self.hive)
        self.assertEqual(result['model_calls'], 0)
        self.assertEqual(assisted.status(self.hive)['local_only_events'], {'LOCAL:checkpoint': 1, 'LOCAL:noise': 50})

    def test_hivemind_tool_results_do_not_reenter_the_learning_loop(self):
        for name in ('mcp__hivemind__session_checkpoint', 'mcp_hivemind_memory_search', 'MCP:hivemind:note_read', 'session_resume'):
            result = assisted.hook(self.root, 'claude', {'hook_event_name': 'PostToolUse', 'cwd': str(self.root),
                'session_id': 'memory-loop', 'tool_name': name, 'tool_response': {'text': 'Previously learned preferences'}})
            self.assertFalse(result['saved'])
        self.assertEqual(assisted.read_events(self.hive, 'alpha'), [])
        old = {'role': 'tool', 'text': json.dumps({'tool_name': 'mcp__hivemind__memory_write', 'tool_response': 'old memory'})}
        self.assertFalse(assisted.useful(old))

    def test_whole_turn_and_checkpoint_use_one_request_after_local_coalescing(self):
        self.record('Please fix the parser and keep the change small.')
        self.record('{"event":"PostToolUse","tool_name":"shell","tool_response":"Regression passed"}', 'tool')
        self.record('Parser updated; integration test remains.', 'assistant')
        self.checkpoint()
        self.record('{"event":"SessionEnd","reason":"other"}', 'tool')
        result = assisted.run(self.hive, infer_fn=self.fake)
        self.assertEqual(result['model_calls'], 1)
        status = assisted.status(self.hive, 'alpha')
        self.assertEqual(status['usage_last_24h']['prompt_tokens'], 100)
        self.assertEqual(status['usage_last_24h']['completion_tokens'], 25)
        self.assertEqual(status['usage_last_24h']['total_tokens'], 125)
        self.assertEqual(status['usage_last_24h']['cached_tokens'], 40)
        self.assertEqual(status['usage_last_24h']['requests_with_token_usage'], 1)
        self.assertEqual(assisted.status(self.hive, 'beta')['usage_last_24h']['total_tokens'], 0)

    def test_spool_replay_retains_original_time_and_deduplicates_before_drain(self):
        event = {'project': 'alpha', 'agent': 'codex', 'native_session': 'spooled', 'role': 'user',
                 'text': 'Earlier user request', 'source_id': 'same-event', 'captured_at': 1000000.25}
        first = assisted.spool(self.root, event)
        second = assisted.spool(self.root, event)
        self.assertTrue(first['saved'] and second['duplicate'])
        self.assertEqual(len(list((self.root / 'runtime/assisted-spool').glob('*.json'))), 1)
        assisted.drain(self.hive)
        event = assisted.read_events(self.hive, 'alpha')[0]
        self.assertEqual(event['captured_at'], 1000000.25)
        self.assertEqual(assisted.recent(self.hive, 'alpha', after_timestamp=1000001), [])

    def test_bad_spool_is_quarantined_and_does_not_poison_good_events(self):
        atomic_write(self.root / 'runtime/assisted-spool/000-bad.json', '{broken')
        assisted.spool(self.root, {'project': 'alpha', 'agent': 'codex', 'native_session': 'good',
                                 'role': 'user', 'text': 'Fix parser next', 'source_id': 'good'})
        self.assertEqual(assisted.drain(self.hive), 1)
        self.assertEqual(assisted.status(self.hive)['quarantined_events'], 1)
        self.assertEqual(assisted.read_events(self.hive, 'alpha')[0]['text'], 'Fix parser next')

    def test_dead_worker_lease_is_recovered_without_waiting_for_timeout(self):
        self.record('Continue parser implementation')
        job = assisted._job(self.hive)
        with self.hive.connect(write=True) as c:
            c.execute("UPDATE assist_jobs SET status='running' WHERE id=?", (job['id'],))
            c.execute("INSERT INTO assist_state VALUES ('worker',?)", (json.dumps({'token': 'dead', 'pid': 123456789, 'until': time.time() + 200}),))
        with patch('hivemind.assisted_worker._alive', return_value=False):
            result = assisted.run(self.hive, infer_fn=self.fake)
        self.assertEqual(result['processed'], 1)

    def test_empty_or_delayed_summary_cannot_replace_newer_progress(self):
        self.record('New parser behavior implemented', captured_at=2000000)
        assisted.run(self.hive, limit=1, infer_fn=self.fake)
        self.record('Earlier event arrived late', captured_at=1000000)
        with self.hive.connect(write=True) as c:
            c.execute('UPDATE assist_requests SET created=created-10')
        def older(*args):
            return assisted.Extraction(progress='Stale older work', memories=[]), 'fixture/free'
        assisted.run(self.hive, limit=1, infer_fn=older)
        self.assertNotIn('Stale', assisted.latest(self.hive, 'alpha')['summary'])
        self.record('{"event":"SessionEnd"}', 'tool')
        self.assertEqual(assisted.run(self.hive, infer_fn=older)['model_calls'], 0)

    def test_usage_is_recorded_even_when_provider_json_is_invalid(self):
        self.record('Useful work is pending')
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size):
                return json.dumps({'id': 'generation-1', 'model': 'fixture/free',
                    'choices': [{'message': {'content': 'invalid SECRET RESPONSE'}, 'finish_reason': 'length'}],
                    'usage': {'prompt_tokens': 321, 'completion_tokens': 80, 'total_tokens': 401, 'cost': 0}}).encode()
        with patch('urllib.request.build_opener', return_value=SimpleNamespace(open=lambda *a, **k: Response())):
            result = assisted.run(self.hive, limit=1)
        self.assertEqual(result['processed'], 0)
        self.assertEqual(assisted.status(self.hive)['usage_last_24h']['total_tokens'], 401)
        self.assertNotIn('SECRET RESPONSE', result['error'])
        self.assertIsNone(assisted.latest(self.hive, 'alpha'))

    def test_request_uses_smaller_output_cap_and_omits_event_timestamps(self):
        wire = {}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size):
                return b'{"choices":[{"message":{"content":"{\\"progress\\":\\"Pending\\",\\"memories\\":[]}"}}],"usage":{"cost":0}}'
        def open_request(request, timeout):
            wire.update(json.loads(request.data))
            return Response()
        with patch('urllib.request.build_opener', return_value=SimpleNamespace(open=open_request)):
            assisted.infer('openrouter/free', [{'id': 1, 'role': 'user', 'text': 'Fix parser', 'created': 'long timestamp', 'captured_at': 100}], '', 'fixture')
        self.assertEqual(wire['max_tokens'], 1800)
        self.assertEqual(set(json.loads(wire['messages'][1]['content'])['events'][0]), {'id', 'role', 'text'})

    def test_burst_schedules_one_worker_and_dead_ticket_can_be_replaced(self):
        with patch('hivemind.assisted_worker.subprocess.Popen', return_value=SimpleNamespace(pid=os.getpid())) as launch:
            self.assertTrue(assisted_worker.schedule(self.root, False)['scheduled'])
            self.assertFalse(assisted_worker.schedule(self.root, True)['scheduled'])
            self.assertFalse(assisted_worker.schedule(self.root, True)['scheduled'])
            self.assertEqual(launch.call_count, 1)
            with patch('hivemind.assisted_worker._alive', return_value=False):
                self.assertTrue(assisted_worker.schedule(self.root)['scheduled'])
            self.assertEqual(launch.call_count, 2)

    def test_background_coalesces_prompt_and_reply_and_excludes_manual_projects(self):
        assisted.configure_project(self.root, 'alpha', auto_run=True)
        self.record('Fix parser carefully')
        self.record('Other project should remain manual', project='beta')
        atomic_write(self.root / 'runtime/assisted-worker.json', json.dumps({'token': 'fixture'}))
        atomic_write(self.root / 'runtime/assisted-wake.json', json.dumps({'id': 'prompt', 'boundary': False}))
        now = [1000000.0]
        replied = [False]
        def sleep(seconds):
            now[0] += seconds
            if now[0] >= 1000005 and not replied[0]:
                self.record('Parser fixed; tests remain', 'assistant')
                atomic_write(self.root / 'runtime/assisted-wake.json', json.dumps({'id': 'reply', 'boundary': True}))
                replied[0] = True
        with patch('hivemind.assisted.time.time', side_effect=lambda: now[0]):
            result = assisted_worker.background(self.hive, 'fixture', infer_fn=self.fake,
                                                clock=lambda: now[0], sleep=sleep)
        self.assertEqual(result['model_calls'], 1)
        self.assertLess(now[0], 1000060)
        self.assertEqual(assisted.status(self.hive)['jobs'].get('done'), 1)
        self.assertIsNone(assisted.latest(self.hive, 'beta'))

    def test_new_work_during_inference_gets_its_own_quiet_window(self):
        assisted.configure_project(self.root, 'alpha', auto_run=True)
        self.record('First meaningful task')
        atomic_write(self.root / 'runtime/assisted-worker.json', '{"token":"fixture"}')
        atomic_write(self.root / 'runtime/assisted-wake.json', '{"id":"first","boundary":true}')
        now, starts = [1000000.0], []
        def infer(*args):
            starts.append(now[0])
            if len(starts) == 1:
                now[0] += 4
                self.record('New user direction during inference')
                atomic_write(self.root / 'runtime/assisted-wake.json', '{"id":"second","boundary":true}')
            return self.fake(*args)
        def sleep(seconds): now[0] += seconds
        with patch('hivemind.assisted.time.time', side_effect=lambda: now[0]):
            report = assisted_worker.background(self.hive, 'fixture', infer_fn=infer, clock=lambda: now[0], sleep=sleep)
        self.assertEqual(report['model_calls'], 2)
        self.assertGreaterEqual(starts[1] - starts[0], 19)

    def test_prompt_only_activity_waits_for_maximum_delay(self):
        assisted.configure_project(self.root, 'alpha', auto_run=True)
        cfg = assisted.config(self.root)
        cfg['assisted_learning']['max_delay_seconds'] = 30
        atomic_write(self.root / 'hive.local.json', json.dumps(cfg))
        self.record('Preserve this longer-running request')
        atomic_write(self.root / 'runtime/assisted-worker.json', '{"token":"fixture"}')
        atomic_write(self.root / 'runtime/assisted-wake.json', '{"id":"prompt","boundary":false}')
        now, starts = [1000000.0], []
        def infer(*args):
            starts.append(now[0])
            return self.fake(*args)
        def sleep(seconds): now[0] += seconds
        with patch('hivemind.assisted.time.time', side_effect=lambda: now[0]):
            assisted_worker.background(self.hive, 'fixture', infer_fn=infer, clock=lambda: now[0], sleep=sleep)
        self.assertEqual(starts, [1000030.0])

    def test_callback_at_worker_exit_is_not_stranded(self):
        atomic_write(self.root / 'runtime/assisted-worker.json', '{"token":"fixture"}')
        atomic_write(self.root / 'runtime/assisted-wake.json', '{"id":"first","boundary":true}')
        original_guard = assisted_worker._guard
        @contextmanager
        def guard(folder):
            with original_guard(folder) as locked:
                yield locked
                # Callback lands after ticket removal but before the lock release.
                self.record('New work at the exit boundary')
                atomic_write(folder / 'assisted-wake.json', '{"id":"late","boundary":true}')
        now = [1000000.0]
        def sleep(seconds): now[0] += seconds
        with patch('hivemind.assisted_worker._guard', side_effect=guard), \
                patch('hivemind.assisted_worker.schedule', return_value={'scheduled': True}) as schedule:
            assisted_worker.background(self.hive, 'fixture', clock=lambda: now[0], sleep=sleep)
        schedule.assert_called_once()
        self.assertEqual(assisted.read_events(self.hive, 'alpha')[0]['text'], 'New work at the exit boundary')

    def test_recent_activity_keeps_user_direction_through_a_tool_flood(self):
        self.record('Keep the public API unchanged')
        for number in range(90):
            self.record(json.dumps({'tool_name': 'read', 'tool_input': {'path': f'file-{number}.py'}}), 'tool')
        self.record('{"event":"Interrupt","reason":"usage_limit_exhausted"}', 'tool')
        rows = assisted.recent(self.hive, 'alpha')
        self.assertTrue(any(row['role'] == 'user' for row in rows))
        # Lifecycle reasons remain local handoff evidence and do not need inference.
        issue = assisted.read_events(self.hive, 'alpha')[0]
        self.assertFalse(assisted.useful(issue))
        self.assertTrue(assisted.lifecycle_issue(issue))

    def test_unknown_token_usage_is_reported_as_unknown(self):
        self.record('Meaningful work without provider usage')
        def infer(*args):
            return assisted.Extraction(progress='Work pending', memories=[]), 'fixture/free'
        assisted.run(self.hive, infer_fn=infer)
        status = assisted.status(self.hive)
        self.assertEqual(status['requests_missing_token_usage'], 1)
        self.assertEqual(status['usage_last_24h']['requests_with_token_usage'], 0)

    def test_synthetic_immediate_trace_four_requests_becomes_one_coalesced_request(self):
        counts = []
        for immediate in (True, False):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                hive = Hive(root)
                atomic_write(root / 'hive.local.json', json.dumps({'offline': True, 'projects': {'alpha': folder}}))
                assisted.configure(root, ['alpha'], auto_run=False)
                now = [1000000.0]
                calls = 0
                stages = [('user', 'Keep parser changes focused', 'turn'),
                          ('assistant', 'Parser updated; tests remain', 'turn'),
                          ('checkpoint', '{"summary":"Parser changed","next_steps":["Run tests"]}', 'checkpoint'),
                          ('tool', '{"event":"SessionEnd","reason":"other"}', 'turn')]
                # Reproduce the previous immediate/no-noise-gate behavior as a
                # controlled baseline; this is not a production savings claim.
                with patch('hivemind.assisted.time.time', side_effect=lambda: now[0]):
                    for role, text, native in stages:
                        assisted.record(hive, 'alpha', 'codex', native, role, text)
                        if immediate:
                            with patch('hivemind.assisted.useful', return_value=True):
                                calls += assisted.run(hive, infer_fn=self.fake)['model_calls']
                        now[0] += 10
                    if not immediate:
                        calls += assisted.run(hive, infer_fn=self.fake)['model_calls']
                counts.append(calls)
        self.assertEqual(counts, [4, 1])

    def test_unexpected_charge_pauses_free_inference_until_explicit_review(self):
        self.record('Useful parser work')
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size):
                return b'{"choices":[],"usage":{"cost":0.01,"prompt_tokens":50,"completion_tokens":0,"total_tokens":50}}'
        with patch('urllib.request.build_opener', return_value=SimpleNamespace(open=lambda *a, **k: Response())):
            report = assisted.run(self.hive, limit=1)
        self.assertEqual(report['model_calls'], 1)
        status = assisted.status(self.hive)
        self.assertEqual(status['jobs'], {'failed': 1})
        self.assertEqual(len(status['billing_holds']), 1)
        self.record('More useful work')
        self.assertEqual(assisted.run(self.hive, infer_fn=self.fake)['model_calls'], 0)
        assisted.retry(self.hive, status['problem_jobs'][0]['id'])
        self.assertEqual(assisted.status(self.hive)['billing_holds'], [])

    def test_real_background_process_records_without_a_key_or_model_call(self):
        assisted.configure_project(self.root, 'alpha', auto_run=True)
        cfg = assisted.config(self.root)
        cfg['assisted_learning'].update(debounce_seconds=0, max_delay_seconds=1)
        atomic_write(self.root / 'hive.local.json', json.dumps(cfg))
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': ''}):
            assisted.hook(self.root, 'codex', {'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.root),
                'session_id': 'real-process', 'prompt': 'Preserve a compact parser handoff'})
        path = self.root / 'runtime/assisted-worker.json'
        until = time.monotonic() + 12
        while path.exists() and time.monotonic() < until:
            time.sleep(.1)
        self.assertFalse(path.exists(), 'Finite worker did not clean up its ticket')
        status = assisted.status(self.hive)
        self.assertEqual(status['requests_last_24h'], 0)
        self.assertEqual(status['jobs'], {'queued': 1})
        self.assertEqual(assisted.read_events(self.hive, 'alpha')[0]['text'], 'Preserve a compact parser handoff')

    def test_unavailable_model_still_has_a_source_backed_local_handoff(self):
        self.record('Implement parser compatibility next')
        def fail(*args): raise RuntimeError('OpenRouter HTTP 503')
        self.assertEqual(assisted.run(self.hive, infer_fn=fail)['processed'], 0)
        packet = handoff.build(self.hive, 'alpha')
        self.assertIsNone(packet['checkpoint'])
        self.assertIn('Implement parser compatibility next', handoff.markdown(packet))
        self.assertIn('unverified', handoff.markdown(packet))

    def test_context_surfaces_new_local_work_during_outage_without_model_calls(self):
        source = self.checkpoint()
        assisted.spool(self.root, {'project': 'alpha', 'agent': 'grok', 'native_session': 'new-harness',
            'role': 'user', 'text': 'Keep parser public names unchanged.', 'source_id': 'new-direction',
            'captured_at': source['checkpoint_timestamp'] + .1})
        with patch('hivemind.assisted.infer', side_effect=AssertionError('context must not infer')):
            packet = self.hive.context(project='alpha', agent='grok', budget_tokens=1000)
        self.assertIn('local_activity', packet)
        self.assertTrue(packet['local_activity']['unverified'])
        self.assertIn('Keep parser public names unchanged.', packet['local_activity']['events'][0]['text'])
        self.assertLessEqual(packet['budget']['estimated_tokens'], 1000)

    def test_tool_payload_keeps_paths_and_failure_tail_without_whole_file(self):
        assisted.hook(self.root, 'claude', {'hook_event_name': 'PostToolUse', 'cwd': str(self.root),
            'session_id': 'one', 'tool_name': 'shell', 'tool_input': {'command': 'python -m unittest'},
            'tool_response': 'START\n' + 'file contents ' * 2000 + '\nFAILED: parser.py:42'})
        event = assisted.read_events(self.hive, 'alpha')[0]
        self.assertLessEqual(len(event['text']), 1800)
        self.assertIn('FAILED: parser.py:42', event['text'])
        self.assertIn('python -m unittest', event['text'])

    def test_clean_handoff_preserves_checkpoint_and_works_without_api_key(self):
        source = self.checkpoint()
        self.record('User wants integration test next', captured_at=source['checkpoint_timestamp'] + .1)
        self.record('Other project secret', project='beta')
        self.hive.session_start('alpha', 'grok', 'Continue the parser work')
        with patch.dict(os.environ, {}, clear=True), patch('hivemind.assisted.infer', side_effect=AssertionError('handoff is deterministic')):
            packet = handoff.build(self.hive, 'alpha')
        self.assertEqual(packet['checkpoint']['session'], source['id'])
        self.assertIn('Parser regression passed', packet['checkpoint']['verification'])
        self.assertIn('Review and run the integration test', packet['checkpoint']['next_steps'])
        self.assertIn('Release review pending', packet['checkpoint']['blockers'])
        self.assertIn('parser.py', packet['checkpoint']['changed_files'])
        self.assertEqual(packet['recent_activity'][0]['text'], 'User wants integration test next')
        self.assertNotIn('Other project secret', json.dumps(packet))
        self.assertIn('unverified', handoff.markdown(packet))

    def test_handoff_uses_capture_time_not_replay_time_with_same_second_precision(self):
        source = self.checkpoint()
        stamp = source['checkpoint_timestamp']
        for text, captured_at in (('Old event replayed late', stamp - .1), ('New event same second', stamp + .1)):
            assisted.spool(self.root, {'project': 'alpha', 'agent': 'codex', 'native_session': 'raw',
                'role': 'user', 'text': text, 'source_id': text, 'captured_at': captured_at})
        packet = handoff.build(self.hive, 'alpha')
        self.assertEqual([event['text'] for event in packet['recent_activity']], ['New event same second'])

    def test_checkpoint_hides_older_ai_context_and_handoff_budget_is_honest(self):
        self.record('Earlier work on the parser', captured_at=1000000)
        assisted.run(self.hive, infer_fn=self.fake)
        self.checkpoint()
        self.assertNotIn('derived_progress', self.hive.context(project='alpha'))
        for budget in (700, 1200):
            result = handoff.build(self.hive, 'alpha', budget)
            self.assertLessEqual(len(json.dumps(result, ensure_ascii=False, indent=2).encode()), budget * 4)
            self.assertLessEqual(len(handoff.markdown(result).encode()), budget * 4)
            self.assertLessEqual(result['budget']['estimated_tokens'], budget)

    def test_cli_handoff_is_readable_markdown_not_quoted_json(self):
        self.checkpoint()
        result = subprocess.run([sys.executable, str(ROOT / 'hive.py'), '--root', str(self.root), 'handoff', 'alpha'],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith('# Handoff: alpha'))
        self.assertIn('Reported checks:', result.stdout)


if __name__ == '__main__':
    unittest.main()
