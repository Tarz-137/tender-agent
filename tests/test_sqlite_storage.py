import json
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from discovery_mvp.storage import Store


class SQLiteStorageTests(unittest.TestCase):
    def report(self, timestamp='2026-11-01T10:00:00+05:30', record='one'):
        return {'created_at': timestamp, 'health': 'ok', 'issues': [], 'candidates': [{
            'id': record, 'title': 'Musical fountain', 'url': 'https://example.com/' + record,
            'snippets': ['Tender 2026_TEST_100_1; estimated value ₹10 lakh'],
            'score': .9, 'status': 'shortlist', 'groups': ['fountains'], 'providers': ['codex']}]}

    def test_legacy_import_is_idempotent_and_keeps_source_files_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'runs').mkdir()
            report = self.report()
            source = path / 'runs/20261101-100000-aaaaaa.json'
            source.write_text(json.dumps(report))
            (path / 'latest.json').write_text(json.dumps(report))
            state = {'seen': {'one': {'url': 'https://example.com/one', 'groups': ['fountains']}},
                     'group_attempts': {'fountains': 3}, 'feedback': {
                         'one': {'label': 'relevant', 'groups': ['fountains'], 'updated_at': '2026-11-01'}}}
            (path / 'state.json').write_text(json.dumps(state))
            (path / 'ranking_cache.json').write_text(json.dumps({'entries': {'cached-key': {
                'assessment': {'score': .9}, 'ranked_at': '2026-11-01', 'origin': 'model'}}}))
            before = {file.name: file.read_bytes() for file in path.glob('*.json')}
            store = Store(path)
            self.assertEqual(store.counts()['runs'], 1)
            self.assertEqual(store.counts()['opportunities'], 1)
            self.assertEqual(store.counts()['observations'], 1)
            self.assertEqual(store.state(), state)
            self.assertEqual(store.cache_get('cached-key')['assessment']['score'], .9)
            self.assertEqual(store.history()[0]['id'], '20261101-100000-aaaaaa')
            self.assertEqual(Store(path).counts(), store.counts())
            self.assertEqual(before, {file.name: file.read_bytes() for file in path.glob('*.json')})
            (path / 'latest.json').write_text(json.dumps(self.report(record='changed-export')))
            self.assertEqual(Store(path).report()['candidates'][0]['id'], 'one')

    def test_run_state_and_library_rollback_together_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            with patch.object(store, '_rebuild_library', side_effect=RuntimeError('failure')):
                with self.assertRaises(RuntimeError):
                    store.save_run('20261101-100000-aaaaaa', self.report(), {'group_attempts': {'fountains': 1}})
            self.assertEqual(store.counts()['runs'], 0)
            self.assertEqual(store.counts()['observations'], 0)
            self.assertEqual(store.state()['group_attempts'], {})
            self.assertIsNone(store.library())

    def test_feedback_during_discovery_survives_older_state_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            state = {'seen': {'one': {'url': 'https://example.com/one', 'groups': ['fountains']}},
                     'feedback': {'one': {'label': 'relevant', 'updated_at': '2026-11-01'}}}
            store.save_run('20261101-100000-aaaaaa', self.report(), state)
            old_state = store.state()
            store.feedback('one', 'irrelevant', '2026-11-03')
            store.save_run('20261102-100000-bbbbbb', self.report('2026-11-02T10:00:00+05:30'), old_state)
            self.assertEqual(store.state()['feedback']['one']['label'], 'irrelevant')

    def test_database_retains_library_and_history_without_json_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.save_run('20261101-100000-aaaaaa', self.report(), {})
            store.save_run('20261102-100000-bbbbbb', self.report('2026-11-02T10:00:00+05:30', 'mirror'), {})
            reopened = Store(directory)
            self.assertFalse((Path(directory) / 'latest.json').exists())
            self.assertEqual(reopened.counts()['runs'], 2)
            self.assertEqual(reopened.counts()['sources'], 2)
            self.assertEqual(len(reopened.library()['candidates']), 1)
            self.assertEqual(reopened.library()['candidates'][0]['discovery_status'], 'rediscovered')
            self.assertEqual(reopened.integrity_check(), {'integrity': 'ok', 'foreign_key_errors': []})

    def test_discovery_date_uses_all_daily_observations_and_ist_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            # The same opportunity appears on three days, twice on the middle day.
            for index, stamp in enumerate((
                '2026-11-01T10:00:00+05:30',
                '2026-11-01T18:30:00+00:00',  # Midnight Nov 2 IST, inclusive.
                '2026-11-02T12:00:00+05:30',
                '2026-11-03T10:00:00+05:30',
            )):
                store.save_run(f'run-{index}', self.report(stamp), {})
            excluded = self.report('2026-11-02T18:30:00+00:00', 'outside')
            excluded['candidates'][0]['snippets'] = ['A separate tender']
            store.save_run('outside', excluded, {})  # Midnight Nov 3, exclusive.
            result = store.library('2026-11-02')
            self.assertEqual(len(result['candidates']), 1)
            item = result['candidates'][0]
            self.assertEqual(item['record_ids'], ['one'])
            self.assertTrue(item['first_seen'].startswith('2026-11-01'))
            self.assertTrue(item['last_seen'].startswith('2026-11-03'))
            self.assertEqual(result['library_summary']['total'], 1)
            self.assertEqual(result['discovery_date'], '2026-11-02')
            self.assertEqual(store.library('2026-11-04')['candidates'], [])
            self.assertEqual(len(store.library()['candidates']), 2)

    def test_discovery_date_supports_legacy_dates_and_naive_ist_times(self):
        for stamp in ('2026-11-02', '2026-11-02T23:59:59'):
            with self.subTest(stamp=stamp), tempfile.TemporaryDirectory() as directory:
                store = Store(directory)
                store.save_run('legacy', self.report(stamp), {})
                self.assertEqual(len(store.library('2026-11-02')['candidates']), 1)
                self.assertEqual(store.library('2026-11-03')['candidates'], [])

    def test_discovery_date_validation_even_without_saved_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            for value in ('2026-02-30', '2026-1-01', "2026-11-01' OR 1=1", ''):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    store.library(value)
            self.assertIsNone(store.library('2026-11-01'))

    def test_library_upgrade_rebuilds_saved_runs_without_changing_history_or_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            first = self.report()
            first['candidates'][0].update(
                title='Design supply installation of musical fountain with multimedia show at Kumbh Mela Haridwar',
                deadline='2026-11-16',
                snippets=['2026_HRDA_101761_1; estimated value INR 3.7314 crore'])
            mirror = self.report('2026-11-02T10:00:00+05:30', 'mirror')
            mirror['candidates'][0].update(
                title='Musical fountain and multimedia show for Kumbh Mela Haridwar',
                deadline='2026-11-16', snippets=['Estimated value INR 3.73 crore'])
            with patch('discovery_mvp.library.missing_id_match', return_value=False):
                store.save_run('first', first, {})
                store.save_run('second', mirror, {'feedback': {
                    'one': {'label': 'relevant', 'updated_at': '2026-11-02'}}})
            self.assertEqual(len(store.library()['candidates']), 2)
            before_history, before_state = store.history(), store.state()
            with store.connection(write=True) as connection:
                store._set_meta(connection, 'library_version', 1)
            upgraded = Store(directory)
            self.assertEqual(len(upgraded.library()['candidates']), 1)
            self.assertEqual(upgraded.library()['library_version'], 2)
            self.assertEqual(upgraded.library('2026-11-02')['candidates'][0]['record_ids'], ['mirror', 'one'])
            self.assertEqual(upgraded.history(), before_history)
            self.assertEqual(upgraded.state(), before_state)
            self.assertEqual(upgraded.report('first'), first)
            self.assertEqual(upgraded.report('second'), mirror)
            self.assertEqual(upgraded.integrity_check(), {'integrity': 'ok', 'foreign_key_errors': []})
            with patch.object(Store, '_rebuild_library', side_effect=AssertionError('unnecessary rebuild')):
                Store(directory)

    def test_backup_can_be_opened_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.save_run('20261101-100000-aaaaaa', self.report(), {})
            target = Path(directory) / 'backup.sqlite3'
            store.backup(target)
            import sqlite3
            with closing(sqlite3.connect(target)) as connection:
                self.assertEqual(connection.execute('SELECT count(*) FROM runs').fetchone()[0], 1)
                self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')


if __name__ == '__main__':
    unittest.main()
