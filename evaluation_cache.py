"""Content-addressed reuse of completed evaluations in the existing JSONL store."""
import hashlib
import json
from pathlib import Path

from config import (
    EVALUATION_CACHE_VERSION, OLLAMA_BASE_URL, OLLAMA_CHAT_MODEL, OLLAMA_EMBEDDING_MODEL,
)

SCORE_FIELDS = (
    "matched_keywords", "keyword_score", "embedding_score",
    "llm_score", "final_score", "relevant",
)
INPUT_FIELDS = (
    "title", "description", "organisation", "product_category", "location", "tender_value",
)


def evaluator_signature() -> str:
    root = Path(__file__).resolve().parent
    sources = (
        "capabilities.py", "prompts/relevance_examples.py", "filters/keyword_filter.py",
        "filters/embedding_filter.py", "filters/llm_filter.py", "filters/scoring.py",
        "nodes/tender_nodes.py", "graph.py",
    )
    payload = {
        "version": 1,
        "revision": EVALUATION_CACHE_VERSION,
        "models": [OLLAMA_BASE_URL, OLLAMA_CHAT_MODEL, OLLAMA_EMBEDDING_MODEL],
        "sources": {name: (root / name).read_text(encoding="utf-8") for name in sources},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def evaluation_key(tender, signature: str) -> str:
    # Preserve exact input strings: normalising whitespace/case could change model input.
    payload = {name: getattr(tender, name) for name in INPUT_FIELDS}
    payload["evaluator"] = signature
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def reuse_evaluation(tender, cached, key: str):
    for field in SCORE_FIELDS:
        value = getattr(cached, field)
        setattr(tender, field, list(value) if isinstance(value, list) else value)
    tender.evaluation_cache_key = key
    return tender
