import json
from pathlib import Path
import tempfile
import unittest

from discovery_mvp.library import build_library, persist_library
from discovery_mvp.storage import Store


class OpportunityLibraryTests(unittest.TestCase):
    def item(self, ident, url, title='Musical fountain work', text='', score=.9):
        return {'id': url, 'url': 'https://example.com/' + url, 'title': title,
                'snippets': [ident + ' ' + text], 'score': score, 'status': 'shortlist',
                'deadline': '2026-11-12', 'groups': ['fountains'], 'providers': ['codex']}

    def report(self, stamp, items):
        return {'created_at': stamp, 'candidates': items, 'issues': [], 'health': 'ok'}

    def test_retains_missing_and_merges_id_across_mirrors_with_latest_assessment(self):
        a = self.item('2026_TEST_100_1', 'a', text='Estimated value ₹10 lakh')
        b = self.item('2026_TEST_200_1', 'b')
        mirror = self.item('2026_test_100_1', 'mirror', title='Renovation of musical fountain', score=.7)
        c = self.item('2026_TEST_300_1', 'c')
        library = build_library([self.report('2026-11-01', [a, b]),
                                 self.report('2026-11-02', [mirror, c])])
        self.assertEqual(len(library['candidates']), 3)
        self.assertEqual(library['library_summary']['not_seen'], 1)
        merged = next(item for item in library['candidates'] if '2026_TEST_100_1' in item['tender_ids'])
        self.assertEqual(merged['score'], .7)
        self.assertEqual(merged['discovery_status'], 'rediscovered')
        self.assertEqual(merged['first_seen'], '2026-11-01')
        self.assertEqual(merged['last_seen'], '2026-11-02')
        self.assertEqual(len(merged['sources']), 2)
        self.assertEqual(merged['tender_value_inr'], 1000000)
        self.assertIn('previously stated', merged['tender_value_status'])
        new = next(item for item in library['candidates'] if '2026_TEST_300_1' in item['tender_ids'])
        self.assertEqual(new['discovery_status'], 'new')

    def test_different_tender_ids_do_not_merge_even_if_titles_and_urls_match(self):
        a = self.item('2026_TEST_100_1', 'same')
        b = self.item('2026_TEST_200_1', 'same')
        library = build_library([self.report('2026-11-01', [a, b])])
        self.assertEqual(len(library['candidates']), 2)

    def test_exact_specific_title_and_deadline_bridge_missing_id(self):
        title = 'Integrated audio visual projection mapping laser and lighting systems at Pushkar'
        a = self.item('', 'mirror', title=title)
        b = self.item('2026_ADA_596113_1', 'official', title=title)
        library = build_library([self.report('2026-11-01', [a]), self.report('2026-11-02', [b])])
        self.assertEqual(len(library['candidates']), 1)
        self.assertEqual(library['candidates'][0]['tender_ids'], ['2026_ADA_596113_1'])

    def test_short_generic_titles_and_different_wards_are_separate(self):
        items = [self.item('', 'a', title='Fountain work'), self.item('', 'b', title='Fountain work'),
                 self.item('', 'ward02', title='Construction of Colour Musical Fountain at Ward 02 Near NDC Dhanpuri'),
                 self.item('', 'ward13', title='Construction of Colour Musical Fountain at Ward 13 Near Shopping Complex Dhanpuri')]
        self.assertEqual(len(build_library([self.report('2026-11-01', items)])['candidates']), 4)

    def test_precise_department_reference_bridges_missing_tender_id(self):
        reference = '80/EE (E)/PWD EMD South West/2026-27'
        a = self.item('2026_PWD_300656_1', 'mirror', text='Reference ' + reference)
        b = self.item('', 'new-mirror', title='Water fountains at Dabri Palam', text='PWD tender ' + reference)
        library = build_library([self.report('2026-11-01', [a]), self.report('2026-11-02', [b])])
        self.assertEqual(len(library['candidates']), 1)
        self.assertEqual(len(library['candidates'][0]['sources']), 2)

    def test_persistence_rebuilds_from_history_and_does_not_duplicate_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'runs').mkdir()
            first = self.report('2026-11-01', [self.item('GEM/2026/B/8199031', 'first')])
            latest = self.report('2026-11-02', [self.item('GEM/2026/B/8199031', 'second')])
            (path / 'runs/first.json').write_text(json.dumps(first))
            (path / 'runs/second.json').write_text(json.dumps(latest))
            (path / 'latest.json').write_text(json.dumps(latest))
            (path / 'runs/broken.json').write_text('bad json')
            library = persist_library(path)
            self.assertEqual(library['library_summary']['run_count'], 2)
            self.assertEqual(len(library['candidates']), 1)
            self.assertEqual(library['candidates'][0]['seen_in_runs'], 2)
            self.assertTrue(library['issues'])
            self.assertTrue(Store(path).path.exists())
            self.assertEqual(len(list((path / 'runs').glob('*.json'))), 3)


if __name__ == '__main__':
    unittest.main()
