"""SQLite source of truth; JSON run files are optional exports and migration input."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parent.parent


def database_path(directory):
    directory = Path(directory).resolve()
    if directory in ((ROOT / 'data/discovery_mvp').resolve(), (ROOT / 'data/discovery_mvp_demo').resolve()):
        base = os.getenv('TENDER_AGENT_DB_DIR') or (
            str(Path(os.environ['LOCALAPPDATA']) / 'TenderAgent') if os.getenv('LOCALAPPDATA') else None)
        if base:
            workspace = ROOT.name + '-' + hashlib.sha256(str(ROOT).casefold().encode()).hexdigest()[:8]
            return Path(base) / workspace / ('demo.sqlite3' if directory.name.endswith('_demo') else 'live.sqlite3')
    return directory / 'tenders.sqlite3'


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.path = database_path(self.directory)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs(
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL UNIQUE, health TEXT NOT NULL,
                    report_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS runs_created ON runs(created_at DESC);
                CREATE TABLE IF NOT EXISTS observations(
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL, record_id TEXT NOT NULL, source_url TEXT NOT NULL,
                    payload_json TEXT NOT NULL, PRIMARY KEY(run_id, ordinal));
                CREATE INDEX IF NOT EXISTS observation_record ON observations(record_id);
                CREATE TABLE IF NOT EXISTS opportunities(
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, score REAL NOT NULL,
                    value_inr REAL, deadline TEXT, first_seen TEXT, last_seen TEXT,
                    discovery_status TEXT NOT NULL, payload_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS opportunity_order ON opportunities(score DESC, value_inr DESC);
                CREATE TABLE IF NOT EXISTS sources(
                    opportunity_id TEXT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
                    url TEXT NOT NULL, first_seen TEXT, last_seen TEXT, payload_json TEXT NOT NULL,
                    PRIMARY KEY(opportunity_id, url));
                CREATE TABLE IF NOT EXISTS opportunity_records(
                    opportunity_id TEXT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
                    record_id TEXT NOT NULL, PRIMARY KEY(opportunity_id, record_id));
                CREATE TABLE IF NOT EXISTS assessments(
                    cache_key TEXT PRIMARY KEY, signature TEXT, ranked_at TEXT, origin TEXT,
                    assessment_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS seen(
                    record_id TEXT PRIMARY KEY, url TEXT, first_seen TEXT, last_seen TEXT,
                    payload_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS group_attempts(name TEXT PRIMARY KEY, count INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS feedback(
                    record_id TEXT PRIMARY KEY, label TEXT NOT NULL, updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL);
            ''')
        self._migrate_legacy()

    @contextmanager
    def connection(self, write=False):
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=20000')
        try:
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _meta(self, connection, key, default=None):
        row = connection.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else default

    def _set_meta(self, connection, key, value):
        connection.execute('INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (key, encode(value)))

    def _insert_run(self, connection, run_id, report):
        existing = connection.execute('SELECT id FROM runs WHERE created_at=?', (report['created_at'],)).fetchone()
        run_id = existing['id'] if existing else run_id
        connection.execute('INSERT INTO runs VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET health=excluded.health, report_json=excluded.report_json',
                           (run_id, report['created_at'], report.get('health', 'unknown'), encode(report)))
        connection.execute('DELETE FROM observations WHERE run_id=?', (run_id,))
        connection.executemany('INSERT INTO observations VALUES(?,?,?,?,?)',
            [(run_id, index, item['id'], item['url'], encode(item)) for index, item in enumerate(report['candidates'])])

    def _save_state(self, connection, state):
        self._set_meta(connection, 'state_extra', {key: value for key, value in state.items()
                                                 if key not in ('seen', 'feedback', 'group_attempts')})
        for ident, item in state.get('seen', {}).items():
            connection.execute('INSERT INTO seen VALUES(?,?,?,?,?) ON CONFLICT(record_id) DO UPDATE SET '
                'url=excluded.url, first_seen=excluded.first_seen, last_seen=excluded.last_seen, payload_json=excluded.payload_json',
                (ident, item.get('url'), item.get('first_seen'), item.get('last_seen'), encode(item)))
        for group, count in state.get('group_attempts', {}).items():
            connection.execute('INSERT INTO group_attempts VALUES(?,?) ON CONFLICT(name) DO UPDATE SET count=excluded.count',
                               (group, count))
        for ident, review in state.get('feedback', {}).items():
            self._review(connection, ident, review)

    def _review(self, connection, ident, review):
        connection.execute('INSERT INTO feedback VALUES(?,?,?,?) ON CONFLICT(record_id) DO UPDATE SET '
            'label=excluded.label, updated_at=excluded.updated_at, payload_json=excluded.payload_json '
            'WHERE excluded.updated_at >= feedback.updated_at',
            (ident, review['label'], review.get('updated_at', ''), encode(review)))

    def _rebuild_library(self, connection):
        from discovery_mvp.library import build_library
        reports = [json.loads(row['report_json']) for row in connection.execute('SELECT report_json FROM runs ORDER BY created_at')]
        library = build_library(reports)
        connection.execute('DELETE FROM opportunities')
        self._set_meta(connection, 'library_report', {key: value for key, value in library.items() if key != 'candidates'} if library else None)
        if library:
            for item in library['candidates']:
                connection.execute('INSERT INTO opportunities VALUES(?,?,?,?,?,?,?,?,?)',
                    (item['id'], item['title'], item['score'], item.get('tender_value_inr'), item.get('deadline'),
                     item.get('first_seen'), item.get('last_seen'), item['discovery_status'], encode(item)))
                connection.executemany('INSERT INTO sources VALUES(?,?,?,?,?)',
                    [(item['id'], source['url'], source['first_seen'], source['last_seen'], encode(source))
                     for source in item['sources']])
                connection.executemany('INSERT INTO opportunity_records VALUES(?,?)',
                                       [(item['id'], ident) for ident in item['record_ids']])
        return library

    def _migrate_legacy(self):
        with self.connection() as connection:
            if self._meta(connection, 'json_migration_complete', False):
                return
        from discovery_mvp.library import saved_reports
        reports, issues = saved_reports(self.directory)
        reports = {report['created_at']: report for report in reports}
        archived_ids = {}
        for path in sorted((self.directory / 'runs').glob('*.json')):
            try:
                archived_ids[json.loads(path.read_text(encoding='utf-8'))['created_at']] = path.stem
            except (ValueError, KeyError, OSError):
                pass
        def legacy_file(name, default):
            path = self.directory / name
            if not path.exists():
                return default
            try:
                return json.loads(path.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                issues.append('Could not import legacy file: ' + name)
                return default
        state = legacy_file('state.json', {})
        cache = legacy_file('ranking_cache.json', {})
        with self.connection(write=True) as connection:
            if self._meta(connection, 'json_migration_complete', False):
                return
            for timestamp, report in sorted(reports.items()):
                run_id = archived_ids.get(timestamp)
                if not run_id or not re.fullmatch(r'\d{8}-\d{6}-[a-f0-9]{6}', run_id):
                    run_id = datetime.fromisoformat(timestamp).strftime('%Y%m%d-%H%M%S') + '-' + hashlib.sha256(timestamp.encode()).hexdigest()[:6]
                self._insert_run(connection, run_id, report)
            self._save_state(connection, state)
            for key, entry in cache.get('entries', {}).items():
                if isinstance(entry, dict) and isinstance(entry.get('assessment'), dict):
                    connection.execute('INSERT OR IGNORE INTO assessments VALUES(?,?,?,?,?)',
                        (key, None, entry.get('ranked_at'), entry.get('origin', 'legacy_json'), encode(entry['assessment'])))
            self._rebuild_library(connection)
            self._set_meta(connection, 'migration_issues', issues)
            self._set_meta(connection, 'schema_version', 1)
            self._set_meta(connection, 'json_migration_complete', True)
            self._set_meta(connection, 'migrated_at', datetime.now(timezone.utc).isoformat())

    def state(self):
        with self.connection() as connection:
            return {**self._meta(connection, 'state_extra', {}),
                    'seen': {row['record_id']: json.loads(row['payload_json']) for row in connection.execute('SELECT * FROM seen')},
                    'feedback': {row['record_id']: json.loads(row['payload_json']) for row in connection.execute('SELECT * FROM feedback')},
                    'group_attempts': {row['name']: row['count'] for row in connection.execute('SELECT * FROM group_attempts')}}

    def save_run(self, run_id, report, state):
        with self.connection(write=True) as connection:
            self._insert_run(connection, run_id, report)
            self._save_state(connection, state)
            return self._rebuild_library(connection)

    def report(self, run_id=None):
        with self.connection() as connection:
            row = connection.execute('SELECT report_json FROM runs WHERE id=?', (run_id,)).fetchone() if run_id else \
                connection.execute('SELECT report_json FROM runs ORDER BY created_at DESC LIMIT 1').fetchone()
            return json.loads(row['report_json']) if row else None

    def history(self, limit=30):
        with self.connection() as connection:
            return [{'id': row['id'], 'created_at': row['created_at'], 'health': row['health'],
                     'count': len((report := json.loads(row['report_json']))['candidates']),
                     'shortlisted': sum(item['status'] == 'shortlist' for item in report['candidates'])}
                    for row in connection.execute('SELECT * FROM runs ORDER BY created_at DESC LIMIT ?', (limit,))]

    def library(self):
        with self.connection() as connection:
            metadata = self._meta(connection, 'library_report')
            if metadata is None:
                return None
            return {**metadata, 'issues': [*metadata.get('issues', []), *self._meta(connection, 'migration_issues', [])],
                    'candidates': [json.loads(row['payload_json']) for row in connection.execute(
                        'SELECT payload_json FROM opportunities ORDER BY score DESC, value_inr DESC, title COLLATE NOCASE')]}

    def migration_issues(self):
        with self.connection() as connection:
            return self._meta(connection, 'migration_issues', [])

    def feedback(self, record_id, label, timestamp):
        with self.connection(write=True) as connection:
            row = connection.execute('SELECT payload_json FROM seen WHERE record_id=?', (record_id,)).fetchone()
            if not row:
                raise ValueError('This candidate is not in the saved discovery state')
            seen = json.loads(row['payload_json'])
            self._review(connection, record_id, {'label': label, 'groups': seen.get('groups', []), 'updated_at': timestamp})

    def cache_get(self, key):
        with self.connection() as connection:
            row = connection.execute('SELECT * FROM assessments WHERE cache_key=?', (key,)).fetchone()
            return {'assessment': json.loads(row['assessment_json']), 'ranked_at': row['ranked_at'], 'origin': row['origin']} if row else None

    def cache_put(self, key, entry, signature):
        with self.connection(write=True) as connection:
            connection.execute('INSERT INTO assessments VALUES(?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET '
                'signature=excluded.signature, ranked_at=excluded.ranked_at, origin=excluded.origin, assessment_json=excluded.assessment_json',
                (key, signature, entry['ranked_at'], entry['origin'], encode(entry['assessment'])))

    def cache_entries(self):
        with self.connection() as connection:
            return {row['cache_key']: {'assessment': json.loads(row['assessment_json']), 'ranked_at': row['ranked_at'],
                                      'origin': row['origin']} for row in connection.execute('SELECT * FROM assessments')}

    def counts(self):
        with self.connection() as connection:
            tables = ('runs', 'observations', 'opportunities', 'sources', 'assessments', 'feedback', 'seen')
            return {table: connection.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in tables}

    def integrity_check(self):
        with self.connection() as connection:
            return {'integrity': connection.execute('PRAGMA integrity_check').fetchone()[0],
                    'foreign_key_errors': [tuple(row) for row in connection.execute('PRAGMA foreign_key_check')]}

    def backup(self, destination):
        with self.connection() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Import legacy JSON data into SQLite without deleting source files.')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/discovery_mvp')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    store = Store(args.output)
    if args.backup:
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        store.backup(args.backup)
    print(json.dumps({'database': str(store.path), 'counts': store.counts(),
                      'checks': store.integrity_check(), 'migration_issues': store.migration_issues(),
                      'sqlite_version': sqlite3.sqlite_version}, indent=2))
