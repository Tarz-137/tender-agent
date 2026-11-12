from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from discovery_mvp.engine import candidate_id
from discovery_mvp.server import Dashboard, Handler


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'discovery_mvp').mkdir()
        (self.root / 'discovery_mvp/profile.json').write_text(json.dumps({'groups': {'fountains': ['musical fountain']}}))
        self.patch = patch('discovery_mvp.server.ROOT', self.root)
        self.patch.start()
        self.dashboard = Dashboard()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.dashboard = self.dashboard
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.patch.stop()
        self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.base + path, data=data, headers=headers or {})
        try:
            with urlopen(request) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def test_static_assets_and_secret_paths_are_not_served(self):
        with urlopen(self.base) as response:
            self.assertIn(b'Tender discovery', response.read())
            self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
        for path in ['/discovery_mvp/.env', '/profile.json', '/../server.py']:
            self.assertEqual(self.request(path)[0], 404)

    def test_state_returns_only_key_presence_and_separates_modes(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'do-not-expose-this-value'}):
            code, data = self.request('/api/state?mode=demo')
        self.assertEqual(code, 200)
        self.assertTrue(data['configuration']['openai_key'])
        self.assertNotIn('do-not-expose-this-value', json.dumps(data))
        self.assertIsNone(data['report'])
        self.assertEqual(self.request('/api/state?mode=invalid')[0], 400)
        self.assertEqual(self.request('/api/state?mode=live&run=..%2Fstate')[0], 400)
        self.assertEqual(self.request('/api/state?scope=invalid')[0], 400)

    def test_default_library_retains_old_records_and_run_scope_preserves_snapshot(self):
        directory = self.dashboard.directory('live')
        (directory / 'runs').mkdir(parents=True)
        def report(stamp, ident):
            return {'created_at': stamp, 'health': 'ok', 'candidates': [
                {'id': ident, 'title': ident, 'url': 'https://example.com/' + ident,
                 'snippets': [], 'score': .9, 'status': 'shortlist'}]}
        old = report('2026-11-01', 'old')
        latest = report('2026-11-02', 'new')
        (directory / 'runs/20261101-100000-aaaaaa.json').write_text(json.dumps(old))
        (directory / 'latest.json').write_text(json.dumps(latest))
        library = self.request('/api/state?mode=live')[1]['report']
        self.assertEqual(len(library['candidates']), 2)
        self.assertEqual(library['library_summary']['not_seen'], 1)
        latest_view = self.request('/api/state?mode=live&scope=run')[1]['report']
        self.assertEqual(len(latest_view['candidates']), 1)
        historical_view = self.request('/api/state?mode=live&run=20261101-100000-aaaaaa')[1]['report']
        self.assertEqual(historical_view['candidates'][0]['id'], 'old')

    def test_feedback_follows_merged_source_records(self):
        directory = self.dashboard.directory('live')
        directory.mkdir(parents=True)
        items = [{'id': ident, 'title': 'Musical fountain', 'url': 'https://example.com/' + ident,
                  'snippets': ['Tender 2026_TEST_100_1'], 'score': .9, 'status': 'shortlist'}
                 for ident in ('source-a', 'source-b')]
        report = {'created_at': '2026-11-01', 'candidates': items, 'health': 'ok'}
        (directory / 'latest.json').write_text(json.dumps(report))
        (directory / 'state.json').write_text(json.dumps({'feedback': {
            'source-a': {'label': 'relevant', 'updated_at': '2026-11-01'},
            'source-b': {'label': 'irrelevant', 'updated_at': '2026-11-02'}}}))
        snapshot = self.dashboard.snapshot('live')
        self.assertEqual(len(snapshot['report']['candidates']), 1)
        item = snapshot['report']['candidates'][0]
        self.assertEqual(snapshot['feedback'][item['id']]['label'], 'irrelevant')

    def test_mutations_require_session_token_and_local_origin(self):
        self.assertEqual(self.request('/api/run', {'mode': 'demo'})[0], 403)
        headers = {'X-Dashboard-Token': self.dashboard.token, 'Origin': 'https://untrusted.example'}
        self.assertEqual(self.request('/api/run', {'mode': 'demo'}, headers)[0], 403)
        headers['Origin'] = self.base
        with patch.object(self.dashboard, 'start', return_value={'status': 'running'}) as start:
            self.assertEqual(self.request('/api/run', {'mode': 'demo'}, headers)[0], 202)
            start.assert_called_once_with({'mode': 'demo'})

    def test_feedback_roundtrip_is_saved_to_correct_workspace(self):
        directory = self.dashboard.directory('demo')
        directory.mkdir(parents=True)
        url = 'https://example.com/tender'
        ident = candidate_id(url)
        (directory / 'state.json').write_text(json.dumps({'seen': {ident: {'groups': ['fountains']}}}))
        code, _ = self.request('/api/feedback', {'mode': 'demo', 'url': url, 'label': 'relevant'},
                               {'X-Dashboard-Token': self.dashboard.token})
        self.assertEqual(code, 200)
        self.assertEqual(self.dashboard.snapshot('demo')['feedback'][ident]['label'], 'relevant')
        self.assertEqual(self.dashboard.snapshot('live')['feedback'], {})

    def test_run_validation_and_overlap_protection(self):
        with self.assertRaises(ValueError):
            self.dashboard.start({'query_budget': 0})
        with patch.dict(os.environ, {}, clear=True), patch('discovery_mvp.server.cli_path', return_value='codex'), \
                self.assertRaisesRegex(ValueError, 'OPENAI_API_KEY'):
            self.dashboard.start({'mode': 'live', 'provider': 'both'})
        self.dashboard.job = {'status': 'running'}
        with self.assertRaisesRegex(RuntimeError, 'already in progress'):
            self.dashboard.start({'mode': 'demo'})

    def test_run_defaults_to_codex_search_and_ranker_without_api_keys(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch('discovery_mvp.server.cli_path', return_value='codex'), \
                patch('discovery_mvp.server.subprocess.Popen') as popen, \
                patch('discovery_mvp.server.threading.Thread'):
            self.dashboard.start({'mode': 'live'})
            command = popen.call_args.args[0]
            self.assertEqual(command[command.index('--provider') + 1], 'codex')
            self.assertEqual(command[command.index('--ranker') + 1], 'codex')

    def test_codex_ranker_requires_cli_even_with_other_search_provider(self):
        with patch('discovery_mvp.server.cli_path', return_value=None):
            with self.assertRaisesRegex(ValueError, 'Install Codex CLI'):
                self.dashboard.start({'mode': 'live', 'provider': 'gpt', 'ranker': 'codex'})


if __name__ == '__main__':
    unittest.main()
