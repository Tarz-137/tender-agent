import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from discovery_mvp.codex_auth import CodexAuth, login_status


class CodexAuthTests(unittest.TestCase):
    def test_status_distinguishes_signed_out_crash_and_chatgpt(self):
        cases = [(0, 'Logged in using ChatGPT', 'connected'),
                 (0, 'Logged in using an API key', 'signed_out'),
                 (1, 'Not logged in', 'signed_out'), (-9, '', 'error')]
        for code, output, expected in cases:
            with self.subTest(code=code, output=output), \
                    patch('discovery_mvp.codex_auth.cli_path', return_value='codex'), \
                    patch('discovery_mvp.codex_auth.subprocess.run', return_value=subprocess.CompletedProcess([], code, output, 'private diagnostic')):
                result = login_status()
                self.assertEqual(result['status'], expected)
                self.assertNotIn('private diagnostic', json.dumps(result))

    def test_missing_and_timeout(self):
        with patch('discovery_mvp.codex_auth.cli_path', return_value=None):
            self.assertEqual(login_status()['status'], 'missing')
        with patch('discovery_mvp.codex_auth.cli_path', return_value='codex'), \
                patch('discovery_mvp.codex_auth.subprocess.run', side_effect=subprocess.TimeoutExpired('codex', 20)):
            self.assertEqual(login_status()['status'], 'error')

    def test_login_uses_cli_oauth_and_verifies_result(self):
        auth = CodexAuth()
        process = Mock()
        process.wait.return_value = 0
        with patch('discovery_mvp.codex_auth.cli_path', return_value='codex'), \
                patch('discovery_mvp.codex_auth.subprocess.Popen', return_value=process) as popen, \
                patch('discovery_mvp.codex_auth.login_status', return_value={'status': 'connected'}):
            auth._work('login')
        self.assertEqual(auth.snapshot()['status'], 'connected')
        self.assertEqual(popen.call_args.args[0][-1], 'login')
        self.assertEqual(popen.call_args.kwargs['stdout'], subprocess.DEVNULL)
        process.wait.assert_called_once_with(timeout=180)

    def test_login_timeout_kills_process_and_can_retry(self):
        auth = CodexAuth()
        process = Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired('codex', 180), 1]
        with patch('discovery_mvp.codex_auth.cli_path', return_value='codex'), \
                patch('discovery_mvp.codex_auth.subprocess.Popen', return_value=process):
            auth._work('login')
        process.kill.assert_called_once()
        self.assertEqual(auth.snapshot()['status'], 'error')
        self.assertIsNone(auth.process)

    def test_duplicate_login_and_shutdown(self):
        auth = CodexAuth()
        with patch('discovery_mvp.codex_auth.threading.Thread'):
            auth.start('login')
            with self.assertRaises(RuntimeError):
                auth.start('login')
        auth.process = Mock()
        auth.process.poll.return_value = None
        auth.close()
        auth.process.kill.assert_called_once()
