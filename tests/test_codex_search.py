import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from discovery_mvp.codex_search import CodexSearch, child_environment, cli_path
from discovery_mvp.providers import Ollama


class CodexSearchTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows desktop installation')
    def test_finds_desktop_binary_when_it_is_missing_from_path(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'OpenAI/Codex/bin/test-version/codex.exe'
            binary.parent.mkdir(parents=True)
            binary.touch()
            with patch.dict(os.environ, {'LOCALAPPDATA': directory}), \
                    patch('discovery_mvp.codex_search.shutil.which', return_value=None):
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop('CODEX_CLI_PATH', None)
                    self.assertEqual(cli_path(), str(binary))

    def client(self):
        with patch('discovery_mvp.codex_search.cli_path', return_value='codex.exe'), \
                patch('discovery_mvp.codex_search.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'Logged in using ChatGPT', '')):
            return CodexSearch()

    def test_requires_chatgpt_auth_not_api_key_auth(self):
        with patch('discovery_mvp.codex_search.cli_path', return_value='codex.exe'), \
                patch('discovery_mvp.codex_search.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'Logged in using API key', '')):
            with self.assertRaisesRegex(ValueError, 'ChatGPT sign-in'):
                CodexSearch()

    def test_api_keys_and_parent_thread_are_not_forwarded(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'secret', 'OLLAMA_API_KEY': 'secret', 'CODEX_THREAD_ID': 'parent'}):
            environment = child_environment()
        self.assertNotIn('OPENAI_API_KEY', environment)
        self.assertNotIn('OLLAMA_API_KEY', environment)
        self.assertNotIn('CODEX_THREAD_ID', environment)

    def test_structured_results_require_completed_search_and_keep_cli_bounds(self):
        client = self.client()
        def fake_run(command, **kwargs):
            Path(command[command.index('--output-last-message') + 1]).write_text(json.dumps({
                'results': [{'title': 'Fountain', 'url': 'https://example.com/tender', 'content': 'Tender scope'}],
            }))
            self.assertIn('--search', command)
            self.assertIn('read-only', command)
            self.assertIn('features.shell_tool=false', command)
            self.assertIn('forced_login_method="chatgpt"', command)
            self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', command)
            self.assertIsInstance(kwargs['input'], str)
            event = {'type': 'item.completed', 'item': {'type': 'web_search', 'query': 'fountain'}}
            return subprocess.CompletedProcess(command, 0, json.dumps(event), '')
        with patch('discovery_mvp.codex_search.subprocess.run', side_effect=fake_run):
            self.assertEqual(len(client.search('fountain tender', '2026-11-03', 1)), 1)
        self.assertEqual(client.calls[0]['web_search_events'], 1)

    def test_no_search_event_or_timeout_does_not_return_guessed_results(self):
        client = self.client()
        with patch('discovery_mvp.codex_search.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            with self.assertRaisesRegex(RuntimeError, 'no completed web-search'):
                client.search('fountain', '2026-11-03', 1)
        with patch('discovery_mvp.codex_search.subprocess.run', side_effect=subprocess.TimeoutExpired('codex', 240)):
            with self.assertRaisesRegex(RuntimeError, 'no automatic retry'):
                client.search('fountain', '2026-11-03', 1)

    def test_short_local_json_tasks_disable_thinking_and_duplicate_retries(self):
        with patch('discovery_mvp.providers.post_json', return_value={'message': {'content': '{"score": 0.9}'}}) as post:
            self.assertEqual(Ollama().complete('rank', {})['score'], .9)
        self.assertFalse(post.call_args.args[1]['think'])
        self.assertEqual(post.call_args.kwargs['attempts'], 1)


if __name__ == '__main__':
    unittest.main()
