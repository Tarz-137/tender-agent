import copy
import itertools
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

    def kumbh_pair(self):
        known = self.item('2026_HRDA_101761_1', 'known',
            title='Design, supply, installation and commissioning of musical fountain with multimedia water-screen show at Kumbh Mela 2027, Haridwar',
            text='Estimated value INR 3.7314 crore')
        mirror = self.item('', 'mirror',
            title='Musical fountain and multimedia water-screen show for Kumbh Mela 2027, Haridwar',
            text='Estimated value INR 3.73 crore')
        return known, mirror

    def test_paraphrased_missing_id_merges_with_corroboration_in_either_order(self):
        for items in itertools.permutations(self.kumbh_pair()):
            library = build_library([self.report('2026-11-01', list(items))])
            self.assertEqual(len(library['candidates']), 1)
            merged = library['candidates'][0]
            self.assertEqual(merged['tender_ids'], ['2026_HRDA_101761_1'])
            self.assertEqual(set(merged['record_ids']), {'known', 'mirror'})
            self.assertEqual(len(merged['sources']), 2)

    def test_similar_listings_stay_separate_without_all_required_evidence(self):
        cases = {
            'different place': {'title': 'Musical fountain and multimedia water-screen show for Kumbh Mela 2027, Prayagraj'},
            'different scope': {'title': 'Repair of musical fountain and multimedia water-screen show for Kumbh Mela 2027, Haridwar'},
            'different year': {'title': 'Musical fountain and multimedia water-screen show for Kumbh Mela 2028, Haridwar'},
            'different deadline': {'deadline': '2026-12-01'},
            'missing deadline': {'deadline': None},
            'different value': {'snippets': ['Estimated value INR 4 crore']},
            'missing value': {'snippets': []},
            'conflicting values': {'snippets': ['Estimated value INR 3.73 crore; estimated value INR 4 crore']},
            'different official ID': {'snippets': ['2026_HRDA_101762_1; Estimated value INR 3.73 crore']},
            'multiple official IDs': {'snippets': ['2026_HRDA_101761_1 and 2026_HRDA_101762_1; Estimated value INR 3.73 crore']},
            'package only in snippet': {'snippets': ['Package 2; Estimated value INR 3.73 crore']},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                known, mirror = self.kumbh_pair()
                mirror.update(changes)
                self.assertEqual(len(build_library([self.report('2026-11-01', [known, mirror])])['candidates']), 2)

    def test_conflicting_ward_and_package_details_block_even_exact_titles(self):
        for kind in ('Ward', 'Package', 'Lot', 'Phase'):
            with self.subTest(kind=kind):
                known, mirror = self.kumbh_pair()
                mirror['title'] = known['title']
                known['snippets'][0] += f'; {kind} 1'
                mirror['snippets'][0] += f'; {kind} 2'
                self.assertEqual(len(build_library([self.report('2026-11-01', [known, mirror])])['candidates']), 2)

    def test_ambiguous_missing_id_cannot_bridge_two_official_tenders(self):
        known, mirror = self.kumbh_pair()
        other = copy.deepcopy(known)
        other.update(id='other', url='https://example.com/other')
        other['snippets'] = ['2026_HRDA_101762_1; Estimated value INR 3.73 crore']
        for items in itertools.permutations((known, other, mirror)):
            self.assertEqual(len(build_library([self.report('2026-11-01', list(items))])['candidates']), 3)

    def test_marker_detection_does_not_treat_third_party_as_part_y(self):
        from discovery_mvp.library import detail_markers
        item = self.item('', 'mirror', text='Third-party notice; Phase I; Package 2')
        self.assertEqual(detail_markers(item), {('phase', 'i'), ('package', '2')})

    def test_missing_package_cannot_bridge_different_packages_in_exact_title_groups(self):
        known, mirror = self.kumbh_pair()
        known['snippets'] = ['Estimated value INR 3.7314 crore; Package 1']
        mirror['title'] = known['title']
        other = copy.deepcopy(known)
        other.update(id='other', url='https://example.com/other')
        other['snippets'] = ['Estimated value INR 3.73 crore; Package 2']
        for items in itertools.permutations((known, mirror, other)):
            result = build_library([self.report('2026-11-01', list(items))])
            self.assertEqual(len(result['candidates']), 2)
            self.assertFalse(any({'known', 'other'} <= set(item['record_ids']) for item in result['candidates']))

    def test_existing_exact_title_unidentified_mirrors_still_merge(self):
        known, mirror = self.kumbh_pair()
        known['snippets'] = ['Estimated value INR 3.7314 crore']
        mirror['title'] = known['title']
        self.assertEqual(len(build_library([self.report('2026-11-01', [known, mirror])])['candidates']), 1)

    def test_two_unidentified_paraphrases_are_not_merged_by_similarity(self):
        known, mirror = self.kumbh_pair()
        known['snippets'] = ['Estimated value INR 3.7314 crore']
        self.assertEqual(len(build_library([self.report('2026-11-01', [known, mirror])])['candidates']), 2)

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
