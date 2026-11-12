"""Run once; use an operating-system scheduler for periodic execution."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import sqlite3
import time
import uuid

from discovery_mvp.engine import (
    PLANNER_PROMPT, build_queries, candidate_id, mark_changes,
    merge_results, rank_candidate, render_report,
)
from discovery_mvp.providers import Ollama, OpenAI
from discovery_mvp.codex_search import CodexSearch
from discovery_mvp.codex_ranker import CodexRanker
from discovery_mvp.values import prepare_report
from discovery_mvp.ranking_cache import RankingCache, ranking_signature
from discovery_mvp.storage import Store

ROOT = Path(__file__).resolve().parent.parent
IST = timezone(timedelta(hours=5, minutes=30))


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if not sep or not name.strip().replace("_", "").isalnum():
            raise ValueError("Invalid environment file line")
        os.environ.setdefault(name.strip(), value.strip().strip("\"'"))


def read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


@contextmanager
def run_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    # OS lock is released on exit/crash; the persistent file is not a stale lock.
    with (directory / "run.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("Another discovery or feedback run already holds the lock") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def execute(args, output):
    processing_started = time.perf_counter()
    now = datetime.now(IST)
    today = now.date()
    profile = read_json(args.profile, None)
    if not profile or not profile.get("groups"):
        raise ValueError("Profile must contain capability groups")
    store = Store(output)
    state = store.state()
    if args.feedback:
        url, label = args.feedback
        ident = candidate_id(url)
        seen = state.get("seen", {}).get(ident)
        if not seen:
            raise ValueError("Feedback URL must already exist in this output directory's results")
        store.feedback(ident, label, now.isoformat())
        print("Feedback saved; future query allocation will use it.")
        return 0

    issues, providers = [], {}
    if args.demo:
        from discovery_mvp.demo import Demo
        providers = {"demo-gpt": Demo(), "demo-ollama": Demo()}
        ranker = Demo()
    else:
        for name in (["gpt", "ollama"] if args.provider == "both" else [args.provider]):
            try:
                if name == "ollama" and not os.getenv("OLLAMA_API_KEY"):
                    raise ValueError("Set OLLAMA_API_KEY for Ollama web search")
                providers[name] = {"gpt": OpenAI, "ollama": Ollama, "codex": CodexSearch}[name]()
            except ValueError as exc:
                issues.append(f"{name}: {exc}")
        try:
            ranker = {"codex": CodexRanker, "gpt": OpenAI, "ollama": Ollama}[args.ranker]()
        except ValueError as exc:
            issues.append(f"ranker: {exc}")
            ranker = None

    planning_started = time.perf_counter()
    expansions = {}
    if ranker and providers:
        print("[PLAN] Expanding capability keywords", flush=True)
        try:
            expansions = ranker.complete(PLANNER_PROMPT, profile).get("groups", {})
            if not isinstance(expansions, dict):
                raise ValueError("Invalid keyword expansion")
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            issues.append(f"Keyword expansion failed ({exc}); using curated seeds")
            expansions = {}
    queries = build_queries(profile, args.query_budget, state, expansions)
    planning_seconds = time.perf_counter() - planning_started
    print(f"[PLAN] Ready in {planning_seconds:.2f}s; {len(queries)} queries", flush=True)
    search_started = time.perf_counter()
    batches, search_log = [], []
    for query in queries:
        group_succeeded = False
        for name, provider in providers.items():
            print(f"[{name}] {query['query']}", flush=True)
            try:
                results = provider.search(query["query"], today.isoformat(), args.results_per_query)
                batches.append((name, query, results))
                search_log.append({"provider": name, "group": query["group"],
                                   "status": "ok", "result_count": len(results)})
                group_succeeded = True
            except (RuntimeError, ValueError, KeyError, TypeError) as exc:
                issues.append(f"{name}/{query['group']}: {exc}")
                search_log.append({"provider": name, "group": query["group"], "status": "failed"})
        if group_succeeded:
            attempts = state.setdefault("group_attempts", {})
            attempts[query["group"]] = attempts.get(query["group"], 0) + 1

    search_seconds = time.perf_counter() - search_started
    print(f"[SEARCH] Finished in {search_seconds:.2f}s", flush=True)
    ranking_started = time.perf_counter()
    cache = RankingCache(output / "ranking_cache.json", ranking_signature(profile, ranker, args.ranker, args.demo), store=store)
    if cache.warning:
        issues.append(cache.warning)
    items = merge_results(batches, issues)
    for index, item in enumerate(items):
        try:
            cached = cache.get(item, today)
            if cached is not None:
                items[index] = cached
                print(f"[cache] {index + 1}/{len(items)} Reused: {item['title']}", flush=True)
                continue
            if not ranker:
                raise ValueError("No ranker available")
            print(f"[rank] {index + 1}/{len(items)} {item['title']}", flush=True)
            items[index] = rank_candidate(item, profile, ranker, today)
            try:
                cache.put(items[index])
            except (OSError, sqlite3.Error):
                issues.append("Could not persist a ranking cache entry; its assessment remains in the report")
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            issues.append(f"Ranking {item['id']}: {exc}")
            items[index] = {**item, "score": 0, "status": "review",
                            "reason": "Ranking unavailable; manual review required",
                            "evidence": "Not evaluated", "deadline": None,
                            "deadline_status": "unknown", "eligibility": "not assessed",
                            "source_verified": False}
    ranking_seconds = time.perf_counter() - ranking_started
    print(f"[CACHE] {cache.stats['hits']} reused; {cache.stats['misses']} fresh ranking attempts", flush=True)
    mark_changes(items, state, now.isoformat())
    items.sort(key=lambda item: (-item["score"], item["title"]))
    successful = sum(entry["status"] == "ok" for entry in search_log)
    expected = len(queries) * (2 if args.demo or args.provider == "both" else 1)
    report = {
        "created_at": now.isoformat(), "mode": "demo (synthetic)" if args.demo else "live",
        "health": "failed" if not successful else "partial" if issues else "ok",
        "attempted_searches": expected, "successful_searches": successful,
        "queries": queries, "search_log": search_log, "issues": issues, "candidates": items,
        "ranking_cache": {**cache.stats, "signature": cache.signature},
        "settings": {"provider": args.provider, "ranker": args.ranker,
                     "ranker_model": getattr(ranker, "model", args.ranker),
                     "query_budget": args.query_budget},
        "timing": {"planning_seconds": round(planning_seconds, 2),
                   "search_seconds": round(search_seconds, 2),
                   "ranking_seconds": round(ranking_seconds, 2),
                   "processing_seconds": round(time.perf_counter() - processing_started, 2)},
        "openai_usage": [usage for provider in [*providers.values(), ranker]
                         for usage in getattr(provider, "usage", [])],
        "codex_search_calls": [call for provider in providers.values()
                               for call in getattr(provider, "calls", [])],
        "codex_rank_calls": getattr(ranker, "calls", []) if args.ranker == "codex" else [],
    }
    report = prepare_report(report)
    run_id = now.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    library = store.save_run(run_id, report, state)
    runs = output / "runs"
    runs.mkdir(exist_ok=True)
    write_json(runs / (run_id + ".json"), report)
    write_json(output / "latest.json", report)
    page = render_report(report)
    (runs / (run_id + ".html")).write_text(page, encoding="utf-8")
    temp_html = output / "latest.html.tmp"
    temp_html.write_text(page, encoding="utf-8")
    temp_html.replace(output / "latest.html")
    print(f"[LIBRARY] {len(library['candidates'])} retained opportunities in SQLite", flush=True)
    print(f"{report['health'].upper()}: {len(items)} candidates. Report: {output / 'latest.html'}")
    for issue in issues:
        print("ISSUE: " + issue, file=sys.stderr)
    # Partial failures are nonzero so a scheduler cannot mistake them for success.
    return 0 if report["health"] == "ok" else 2 if successful else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["codex", "gpt", "ollama", "both"], default="codex")
    parser.add_argument("--ranker", choices=["codex", "gpt", "ollama"], default="codex")
    parser.add_argument("--demo", action="store_true", help="Synthetic offline presentation")
    parser.add_argument("--profile", type=Path, default=ROOT / "discovery_mvp/profile.json")
    parser.add_argument("--env-file", type=Path, default=ROOT / "discovery_mvp/.env")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--query-budget", type=int, default=6, help="Distinct queries per run (1..32)")
    parser.add_argument("--results-per-query", type=int, default=5, help="Results per provider/query (1..10)")
    parser.add_argument("--feedback", nargs=2, metavar=("URL", "LABEL"), help="Label relevant or irrelevant")
    args = parser.parse_args()
    if not 1 <= args.query_budget <= 32 or not 1 <= args.results_per_query <= 10:
        parser.error("query-budget must be 1..32 and results-per-query 1..10")
    if args.feedback and args.feedback[1] not in ("relevant", "irrelevant"):
        parser.error("feedback LABEL must be relevant or irrelevant")
    output = (args.output or ROOT / "data" / ("discovery_mvp_demo" if args.demo else "discovery_mvp")).resolve()
    try:
        load_env(args.env_file)
        with run_lock(output):
            return execute(args, output)
    except (RuntimeError, ValueError, OSError, sqlite3.Error) as exc:
        print(f"Discovery failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
