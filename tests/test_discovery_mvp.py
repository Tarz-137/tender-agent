import argparse
from datetime import date
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from discovery_mvp.engine import (
    apply_scope_guard, build_queries, canonical_url, mark_changes, merge_results, rank_candidate, render_report,
)
from discovery_mvp.providers import OpenAI
from discovery_mvp.demo import Demo
from discovery_mvp.run import ROOT, execute, run_lock
from discovery_mvp.storage import Store


class DiscoveryMVPTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads((ROOT / 'discovery_mvp/profile.json').read_text())

    def test_incidental_landmark_does_not_become_strong_match(self):
        result = apply_scope_guard({'title': 'Repairs to footpaths around musical fountain',
                                   'score': .9, 'reason': 'Model keyword match', 'status': 'shortlist'})
        self.assertEqual(result['status'], 'review')
        self.assertEqual(result['model_score'], .9)
        self.assertLess(result['score'], .4)
        direct = {'title': 'Renovation of musical fountain jets', 'score': .9, 'status': 'shortlist'}
        self.assertEqual(apply_scope_guard(direct), direct)

    def test_query_rotation_keeps_exploration_even_with_negative_feedback(self):
        state = {'group_attempts': {}, 'feedback': {}}
        covered = set()
        for iteration in range(5):
            queries = build_queries(self.profile, 3, state)
            self.assertEqual(len(queries), 3)
            for query in queries:
                name = query['group']
                covered.add(name)
                state['group_attempts'][name] = state['group_attempts'].get(name, 0) + 1
                state['feedback'][f'{iteration}/{name}'] = {'groups': [name], 'label': 'irrelevant'}
        self.assertEqual(covered, set(self.profile['groups']))

    def test_canonicalization_preserves_portal_ids_and_deduplicates_provenance(self):
        url = 'https://portal.gov.in/app?sp=123&utm_source=test&page=detail#section'
        canonical = canonical_url(url)
        self.assertIn('sp=123', canonical)
        self.assertIn('page=detail', canonical)
        self.assertNotIn('utm_', canonical)
        query = {'group': 'fountains', 'query': 'musical fountain tender'}
        results = [{'title': 'Fountain', 'url': url, 'content': 'A notice'}]
        issues = []
        items = merge_results([('gpt', query, results), ('ollama', query, results)], issues)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['providers'], ['gpt', 'ollama'])
        self.assertEqual(issues, [])
        with self.assertRaises(ValueError):
            canonical_url('javascript:alert(1)')

    def test_ranking_requires_verbatim_evidence_and_marks_old_dates_for_review(self):
        item = {'title': 'Fountain tender', 'snippets': ['Supply musical fountain'], 'url': 'https://example.com'}
        class Model:
            def complete(self, *_):
                return {'score': .99, 'reason': 'Match', 'is_tender': True,
                        'evidence': 'Invented quote', 'deadline': None}
        self.assertEqual(rank_candidate(item, self.profile, Model(), date.today())['status'], 'review')
        with patch.object(Model, 'complete', return_value={
            'score': .99, 'reason': 'Match', 'is_tender': True,
            'evidence': 'musical fountain', 'deadline': '2020-01-01',
        }):
            result = rank_candidate(item, self.profile, Model(), date.today())
            self.assertEqual(result['status'], 'review')
            self.assertTrue(result['possibly_expired'])

    def test_gpt_rejects_fabricated_urls_and_forces_search(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test'}):
            client = OpenAI()
        response = {'output': [
            {'type': 'web_search_call', 'action': {'sources': [{'url': 'https://example.com/real'}]}},
            {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps({'results': [
                {'title': 'Invented', 'url': 'https://example.com/fake', 'content': 'fake'}]})}]},
        ]}
        with patch.object(client, 'request', return_value=response) as request:
            with self.assertRaisesRegex(ValueError, 'source URLs'):
                client.search('fountain tender', '2026-11-03', 5)
            self.assertEqual(request.call_args.args[0]['tool_choice'], 'required')

    def test_change_tracking_and_report_escaping(self):
        state = {}
        item = {'id': 'id', 'title': '<script>alert(1)</script>', 'snippets': ['text'],
                'groups': ['fountains'], 'url': 'https://example.com',
                'providers': ['test'], 'evidence_kind': ['search snippet'], 'queries': ['query']}
        mark_changes([item], state, 'first')
        self.assertEqual(item['change'], 'new')
        mark_changes([item], state, 'second')
        self.assertEqual(item['change'], 'unchanged')
        item['snippets'].append('changed')
        mark_changes([item], state, 'third')
        self.assertEqual(item['change'], 'changed')
        self.assertEqual(state['seen']['id']['first_seen'], 'first')
        ranked = rank_candidate(item, self.profile, Demo(), date.today())
        ranked['change'] = 'new'
        page = render_report({'mode': 'test', 'created_at': 'now', 'health': 'ok',
                              'candidates': [ranked], 'issues': [], 'queries': [],
                              'successful_searches': 1, 'attempted_searches': 1})
        self.assertNotIn('<script>alert', page)
        self.assertIn('&lt;script&gt;', page)

    def test_lock_blocks_second_writer_and_releases(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            with run_lock(path):
                with self.assertRaises(RuntimeError):
                    with run_lock(path):
                        pass
            with run_lock(path):
                pass

    def test_demo_end_to_end_repeated_run_and_feedback(self):
        args = argparse.Namespace(profile=ROOT / 'discovery_mvp/profile.json', feedback=None,
                                  demo=True, provider='both', ranker='ollama', query_budget=2,
                                  results_per_query=5)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            self.assertEqual(execute(args, output), 0)
            first = json.loads((output / 'latest.json').read_text())
            self.assertEqual(len(first['candidates']), 4)
            self.assertEqual(set(first['timing']), {'planning_seconds', 'search_seconds', 'ranking_seconds', 'processing_seconds'})
            self.assertTrue(all(seconds >= 0 for seconds in first['timing'].values()))
            self.assertTrue(all(item['change'] == 'new' for item in first['candidates']))
            with patch('discovery_mvp.run.rank_candidate') as rank_again:
                self.assertEqual(execute(args, output), 0)
                rank_again.assert_not_called()
            second = json.loads((output / 'latest.json').read_text())
            self.assertEqual(second['ranking_cache']['hits'], 4)
            self.assertEqual(second['ranking_cache']['writes'], 0)
            self.assertTrue(all(item['change'] == 'unchanged' for item in second['candidates']))
            self.assertEqual(len(list((output / 'runs').glob('*.json'))), 2)
            args.feedback = ['https://example.com/demo/fountain', 'relevant']
            self.assertEqual(execute(args, output), 0)
            state = Store(output).state()
            self.assertEqual(len(state['feedback']), 1)

    def test_missing_credentials_are_failure_not_empty_success(self):
        args = argparse.Namespace(profile=ROOT / 'discovery_mvp/profile.json', feedback=None,
                                  demo=False, provider='both', ranker='gpt', query_budget=1,
                                  results_per_query=5)
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            self.assertEqual(execute(args, Path(temp)), 1)
            report = json.loads((Path(temp) / 'latest.json').read_text())
            self.assertEqual(report['health'], 'failed')
            self.assertEqual(report['successful_searches'], 0)
            self.assertEqual(report['attempted_searches'], 2)

    def test_partial_provider_failure_preserves_successful_candidates(self):
        args = argparse.Namespace(profile=ROOT / 'discovery_mvp/profile.json', feedback=None,
                                  demo=False, provider='both', ranker='ollama', query_budget=1,
                                  results_per_query=5)
        class BrokenGPT:
            def search(self, *_):
                raise RuntimeError('API HTTP 429')
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'OLLAMA_API_KEY': 'test'}), \
                patch('discovery_mvp.run.OpenAI', return_value=BrokenGPT()), \
                patch('discovery_mvp.run.Ollama', return_value=Demo()):
            self.assertEqual(execute(args, Path(temp)), 2)
            report = json.loads((Path(temp) / 'latest.json').read_text())
            self.assertEqual(report['health'], 'partial')
            self.assertEqual(len(report['candidates']), 4)
            self.assertEqual(report['successful_searches'], 1)

    def test_codex_and_local_ranker_need_no_search_api_keys(self):
        args = argparse.Namespace(profile=ROOT / 'discovery_mvp/profile.json', feedback=None,
                                  demo=False, provider='codex', ranker='ollama', query_budget=1,
                                  results_per_query=2)
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True), \
                patch('discovery_mvp.run.CodexSearch', return_value=Demo()), \
                patch('discovery_mvp.run.Ollama', return_value=Demo()), \
                patch('discovery_mvp.run.OpenAI') as api:
            self.assertEqual(execute(args, Path(temp)), 0)
            api.assert_not_called()
            report = json.loads((Path(temp) / 'latest.json').read_text())
            self.assertEqual(report['candidates'][0]['providers'], ['codex'])
            self.assertEqual(report['candidates'][0]['evidence_kind'], ['Codex web-search summary'])


if __name__ == '__main__':
    unittest.main()
