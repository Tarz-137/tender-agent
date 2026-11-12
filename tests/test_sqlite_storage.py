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
