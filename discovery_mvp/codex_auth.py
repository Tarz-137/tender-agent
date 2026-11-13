"""Local Codex browser sign-in; never expose CLI output or credentials to HTTP."""
import os
import subprocess
import threading

from discovery_mvp.codex_search import child_environment, cli_path


def login_status():
    executable = cli_path()
    if not executable:
        return {'status': 'missing', 'message': 'Install Codex CLI, then check again. If needed, set CODEX_CLI_PATH in discovery_mvp/.env and restart.'}
    try:
        result = subprocess.run([executable, 'login', 'status'], capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=20,
                                env=child_environment(),
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except (OSError, subprocess.TimeoutExpired):
        return {'status': 'error', 'message': 'Codex could not check sign-in. Check that the CLI runs and has access to its local state.'}
    output = (result.stdout + result.stderr).lower()
    if result.returncode == 0 and 'using chatgpt' in output:
        return {'status': 'connected', 'message': 'Signed in with ChatGPT. Search connectivity and usage allowance are checked when discovery runs.'}
    if 'not logged in' in output or (result.returncode == 0 and 'api key' in output):
        return {'status': 'signed_out', 'message': 'Sign in with ChatGPT to use Codex discovery and ranking.'}
    return {'status': 'error', 'message': f'Codex sign-in check failed (exit {result.returncode}). Check the CLI installation and CODEX_CLI_PATH; this does not necessarily mean you are signed out.'}


class CodexAuth:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.closed = False
        self.state = {'status': 'unchecked', 'message': 'Check your Codex connection or sign in with ChatGPT.'}

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def start(self, action):
        if action not in ('check', 'login'):
            raise ValueError('Unknown Codex connection action')
        with self.lock:
            if self.closed or self.state['status'] in ('checking', 'signing_in'):
                raise RuntimeError('A Codex connection action is already in progress')
            self.state = {'status': 'checking' if action == 'check' else 'signing_in',
                          'message': 'Checking Codex…' if action == 'check' else 'Complete sign-in in your default browser. This expires after three minutes.'}
            threading.Thread(target=self._work, args=(action,), daemon=True).start()
            return dict(self.state)

    def _work(self, action):
        if action == 'check':
            state = login_status()
        else:
            executable = cli_path()
            if not executable:
                state = login_status()
            else:
                try:
                    with self.lock:
                        if self.closed:
                            return
                        # Codex owns OAuth, browser launch and credential storage.
                        self.process = subprocess.Popen(
                            [executable, '-c', 'forced_login_method="chatgpt"', 'login'],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env=child_environment(),
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                        process = self.process
                    code = process.wait(timeout=180)
                    state = login_status() if code == 0 else {
                        'status': 'error', 'message': f'Browser sign-in did not finish (exit {code}). Retry, or run codex login in Terminal and then Check connection.'}
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    state = {'status': 'error', 'message': 'Sign-in timed out. Retry, or run codex login in Terminal and then Check connection.'}
                except OSError:
                    state = {'status': 'error', 'message': 'Could not launch Codex. Check the CLI installation and CODEX_CLI_PATH.'}
        with self.lock:
            self.process = None
            self.state = state

    def close(self):
        with self.lock:
            self.closed = True
            if self.process and self.process.poll() is None:
                self.process.kill()
