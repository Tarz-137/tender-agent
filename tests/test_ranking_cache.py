from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from discovery_mvp.ranking_cache import RankingCache, assessment_key, ranking_signature


class RankModel:
    model = 'local-model:tag'
    base = 'http://localhost:11434'


class RankingCacheTests(unittest.TestCase):
    def item(self):
        return {'title': 'Fountain work', 'snippets': ['Supply musical fountain', 'Deadline 2026-11-05'],
                'id': 'old', 'url': 'https://old.example/tender', 'providers': ['codex'],
                'score': .95, 'reason': 'Direct overlap', 'evidence': 'musical fountain',
                'status': 'shortlist', 'deadline': '2026-11-05', 'deadline_status': 'unverified'}

    def test_persisted_hit_keeps_new_source_metadata_and_refreshes_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.json'
            cache = RankingCache(path, 'signature')
            original = self.item()
            self.assertTrue(cache.put(original))
            fresh = {**original, 'id': 'new', 'url': 'https://new.example/tender', 'providers': ['gpt']}
            fresh['snippets'] = list(reversed(fresh['snippets']))
            cache = RankingCache(path, 'signature')
            result = cache.get(fresh, date(2026, 11, 6))
            self.assertEqual(result['url'], fresh['url'])
            self.assertEqual(result['providers'], ['gpt'])
            self.assertEqual(result['id'], 'new')
            self.assertEqual(result['score'], .95)
            self.assertEqual(result['status'], 'review')
            self.assertTrue(result['possibly_expired'])
            self.assertTrue(result['ranking_cache']['hit'])

    def test_content_and_signature_changes_invalidate(self):
        item = self.item()
        for changed in [{**item, 'title': 'Other work'}, {**item, 'snippets': ['Changed requirement']}]:
            self.assertNotEqual(assessment_key(item, 'a'), assessment_key(changed, 'a'))
        self.assertNotEqual(assessment_key(item, 'a'), assessment_key(item, 'b'))

    def test_model_capabilities_prompt_threshold_and_revision_invalidate(self):
        profile = {'groups': {'fountains': ['musical fountain']}, 'shortlist_threshold': .7}
        signature = ranking_signature(profile, RankModel(), 'ollama')
        self.assertNotEqual(signature, ranking_signature({**profile, 'groups': {'shows': ['laser']}}, RankModel(), 'ollama'))
        self.assertNotEqual(signature, ranking_signature({**profile, 'shortlist_threshold': .8}, RankModel(), 'ollama'))
        with patch.object(RankModel, 'model', 'other-model'):
            self.assertNotEqual(signature, ranking_signature(profile, RankModel(), 'ollama'))
        with patch('discovery_mvp.ranking_cache.RANK_PROMPT', 'new prompt'):
            self.assertNotEqual(signature, ranking_signature(profile, RankModel(), 'ollama'))
        with patch.dict('os.environ', {'DISCOVERY_RANK_CACHE_VERSION': 'new-version'}):
            self.assertNotEqual(signature, ranking_signature(profile, RankModel(), 'ollama'))

    def test_failed_ranking_and_unsupported_evidence_are_not_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = RankingCache(Path(directory) / 'cache.json', 'a')
            self.assertFalse(cache.put({**self.item(), 'evidence': 'Not evaluated', 'reason': 'Ranking unavailable'}))
            self.assertFalse(cache.put({**self.item(), 'evidence': 'Invented evidence'}))
            self.assertFalse(cache.put({**self.item(), 'score': float('nan')}))
            self.assertEqual(cache.entries, {})

    def test_corrupt_cache_and_malformed_entry_are_recoverable_misses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ranking_cache.json'
            path.write_text('invalid json')
            cache = RankingCache(path, 'a')
            self.assertIsNotNone(cache.warning)
            self.assertIsNone(cache.get(self.item(), date.today()))
            cache.put(self.item())
            key = assessment_key(self.item(), 'a')
            entry = cache.store.cache_get(key)
            entry['assessment']['evidence'] = 'invented'
            cache.store.cache_put(key, entry, 'a')
            self.assertIsNone(RankingCache(path, 'a').get(self.item(), date.today()))

    def test_cache_updates_sqlite_without_json_rewrites(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ranking_cache.json'
            cache = RankingCache(path, 'a')
            self.assertTrue(cache.put(self.item()))
            self.assertTrue(cache.path.exists())
            self.assertEqual(cache.path.suffix, '.sqlite3')
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
