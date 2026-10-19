"""Exercise real request construction and durable retries using a fake HTTP transport."""
from email.utils import formatdate
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

from hivemind import assisted, assisted_worker
from hivemind.openrouter import FREE_MODELS, MAX_ATTEMPTS, model_pool, retry_after
from hivemind.store import Hive, atomic_write


class Response:
    def __init__(self, body, headers=None):
        self.body, self.headers = body, headers or {}

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, size): return json.dumps(self.body).encode()


class OpenRouterTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='Hive OpenRouter ')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.hive = Hive(self.root)
        atomic_write(self.root / 'hive.local.json', json.dumps({'offline': True,
            'projects': {'alpha': str(self.root), 'beta': str(self.root / 'beta')}}))
        assisted.configure(self.root, ['alpha', 'beta'], auto_run=False)
        self.now = 1000000.0
        for mock in (patch.dict(os.environ, {'OPENROUTER_API_KEY': 'fixture-only'}),
                     patch('hivemind.assisted.time.time', side_effect=lambda: self.now),
                     patch('hivemind.openrouter.random.uniform', return_value=0)):
            mock.start()
            self.addCleanup(mock.stop)
        self.opener = Mock()
        mock = patch('urllib.request.build_opener', return_value=self.opener)
        mock.start()
        self.addCleanup(mock.stop)

    def record(self, project='alpha', session='native'):
        return assisted.record(self.hive, project, 'codex', session, 'user', 'Keep progress reports concise.')

    def success(self, model=None, content=None):
        return Response({'choices': [{'message': {'content': content if content is not None else
            '{"progress":"Preserved the current task","memories":[]}'}}],
            'model': model or FREE_MODELS[1][0],
            'usage': {'cost': 0, 'prompt_tokens': 40, 'completion_tokens': 10, 'total_tokens': 50}})

    def http_error(self, code=429, delay=None):
        return urllib.error.HTTPError(assisted.ENDPOINT, code, 'PRIVATE PROVIDER BODY',
            {'Retry-After': str(delay)} if delay is not None else {}, io.BytesIO(b'private data'))

    def wire(self, index=0):
        return json.loads(self.opener.open.call_args_list[index].args[0].data)

    def test_free_pool_routes_in_order_and_accounts_for_actual_fallback_model(self):
        self.record()
        actual = FREE_MODELS[3][0]
        self.opener.open.return_value = self.success(actual)
        report = assisted.run(self.hive, limit=1)
        self.assertEqual(report['processed'], 1)
        body = self.wire()
        self.assertEqual(body['models'], [ident for ident, _ in FREE_MODELS])
        self.assertNotIn('model', body)
        self.assertEqual(body['provider']['max_price'], {'prompt': 0, 'completion': 0})
        self.assertTrue(body['provider']['require_parameters'])
        self.assertTrue(body['provider']['allow_fallbacks'])
        self.assertEqual(body['response_format']['type'], 'json_schema')
        with self.hive.connect() as c:
            row = c.execute('SELECT model,total_tokens FROM assist_requests').fetchone()
            self.assertEqual(tuple(row), (actual, 50))
            self.assertEqual(c.execute('SELECT model FROM assist_summaries').fetchone()[0], actual)

    def test_custom_primary_and_fallbacks_are_free_validated_and_deduplicated(self):
        chosen = FREE_MODELS[2][0]
        pool = model_pool(chosen, True)
        self.assertEqual(pool[0], chosen)
        self.assertEqual(len(pool), 6)
        self.assertEqual(model_pool(chosen, True, []), [chosen])
        self.assertEqual(model_pool(chosen, True, [chosen, chosen]), [chosen])
        for bad in (['provider/paid'], 'openrouter/free', [None], ['openrouter/free'] * 7):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                model_pool(chosen, True, bad)
        cfg = assisted.config(self.root)
        cfg['assisted_learning']['fallback_models'] = ['provider/paid']
        atomic_write(self.root / 'hive.local.json', json.dumps(cfg))
        with self.assertRaises(ValueError):
            assisted.run(self.hive)
        self.opener.open.assert_not_called()

    def test_cheap_mode_keeps_its_explicit_model_and_price_ceilings(self):
        assisted.configure_project(self.root, 'alpha', mode='cheap', model='provider/tiny',
            daily_usd=1, prompt_price=.1, completion_price=.2, auto_run=False)
        self.record()
        self.opener.open.return_value = self.success('provider/tiny')
        assisted.run(self.hive, limit=1)
        body = self.wire()
        self.assertEqual(body['model'], 'provider/tiny')
        self.assertNotIn('models', body)
        self.assertEqual(body['provider']['max_price'], {'prompt': .1, 'completion': .2})

    def test_throttle_delay_survives_restart_and_blocks_other_projects(self):
        self.record()
        self.opener.open.side_effect = [self.http_error(delay=9), self.success()]
        first = assisted.run(self.hive, limit=1)
        self.assertEqual(first['retry_after'], 9)
        self.now += 1
        self.record('beta')
        held = assisted.run(Hive(self.root), project='beta', limit=1)
        self.assertEqual(held['model_calls'], 0)
        self.assertEqual(held['retry_after'], 8)
        self.assertEqual(self.opener.open.call_count, 1)
        self.assertEqual(assisted.status(self.hive)['device_requests_last_24h'], 1)
        self.now += 8
        second = assisted.run(Hive(self.root), project='alpha', limit=1)
        self.assertEqual(second['processed'], 1)
        self.assertEqual(self.opener.open.call_count, 2)
        self.assertEqual(self.wire(1)['models'][0], FREE_MODELS[1][0])

    def test_three_failed_requests_stop_without_losing_events(self):
        self.record()
        self.opener.open.side_effect = [self.http_error(503) for _ in range(MAX_ATTEMPTS)]
        waits = []
        for _ in range(MAX_ATTEMPTS):
            report = assisted.run(Hive(self.root), limit=1)
            waits.append(report['retry_after'])
            self.now += (report['retry_after'] or 0) + .1
        self.assertEqual(waits, [5, 10, None])
        self.assertEqual(assisted.status(self.hive)['jobs'], {'failed': 1})
        self.assertEqual(assisted.status(self.hive)['requests_last_24h'], 3)
        self.assertEqual(len(assisted.read_events(self.hive, 'alpha')), 1)
        self.now += 60
        self.assertEqual(assisted.run(self.hive)['model_calls'], 0)
        self.assertEqual(self.opener.open.call_count, 3)

    def test_short_throttle_retries_in_the_bounded_background_worker(self):
        assisted.configure_project(self.root, 'alpha', auto_run=True)
        self.record()
        atomic_write(self.root / 'runtime/assisted-worker.json', '{"token":"fixture"}')
        atomic_write(self.root / 'runtime/assisted-wake.json', '{"id":"first","boundary":true}')
        self.opener.open.side_effect = [self.http_error(delay=7), self.success()]
        def sleep(seconds): self.now += seconds
        report = assisted_worker.background(self.hive, 'fixture', clock=lambda: self.now, sleep=sleep)
        self.assertEqual(report['processed'], 1)
        self.assertEqual(report['model_calls'], 2)
        self.assertEqual(self.now, 1000022)
        self.assertEqual(assisted.status(self.hive)['jobs'], {'done': 1})

    def test_long_server_delay_defers_without_sleeping_through_it(self):
        assisted.configure_project(self.root, 'alpha', auto_run=True)
        self.record()
        atomic_write(self.root / 'runtime/assisted-worker.json', '{"token":"fixture"}')
        atomic_write(self.root / 'runtime/assisted-wake.json', '{"id":"first","boundary":true}')
        self.opener.open.side_effect = [self.http_error(delay=120), self.success()]
        def sleep(seconds): self.now += seconds
        report = assisted_worker.background(self.hive, 'fixture', clock=lambda: self.now, sleep=sleep)
        self.assertEqual(report['model_calls'], 1)
        self.assertEqual(self.now, 1000015)
        self.assertEqual(assisted.status(self.hive)['retry_after'], 120)
        self.now += 120
        self.assertEqual(assisted.run(Hive(self.root), limit=1)['processed'], 1)

    def test_every_retry_still_obeys_the_shared_request_budget(self):
        assisted.configure(self.root, ['alpha', 'beta'], daily_limit=1, auto_run=False)
        self.record()
        self.opener.open.side_effect = self.http_error()
        report = assisted.run(self.hive, limit=1)
        self.now += report['retry_after'] + 1
        report = assisted.run(Hive(self.root), limit=1)
        self.assertEqual(report['model_calls'], 0)
        self.assertEqual(self.opener.open.call_count, 1)
        self.assertEqual(assisted.status(self.hive)['remaining_local_budget'], 0)

    def test_invalid_json_rotates_the_pool_on_the_next_attempt(self):
        self.record()
        self.opener.open.side_effect = [self.success(content='invalid JSON'), self.success()]
        first = assisted.run(self.hive, limit=1)
        self.assertEqual(first['processed'], 0)
        self.now += first['retry_after']
        self.assertEqual(assisted.run(self.hive, limit=1)['processed'], 1)
        self.assertNotEqual(self.wire()['models'][0], self.wire(1)['models'][0])
        self.assertEqual(assisted.status(self.hive)['usage_last_24h']['total_tokens'], 100)

    def test_credentials_and_invalid_request_errors_stop_after_one_attempt(self):
        for code in [400, 401, 402, 403, 404]:
            with self.subTest(code=code):
                self.now += 65
                self.record(session='error-' + str(code))
                self.opener.open.side_effect = self.http_error(code)
                report = assisted.run(self.hive, limit=1)
                self.assertIsNone(report['retry_after'])
                self.assertEqual(report['error'], 'OpenRouter HTTP ' + str(code))
                with self.hive.connect() as c:
                    self.assertEqual(tuple(c.execute('SELECT status,attempts FROM assist_jobs ORDER BY rowid DESC LIMIT 1').fetchone()), ('failed', 1))

    def test_retry_after_supports_seconds_dates_and_invalid_headers(self):
        self.assertEqual(retry_after('12'), 12)
        self.assertEqual(retry_after(formatdate(self.now + 30, usegmt=True)), 30)
        for value in [None, 'bad', 'nan', 'inf', 'x' * 201]:
            with self.subTest(value=value): self.assertIsNone(retry_after(value))

    def test_json_error_envelopes_use_retry_after_without_logging_private_details(self):
        self.record()
        self.opener.open.return_value = Response({'error': {'code': 429, 'message': 'PRIVATE PROVIDER BODY'},
            'usage': {'cost': 0, 'prompt_tokens': 4}}, {'Retry-After': '11'})
        report = assisted.run(self.hive, limit=1)
        self.assertEqual(report['retry_after'], 11)
        self.assertEqual(report['error'], 'OpenRouter HTTP 429')
        self.assertEqual(assisted.status(self.hive)['usage_last_24h']['prompt_tokens'], 4)

    def test_error_envelope_with_unexpected_charge_keeps_accounting_and_billing_hold(self):
        self.record()
        self.opener.open.return_value = Response({'error': {'code': 502}, 'usage': {'cost': .01}})
        report = assisted.run(self.hive, limit=1)
        self.assertIsNone(report['retry_after'])
        status = assisted.status(self.hive)
        self.assertEqual(status['reserved_or_spent_usd_last_24h'], .01)
        self.assertEqual(status['billing_holds'][0]['project'], 'alpha')
        self.assertEqual(status['jobs'], {'failed': 1})


if __name__ == '__main__':
    unittest.main()
