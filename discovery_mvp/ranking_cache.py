"""Persistent reuse of successful content-based tender assessments."""

from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path

from discovery_mvp.engine import RANK_PROMPT
from discovery_mvp.storage import Store

# Preserve existing content keys while changing only their storage backend.
LOGIC_FINGERPRINT = '4049ae33eb0aa62c5dba07792e0679638c09336a6c60930f77b55e65cc5e0206'

ASSESSMENT_FIELDS = (
    "score", "reason", "evidence", "status", "deadline", "deadline_status",
    "possibly_expired", "eligibility", "source_verified", "model_score", "model_reason", "rule_applied",
)


def ranking_signature(profile, model, ranker_name, demo=False):
    root = Path(__file__).parent
    payload = {
        "revision": os.getenv("DISCOVERY_RANK_CACHE_VERSION", "1"), "demo": demo,
        "ranker": ranker_name, "model": getattr(model, "model", ranker_name),
        "base": getattr(model, "base", ""),
        "capabilities": profile["groups"], "threshold": profile["shortlist_threshold"],
        "prompt": RANK_PROMPT,
        "implementation": {**{name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                              for name in ("engine.py", "providers.py")},
                           "ranking_cache.py": LOGIC_FINGERPRINT},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def assessment_key(item, signature):
    # Discovery query, provider, URL, ID and group order do not affect scope fit.
    # Preserve exact text so changed summaries/requirements invalidate the cache.
    content = {"signature": signature, "title": item["title"],
               "snippets": sorted(set(item.get("snippets", [])))}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def valid_assessment(item):
    score = item.get("score")
    evidence = item.get("evidence")
    return (type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1
            and item.get("status") in ("shortlist", "review", "rejected")
            and isinstance(item.get("reason"), str)
            and isinstance(evidence, str) and bool(evidence.strip())
            and any(evidence in text for text in [item["title"], *item.get("snippets", [])]))


class RankingCache:
    def __init__(self, path, signature, store=None):
        self.store = store or Store(Path(path).parent)
        self.path, self.signature = self.store.path, signature
        self.stats = {"hits": 0, "misses": 0, "writes": 0}
        self.warning = next((issue for issue in self.store.migration_issues() if 'ranking_cache' in issue), None)

    @property
    def entries(self):
        return self.store.cache_entries()

    def get(self, item, today):
        key = assessment_key(item, self.signature)
        entry = self.store.cache_get(key)
        if isinstance(entry, dict) and isinstance(entry.get("assessment"), dict):
            assessment = entry["assessment"]
            merged = {**item, **{name: assessment[name] for name in ASSESSMENT_FIELDS if name in assessment}}
            if valid_assessment(merged):
                try:
                    deadline = date.fromisoformat(merged["deadline"]) if merged.get("deadline") else None
                except (TypeError, ValueError):
                    deadline = None
                merged["possibly_expired"] = bool(deadline and deadline < today)
                if merged["possibly_expired"]:
                    merged["status"] = "review"
                merged["ranking_cache"] = {"hit": True, "key": key, "ranked_at": entry.get("ranked_at")}
                self.stats["hits"] += 1
                return merged
        self.stats["misses"] += 1
        return None

    def put(self, item, ranked_at=None, origin="model"):
        if not valid_assessment(item):
            return False
        key = assessment_key(item, self.signature)
        entry = {
            "assessment": {name: item[name] for name in ASSESSMENT_FIELDS if name in item},
            "ranked_at": ranked_at or datetime.now(timezone.utc).isoformat(), "origin": origin,
        }
        self.store.cache_put(key, entry, self.signature)
        self.stats["writes"] += 1
        item["ranking_cache"] = {"hit": False, "key": key, "ranked_at": entry["ranked_at"]}
        return True

