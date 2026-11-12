import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from discovery_mvp.codex_ranker import CodexRanker
from discovery_mvp.engine import PLANNER_PROMPT, RANK_PROMPT


class CodexRankerTests(unittest.TestCase):
    def client(self):
        with patch('discovery_mvp.codex_search.cli_path', return_value='codex.exe'), \
                patch('discovery_mvp.codex_search.subprocess.run', return_value=
                      subprocess.CompletedProcess([], 0, 'Logged in using ChatGPT', '')):
            return CodexRanker()

    def test_planning_and_ranking_use_structured_output_without_search_or_keys(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'secret', 'OLLAMA_API_KEY': 'secret',
                                     'CODEX_RANK_MODEL': 'test-model', 'CODEX_RANK_TIMEOUT': '75'}):
            client = self.client()
            responses = [
                {'groups': {'fountains': ['musical fountain renovation']}},
                {'score': .9, 'reason': 'Direct match', 'evidence': 'Fountain renovation',
                 'is_tender': True, 'deadline': None},
            ]

            def fake_run(command, **kwargs):
                body = responses.pop(0)
                schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text())
                self.assertEqual(schema['required'], list(body))
                Path(command[command.index('--output-last-message') + 1]).write_text(json.dumps(body))
                self.assertNotIn('--search', command)
                self.assertIn('web_search="disabled"', command)
                self.assertIn('features.shell_tool=false', command)
                self.assertIn('read-only', command)
                self.assertEqual(command[command.index('--model') + 1], 'test-model')
                self.assertNotIn('OPENAI_API_KEY', kwargs['env'])
                self.assertNotIn('OLLAMA_API_KEY', kwargs['env'])
                self.assertEqual(kwargs['timeout'], 75)
                self.assertIn('untrusted data', kwargs['input'])
                return subprocess.CompletedProcess(command, 0, json.dumps({
                    'type': 'turn.completed', 'usage': {'input_tokens': 100}}), '')

            with patch('discovery_mvp.codex_ranker.subprocess.run', side_effect=fake_run):
                self.assertIn('groups', client.complete(PLANNER_PROMPT, {'groups': {'fountains': []}}))
                self.assertEqual(client.complete(RANK_PROMPT, {'candidate': {}})['score'], .9)
            self.assertEqual([call['task'] for call in client.calls], ['planning', 'ranking'])
            self.assertEqual(client.calls[1]['usage'], [{'input_tokens': 100}])

    def test_failures_are_reported_without_retry_or_private_diagnostics(self):
        client = self.client()
        cases = [
            (subprocess.TimeoutExpired('codex', 180), RuntimeError, 'no automatic retry'),
            (OSError('private path'), RuntimeError, 'could not start'),
            (subprocess.CompletedProcess([], 1, '', 'private account'), RuntimeError, 'exited 1'),
            (subprocess.CompletedProcess([], 0, '', ''), RuntimeError, 'structured result file'),
        ]
        for result, error, message in cases:
            with self.subTest(message=message):
                kwargs = {'side_effect': result} if isinstance(result, Exception) else {'return_value': result}
                with patch('discovery_mvp.codex_ranker.subprocess.run', **kwargs) as run:
                    with self.assertRaisesRegex(error, message) as raised:
                        client.complete(RANK_PROMPT, {})
                    self.assertNotIn('private', str(raised.exception))
                    self.assertEqual(run.call_count, 1)

    def test_invalid_output_is_rejected(self):
        client = self.client()
        for body in ('not json', '[]'):
            def fake_run(command, **kwargs):
                Path(command[command.index('--output-last-message') + 1]).write_text(body)
                return subprocess.CompletedProcess(command, 0, '', '')
            with patch('discovery_mvp.codex_ranker.subprocess.run', side_effect=fake_run):
                with self.assertRaises(ValueError):
                    client.complete(RANK_PROMPT, {})


if __name__ == '__main__':
    unittest.main()
