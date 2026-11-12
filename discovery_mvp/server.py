"""Local dashboard and a small API for the search-first prototype."""

import argparse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import sqlite3
import threading
from urllib.parse import parse_qs, urlsplit

from discovery_mvp.engine import candidate_id
from discovery_mvp.codex_search import cli_path
from discovery_mvp.values import prepare_report
from discovery_mvp.storage import Store
from discovery_mvp.run import IST, ROOT, load_env, read_json, run_lock, write_json

STATIC = Path(__file__).parent / "web"


class Dashboard:
    def __init__(self):
        self.guard = threading.Lock()
        self.job = None
        self.token = secrets.token_urlsafe(32)
        self.stores = {}

    def store(self, mode):
        with self.guard:
            if mode not in self.stores:
                self.stores[mode] = Store(self.directory(mode))
            return self.stores[mode]

    def directory(self, mode):
        if mode not in ("demo", "live"):
            raise ValueError("Mode must be demo or live")
        return ROOT / "data" / ("discovery_mvp_demo" if mode == "demo" else "discovery_mvp")

    def snapshot(self, mode, run_id=None, scope='library'):
        if scope not in ('library', 'run'):
            raise ValueError('Scope must be library or run')
        directory = self.directory(mode)
        if run_id and not re.fullmatch(r"\d{8}-\d{6}-[a-f0-9]{6}", run_id):
            raise ValueError("Invalid run ID")
        store = self.store(mode)
        report = prepare_report(store.report(run_id))
        if not run_id and scope == 'library':
            report = store.library()
        history = store.history()
        state = store.state()
        with self.guard:
            job = dict(self.job) if self.job else None
            if job:
                job["logs"] = list(job["logs"])
        feedback = dict(state.get('feedback', {}))
        if report and report.get('scope') == 'library':
            for item in report['candidates']:
                reviews = [feedback[ident] for ident in item['record_ids'] if ident in feedback]
                if reviews:
                    feedback[item['id']] = max(reviews, key=lambda review: review.get('updated_at', ''))
        return {"report": report, "history": history, "feedback": feedback,
                "profile": read_json(ROOT / "discovery_mvp/profile.json", {}),
                "job": job, "token": self.token,
                "storage": {"backend": "sqlite", "path": str(store.path)},
                "configuration": {"openai_key": bool(os.getenv("OPENAI_API_KEY")),
                                  "codex_cli": bool(cli_path()),
                                  "ollama_key": bool(os.getenv("OLLAMA_API_KEY")),
                                  "ollama_model": os.getenv("OLLAMA_CHAT_MODEL", "qwen3.5:9b-q4_K_M"),
                                  "gpt_model": os.getenv("OPENAI_SEARCH_MODEL", "gpt-4.1-mini")}}

    def start(self, data):
        mode = data.get("mode", "demo")
        self.directory(mode)
        provider, ranker = data.get("provider", "codex"), data.get("ranker", "codex")
        budget = data.get("query_budget", 8)
        if provider not in ("codex", "gpt", "ollama", "both") or ranker not in ("codex", "gpt", "ollama"):
            raise ValueError("Invalid provider or ranker")
        if type(budget) is not int or not 1 <= budget <= 32:
            raise ValueError("Query budget must be 1..32")
        if mode == "live":
            if (provider == "codex" or ranker == "codex") and not cli_path():
                raise ValueError("Install Codex CLI or set CODEX_CLI_PATH, then run codex login")
            if (provider in ("gpt", "both") or ranker == "gpt") and not os.getenv("OPENAI_API_KEY"):
                raise ValueError("Add OPENAI_API_KEY to discovery_mvp/.env and restart the dashboard")
            if provider in ("ollama", "both") and not os.getenv("OLLAMA_API_KEY"):
                raise ValueError("Add OLLAMA_API_KEY to discovery_mvp/.env and restart the dashboard")
        command = [sys.executable, "-B", "-u", "-m", "discovery_mvp.run", "--provider", provider,
                   "--ranker", ranker, "--query-budget", str(budget)]
        if mode == "demo":
            command.append("--demo")
        with self.guard:
            if self.job and self.job["status"] == "running":
                raise RuntimeError("A discovery run is already in progress")
            process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                       errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.job = {"status": "running", "mode": mode, "started_at": datetime.now(IST).isoformat(),
                        "logs": [], "exit_code": None}
        threading.Thread(target=self.watch, args=(process,), daemon=True).start()
        return {"status": "running"}

    def watch(self, process):
        for line in process.stdout:
            with self.guard:
                self.job["logs"].append(line.rstrip())
                self.job["logs"] = self.job["logs"][-300:]
        code = process.wait()
        with self.guard:
            self.job.update(status="completed" if code == 0 else "partial" if code == 2 else "failed",
                            exit_code=code, finished_at=datetime.now(IST).isoformat())

    def feedback(self, data):
        directory = self.directory(data.get("mode", "demo"))
        label = data.get("label")
        if label not in ("relevant", "irrelevant"):
            raise ValueError("Label must be relevant or irrelevant")
        ident = candidate_id(data.get("url"))
        self.store(data.get('mode', 'demo')).feedback(ident, label, datetime.now(IST).isoformat())
        return {"status": "saved"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, status, data, content_type="application/json"):
        payload = json.dumps(data).encode() if content_type == "application/json" else data
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(payload)

    def trusted_host(self):
        return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

    def do_GET(self):
        if not self.trusted_host():
            return self.send(403, {"error": "Local dashboard only"})
        parts = urlsplit(self.path)
        try:
            if parts.path == "/api/state":
                query = parse_qs(parts.query)
                return self.send(200, self.server.dashboard.snapshot(
                    query.get("mode", ["demo"])[0], query.get("run", [None])[0],
                    query.get("scope", ['library'])[0]))
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/style.css": ("style.css", "text/css; charset=utf-8")}
            if parts.path not in files:
                return self.send(404, {"error": "Not found"})
            name, mime = files[parts.path]
            return self.send(200, (STATIC / name).read_bytes(), mime)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.send(400, {"error": str(exc)})

    def do_POST(self):
        origin = self.headers.get("Origin")
        allowed = (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}")
        if not self.trusted_host() or (origin and origin not in allowed):
            return self.send(403, {"error": "Origin rejected"})
        if self.headers.get("X-Dashboard-Token") != self.server.dashboard.token:
            return self.send(403, {"error": "Invalid session token"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16384:
                raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            if self.path == "/api/run":
                return self.send(202, self.server.dashboard.start(data))
            if self.path == "/api/feedback":
                return self.send(200, self.server.dashboard.feedback(data))
            self.send(404, {"error": "Not found"})
        except RuntimeError as exc:
            self.send(409, {"error": str(exc)})
        except (ValueError, OSError, TypeError, sqlite3.Error) as exc:
            self.send(400, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    load_env(ROOT / "discovery_mvp/.env")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    server.dashboard = Dashboard()
    print(f"Tender dashboard: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
